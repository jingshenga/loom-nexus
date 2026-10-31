"""History backfill: pull a bound source's recent messages (text AND
media) into the pipeline, or run a media-only pass that attaches
photos/videos to already-stored messages.

Media policy: photos always; videos only up to
NEXUS_BRIDGE_MEDIA_MAX_MB (default 50) to protect NAS disk.
After a media pass, cards still missing a price are requeued so the
OCR pass can read it off the new images.
"""
import asyncio
import json
import logging
import os

import asyncpg

from runner import hub, sqlres

logger = logging.getLogger("nexus-bridge.backfill")

SQL_CLAIM = sqlres.load("backfill_claim.sql")
SQL_FINISH = sqlres.load("backfill_finish.sql")
SQL_APPEND = sqlres.load("append_sample.sql")
SQL_SESSION_OF = sqlres.load("session_file_of.sql")
SQL_SET_MEDIA = sqlres.load("set_media.sql")
SQL_RESET_PRICE = sqlres.load("reset_missing_price.sql")
SQL_RAW_IDS = sqlres.load("raw_ids_for_source.sql")

POLL_S = float(os.getenv("NEXUS_BRIDGE_BACKFILL_INTERVAL", "3"))
MEDIA_DIR = os.getenv("NEXUS_MEDIA_DIR", "/app/data/media")
MAX_MEDIA_BYTES = int(os.getenv("NEXUS_BRIDGE_MEDIA_MAX_MB", "50")) * 1024 * 1024


async def save_media(
    msg, source_id: int, stable_prefix: str | None = None,
    force_kind: str | None = None,
) -> list:
    """Download photo/video for one message; returns media_json items."""
    try:
        is_photo = bool(msg.photo)
        is_video = bool(msg.video or msg.video_note)
        if force_kind == "photo":
            is_photo, is_video = True, False
        elif force_kind == "video":
            is_photo, is_video = False, True
        if not (is_photo or is_video):
            return []
        if not is_photo:
            size = getattr(msg.file, "size", 0) or 0
            if size > MAX_MEDIA_BYTES:
                logger.info(
                    "skip video msg %s (%.0f MB over cap)",
                    msg.id, size / 1048576,
                )
                return []
        target_dir = os.path.join(MEDIA_DIR, f"src_{source_id}")
        os.makedirs(target_dir, exist_ok=True)
        target = target_dir
        if stable_prefix:
            media_obj = (
                (msg.video or msg.video_note or msg.document)
                if force_kind == "video"
                else (msg.photo or msg.video or msg.video_note or msg.document)
            )
            media_id = getattr(media_obj, "id", None) or msg.id
            ext = getattr(msg.file, "ext", None) or (".mp4" if is_video else ".jpg")
            target = os.path.join(target_dir, f"{stable_prefix}_{media_id}{ext}")
        saved = await msg.download_media(file=target)
        if not saved:
            return []
        rel = os.path.relpath(str(saved), MEDIA_DIR)
        from runner import media_meta

        # face focus + score (photos), poster frame (videos) — inline,
        # best-effort; ordering happens inside enrich_entries
        return media_meta.enrich_entries(
            [{"kind": "photo" if is_photo else "video", "path": rel}]
        )
    except Exception as exc:
        logger.warning("media download failed (msg %s): %s", msg.id, exc)
        return []


MEDIA_ORDER = {"photo": 0, "video": 1, "file": 2}


