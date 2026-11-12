"""RapidOCR isolated subprocess entrypoint (concurrency 1 by design).

Protocol: one JSON object per stdin line {"image": "<abs path>"}; one
JSON object per stdout line {"ok": true, "texts": [...]} or
{"ok": false, "error": "..."}. EOF on stdin exits cleanly.

Run as: python -m app.extractor.ocr_proc
"""
import json
import sys


def _build_engine():
    from rapidocr import RapidOCR

    return RapidOCR()


def _extract(engine, image_path: str):
    return [line["text"] for line in _extract_lines(engine, image_path)]


def _extract_lines(engine, image_path: str):
    result = engine(image_path)
    if result is None:
        return []
    texts = getattr(result, "txts", None)
    if texts:
        boxes = getattr(result, "boxes", None)
        scores = getattr(result, "scores", None)
        boxes = [] if boxes is None else boxes
        scores = [] if scores is None else scores
        out = []
        for i, text in enumerate(texts):
            if not text or not text.strip():
                continue
            box = boxes[i].tolist() if i < len(boxes) and hasattr(boxes[i], "tolist") else (boxes[i] if i < len(boxes) else None)
            score = float(scores[i]) if i < len(scores) else None
            out.append({"text": text.strip(), "box": box, "score": score})
        return out
    if isinstance(result, (list, tuple)):
        out = []
        for item in result:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                box, text = item[0], item[1]
                if isinstance(text, str) and text.strip():
                    score = float(item[2]) if len(item) > 2 else None
                    out.append({"text": text.strip(), "box": box, "score": score})
        return out
    return []


def main() -> int:
    engine = _build_engine()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            lines = _extract_lines(engine, req["image"])
            texts = [line["text"] for line in lines]
            sys.stdout.write(json.dumps({"ok": True, "texts": texts, "lines": lines}) + "\n")
        except Exception as exc:  # noqa: BLE001 - report any failure as a line
            sys.stdout.write(json.dumps({"ok": False, "error": str(exc)}) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
