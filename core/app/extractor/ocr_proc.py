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
    result = engine(image_path)
    if result is None:
        return []
    texts = getattr(result, "txts", None)
    if texts:
        return [t for t in texts if t and t.strip()]
    if isinstance(result, (list, tuple)):
        out = []
        for item in result:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                text = item[1]
                if isinstance(text, str) and text.strip():
                    out.append(text)
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
            texts = _extract(engine, req["image"])
            sys.stdout.write(json.dumps({"ok": True, "texts": texts}) + "\n")
        except Exception as exc:  # noqa: BLE001 - report any failure as a line
            sys.stdout.write(json.dumps({"ok": False, "error": str(exc)}) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
