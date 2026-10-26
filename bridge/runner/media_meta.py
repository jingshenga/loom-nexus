"""Per-media enrichment: face-aware focus points, quality scores and
video poster frames.

Photos get a YuNet face detection pass (OpenCV); the best face yields
a focus point for CSS object-position and a 0..1 "score" (detection
confidence + face size — a bigger face means a closer, more flattering
shot). Videos get a poster frame extracted with ffmpeg at ~1s.

The gallery list is reordered: photos by score (best face first),
videos last — so the card cover is automatically the strongest photo.
Runs inline at download time and from the hourly backfill for existing
files. Every step degrades gracefully: no model, no ffmpeg or a bad
file simply leaves the entry untouched.
"""
import json
import logging
import os
import subprocess

from runner import sqlres

logger = logging.getLogger("nexus-bridge.media-meta")

MEDIA_ROOT = os.getenv("NEXUS_MEDIA_DIR", "/app/data/media")
MODEL_PATH = "/app/models/face_detection_yunet_2023mar.onnx"

SQL_PENDING = sqlres.load("media_pending.sql")
SQL_STORE = sqlres.load("media_enrich.sql")

_detector = None
_detector_failed = False


def _abs(rel: str) -> str:
    return os.path.join(MEDIA_ROOT, rel)


def _get_detector():
    global _detector, _detector_failed
    if _detector is not None:
        return _detector
    if _detector_failed:
        return None
    try:
        import cv2

        _detector = cv2.FaceDetectorYN_create(MODEL_PATH, "", (320, 320), 0.6)
        return _detector
    except Exception as exc:
        _detector_failed = True
        logger.warning("face detector unavailable (enrichment degrades): %s", exc)
        return None


def detect_focus(rel: str):
    """-> {"focus_x", "focus_y", "score"} for one photo, or None."""
    detector = _get_detector()
    if detector is None:
        return None
    try:
        import cv2

        img = cv2.imread(_abs(rel))
        if img is None:
            return None
        h, w = img.shape[:2]
        if w == 0 or h == 0:
            return None
        detector.setInputSize((w, h))
        faces = detector.detect(img)[1]
        if faces is None or len(faces) == 0:
            # no face: neutral focus with upper-third bias, lowest score
            return {"focus_x": 0.5, "focus_y": 0.3, "score": 0.0}
        # row layout: x, y, w, h, 10 landmarks, score (index 14)
        best = max(faces, key=lambda f: float(f[14]))
        fx, fy, fw, fh = (float(best[0]), float(best[1]),
                          float(best[2]), float(best[3]))
        conf = float(best[14])
        cx = (fx + fw / 2.0) / w
        cy = (fy + fh * 0.42) / h  # slightly above face centre: the eyes
        area_ratio = (fw * fh) / float(w * h)
        score = min(1.0, conf * 0.5 + min(area_ratio * 4.0, 1.0) * 0.5)
        return {
            "focus_x": round(min(max(cx, 0.0), 1.0), 3),
            "focus_y": round(min(max(cy, 0.0), 1.0), 3),
            "score": round(score, 3),
        }
    except Exception as exc:
        logger.warning("face detect failed (%s): %s", rel, exc)
        return None


def make_poster(rel: str):
    """Extract one frame at ~1s via ffmpeg; returns the poster rel path."""
    src = _abs(rel)
    out = os.path.splitext(src)[0] + "_poster.jpg"
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return os.path.relpath(out, MEDIA_ROOT)
    for seek in ("1", "0"):
        try:
            run = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-ss", seek,
                 "-i", src, "-frames:v", "1", "-q:v", "3", out],
                capture_output=True, timeout=60,
            )
            if run.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0:
                return os.path.relpath(out, MEDIA_ROOT)
        except Exception as exc:
            logger.warning("poster extraction failed (%s): %s", rel, exc)
    try:
        if os.path.exists(out):
            os.remove(out)
    except OSError:
        pass
    return None


def enrich_entries(media: list) -> list:
    """Photos: focus + score, sorted best-face first. Videos: poster.
    Order of the returned list becomes the display order."""
    entries = [dict(m) for m in (media or [])]
    photos = [m for m in entries if m.get("kind") == "photo"]
    videos = [m for m in entries if m.get("kind") == "video"]
    others = [m for m in entries if m.get("kind") not in ("photo", "video")]

    for m in photos:
        if "score" in m:
            continue
        info = detect_focus(m.get("path", ""))
        if info:
            m.update(info)
        else:
            m["score"] = 0.0
    photos.sort(key=lambda m: float(m.get("score", 0)), reverse=True)

    for m in videos:
        if not m.get("poster") and m.get("path"):
            poster = make_poster(m["path"])
            if poster:
                m["poster"] = poster
    return photos + videos + others


def _decode(raw):
    if isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return []


async def run_enrich(pool, stop) -> None:
    """Backfill: enrich stored media rows that lack scores or posters."""
    import asyncio

    logger.info("media enrichment loop started (every 300s)")
    while not stop.is_set():
        try:
            rows = await pool.fetch(SQL_PENDING)
            for row in rows:
                entries = enrich_entries(_decode(row["media_json"]))
                await pool.fetch(SQL_STORE, row["id"], json.dumps(entries, ensure_ascii=False))
            if rows:
                logger.info("enriched %d media row(s)", len(rows))
        except Exception:
            logger.exception("enrichment cycle failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=300)
        except TimeoutError:
            pass
