"""Single-worker OCR gateway with an asyncio semaphore of 1.

The RapidOCR engine runs in its own subprocess (python -m
app.extractor.ocr_proc) so the heavy onnxruntime heap never touches the
API process. The worker is started lazily on first use and restarted
automatically if it dies.
"""
import asyncio
import json
import logging
import sys

logger = logging.getLogger("nexus-core.ocr")

WORKER_MODULE = "app.extractor.ocr_proc"


class OcrWorker:
    def __init__(self, timeout_s: float = 60.0):
        self._sem = asyncio.Semaphore(1)
        self._proc = None
        self._timeout = timeout_s

    async def _ensure(self):
        if self._proc is None or self._proc.returncode is not None:
            self._proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", WORKER_MODULE,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            logger.info("ocr worker subprocess started (pid=%s)", self._proc.pid)
        return self._proc

    async def read_texts(self, image_paths) -> list:
        """OCR several images sequentially; returns merged text lines."""
        texts = []
        for path in image_paths:
            result = await self.read_document(path)
            texts.extend(result.get("texts", []))
        return texts

    async def read_document(self, image_path: str) -> dict:
        """Return OCR text and per-line geometry for local card classification."""
        async with self._sem:
            proc = await self._ensure()
            try:
                req = json.dumps({"image": image_path}) + "\n"
                proc.stdin.write(req.encode())
                await proc.stdin.drain()
                raw = await asyncio.wait_for(proc.stdout.readline(), self._timeout)
            except Exception as exc:
                logger.warning("ocr worker call failed (%s); restarting", exc)
                await self._kill()
                return {"ok": False, "texts": [], "lines": []}
            if not raw:
                await self._kill()
                return {"ok": False, "texts": [], "lines": []}
            try:
                resp = json.loads(raw)
            except json.JSONDecodeError:
                return {"ok": False, "texts": [], "lines": []}
            if not resp.get("ok"):
                logger.warning("ocr rejected %s: %s", image_path, resp.get("error"))
                return {"ok": False, "texts": [], "lines": [], "error": resp.get("error")}
            texts = resp.get("texts") or []
            lines = resp.get("lines") or [{"text": text} for text in texts]
            return {"ok": True, "texts": texts, "lines": lines}

    async def _kill(self):
        if self._proc is not None:
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass
            self._proc = None

    async def close(self):
        if self._proc is not None:
            try:
                self._proc.stdin.close()
                await asyncio.wait_for(self._proc.wait(), 5)
            except Exception:
                await self._kill()
            self._proc = None
            logger.info("ocr worker subprocess stopped")