def order_media(media: list) -> list:
    """Public face photos first, then private info cards, videos and files."""
    def sort_key(item):
        if isinstance(item.get("manual_order"), int):
            return (0, item["manual_order"], 0, 0)
        visibility = {"public": 0, "pending": 1, "admin": 2}.get(
            item.get("visibility"), 1,
        )
        try:
            score = float(item.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        return (
            1, MEDIA_ORDER.get(item.get("kind"), 3), visibility,
            -score if item.get("kind") == "photo" else 0,
        )

    return sorted(
        media or [], key=sort_key,
    )


async def _fetch_history(client, peer_id: int, limit: int, source_id: int):
    """[(msg_id, sender, grouped, text, date, media_json)] oldest first.
    Album parts sharing a grouped_id are MERGED into one row anchored
    on the caption-bearing part, carrying all photos + videos."""
    entity = await client.get_entity(peer_id)
    messages = list(reversed((await client.get_messages(entity, limit=limit)) or []))
    rows = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        gid = getattr(msg, "grouped_id", None)
        if not gid:
            media = await save_media(msg, source_id)
            text = (msg.raw_text or "").strip()
            if text or media:
                rows.append((msg.id, msg.sender_id, None, text, msg.date, order_media(media)))
            i += 1
            continue
        group = []
        while i < len(messages) and getattr(messages[i], "grouped_id", None) == gid:
            group.append(messages[i])
            i += 1
        anchor = next((m for m in group if (m.raw_text or "").strip()), group[0])
        media = []
        for part in group:
            media.extend(await save_media(part, source_id))
        text = (anchor.raw_text or "").strip()
        if text or media:
            rows.append(
                (anchor.id, anchor.sender_id, gid, text, anchor.date, order_media(media)),
            )
    return rows


async def _backfill_history(pool, job, client) -> int:
    history = await _fetch_history(client, job["peer_id"], job["msg_limit"], job["source_id"])
    inserted = 0
    for msg_id, sender, grouped, text, date, media in history:
        rows = await pool.fetch(
            SQL_APPEND, job["source_id"], msg_id, sender, grouped,
            text, date, json.dumps(media),
        )
        if rows[0]["raw_id"] is not None:
            inserted += 1
    return inserted


async def _backfill_media(pool, job, client) -> int:
    """Attach media to already-stored messages of the source."""
    stored = await pool.fetch(SQL_RAW_IDS, job["source_id"], job["msg_limit"])
    if not stored:
        return 0
    entity = await client.get_entity(job["peer_id"])
    ids = [r["platform_msg_id"] for r in stored]
    messages = await client.get_messages(entity, ids=ids)
    updated = 0
    for row, msg in zip(stored, messages):
        if msg is None:
            continue
        media = await save_media(msg, job["source_id"])
        if not media:
            continue
        result = await pool.fetch(SQL_SET_MEDIA, row["id"], json.dumps(media))
        if result[0]["updated"]:
            updated += 1
    requeued = await pool.fetch(SQL_RESET_PRICE, job["source_id"])
    logger.info(
        "media pass: %d message(s) gained media, %d requeued for OCR (source=%s)",
        updated, requeued[0]["requeued"], job["source_id"],
    )
    return updated


async def run_backfill(pool: asyncpg.Pool, stop: asyncio.Event) -> None:
    logger.info("backfill executor started")
    while not stop.is_set():
        try:
            jobs = await pool.fetch(SQL_CLAIM)
            for job in jobs:
                session_rows = await pool.fetch(SQL_SESSION_OF, job["account_id"])
                if not session_rows:
                    await pool.fetch(SQL_FINISH, job["job_id"], False, 0, "account missing")
                    continue
                session_name = str(session_rows[0]["session_file"]).removesuffix(".session")
                client = await hub.get_client(session_name)
                if client is None:
                    await pool.fetch(SQL_FINISH, job["job_id"], False, 0, "account unauthorized")
                    continue
                worker = _backfill_media if job["job_mode"] == "media" else _backfill_history
                count = 0
                try:
                    count = await worker(pool, job, client)
                    await pool.fetch(SQL_FINISH, job["job_id"], True, count, None)
                    logger.info(
                        "backfill job %s (%s) done: %d item(s) source=%s",
                        job["job_id"], job["job_mode"], count, job["source_id"],
                    )
                except Exception as exc:
                    await pool.fetch(SQL_FINISH, job["job_id"], False, count, str(exc))
                    logger.warning("backfill job %s failed: %s", job["job_id"], exc)
        except Exception:
            logger.exception("backfill cycle failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_S)
        except TimeoutError:
            pass
