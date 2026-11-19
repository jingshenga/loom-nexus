"""nexus-core entrypoint: FastAPI app with health API and static hosting."""
import asyncio
import os
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import db
from app.api import admin_bots, admin_chat, admin_dashboard, accounts, auth, chat, chat_media, gateway, health, listings, models, ocr_classifier, ocr_review, probe, review, review_meta, rules, settings, sources, users
from app.config import get_settings

logger = logging.getLogger("nexus-core")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    background_tasks = []
    ocr_worker = None
    stop = asyncio.Event()
    try:
        await db.init_pool()
    except Exception as exc:
        logger.warning("database pool unavailable at startup: %s", exc)
    else:
        await auth.ensure_admin_account()
        from app.extractor.media_ocr import MediaOcrProcessor
        from app.extractor.ocr_worker import OcrWorker
        from app.extractor.pipeline import Extractor
        from app.extractor import review_automation

        vision4_model_dir = settings.vision4_model_dir
        try:
            selected_model = await db.get_pool().fetchval(
                "SELECT model_path FROM vision4_model_versions WHERE status='active' "
                "ORDER BY activated_at DESC,id DESC LIMIT 1"
            )
            if selected_model and Path(selected_model).is_dir():
                vision4_model_dir = selected_model
        except Exception:
            logger.info("Vision4 model registry unavailable; using configured model directory")

        ocr_worker = OcrWorker()
        media_ocr_processor = MediaOcrProcessor(
            db.get_pool(), settings.media_dir, ocr_worker,
            settings.vision4_mode, vision4_model_dir,
        )
        app.state.media_ocr_processor = media_ocr_processor
        await media_ocr_processor.initialize_vision4()
        extractor = Extractor(
            db.get_pool(), settings.media_dir, settings.extract_ocr_enabled,
            ocr_worker, media_ocr_processor,
            shadow_sample_rate=settings.shadow_sample_rate,
            shadow_trace_max_bytes=settings.shadow_trace_max_bytes,
            shadow_error_log_interval_s=settings.shadow_error_log_interval_s,
        )
        background_tasks.append(asyncio.create_task(
            extractor.run(stop, settings.extract_batch, settings.extract_interval_s),
            name="extractor",
        ))
        background_tasks.append(asyncio.create_task(
            media_ocr_processor.run(stop),
            name="media-card-ocr",
        ))
        background_tasks.append(asyncio.create_task(
            review_automation.run(db.get_pool(), settings.media_dir, stop),
            name="review-automation",
        ))
    yield
    stop.set()
    for task in background_tasks:
        task.cancel()
    for task in background_tasks:
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    if ocr_worker is not None:
        await ocr_worker.close()
    await db.close_pool()


app = FastAPI(title="nexus-core", version="0.1.0", lifespan=lifespan)
app.include_router(health.router)
app.include_router(admin_dashboard.router)
app.include_router(auth.router)
app.include_router(admin_bots.router)
app.include_router(admin_chat.router)
app.include_router(listings.router)
app.include_router(models.router)
app.include_router(review.router)
app.include_router(review_meta.router)
app.include_router(ocr_review.router)
app.include_router(ocr_classifier.router)
app.include_router(gateway.router)
app.include_router(chat.router)
app.include_router(chat_media.router)
app.include_router(rules.router)
app.include_router(probe.router)
app.include_router(sources.router)
app.include_router(settings.router)
app.include_router(accounts.router)
app.include_router(users.router)


from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from starlette.exceptions import HTTPException as StarletteHTTPException  # noqa: E402


@app.exception_handler(StarletteHTTPException)
async def spa_fallback(request, exc):
    """Serve the SPA shell for client-side routes; real 404s stay JSON."""
    path = request.url.path
    if exc.status_code == 404 and not path.startswith(("/api", "/media")):
        return FileResponse(STATIC_DIR / "index.html")
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

# media files (bind-mounted data/media) under /media, gated by the
# login token (?t=...) since <img> tags cannot send headers
MEDIA_DIR = Path(get_settings().media_dir)
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
THUMB_DIR = MEDIA_DIR / ".thumbnails"
_thumb_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="media-thumb")


def _make_thumbnail(source: Path) -> Path:
    from PIL import Image, ImageOps

    key = hashlib.sha256(str(source.relative_to(MEDIA_DIR.resolve())).encode()).hexdigest()
    target = THUMB_DIR / key[:2] / f"{key}.webp"
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{key}.{threading.get_ident()}.tmp")
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original)
        image.thumbnail((480, 600), Image.Resampling.LANCZOS)
        if image.mode not in {"RGB", "RGBA"}:
            image = image.convert("RGB")
        image.save(temporary, "WEBP", quality=80, method=4)
    temporary.replace(target)
    return target


@app.get("/media/{file_path:path}")
async def media_file(file_path: str, t: str = "", thumb: bool = False):
    from fastapi.responses import FileResponse
    from fastapi import HTTPException as _HTTPException

    identity = auth.verify_token(t)
    if identity is None:
        raise _HTTPException(status_code=401, detail="login required")
    if not identity[1]:
        pool = db.get_pool()
        if pool is None:
            raise _HTTPException(status_code=503, detail="database unavailable")
        restricted = await pool.fetchval(
            "SELECT EXISTS ("
            " SELECT 1 FROM raw_messages rm "
            " CROSS JOIN LATERAL jsonb_array_elements(rm.media_json) item "
            " WHERE item->>'kind'='photo' AND item->>'path'=$1 "
            "   AND item->>'visibility' IS DISTINCT FROM 'public'"
            ") OR EXISTS ("
            " SELECT 1 FROM listings l "
            " CROSS JOIN LATERAL jsonb_array_elements(l.media_json) item "
            " WHERE item->>'kind'='photo' AND item->>'path'=$1 "
            "   AND item->>'visibility' IS DISTINCT FROM 'public'"
            ")",
            file_path,
        )
        if restricted:
            raise _HTTPException(status_code=403, detail="admin-only media")
    target = (MEDIA_DIR / file_path).resolve()
    if not str(target).startswith(str(MEDIA_DIR.resolve()) + os.sep) or ".thumbnails" in Path(file_path).parts:
        raise _HTTPException(status_code=404, detail="not found")
    if not target.is_file():
        raise _HTTPException(status_code=404, detail="not found")
    if thumb:
        try:
            target = await asyncio.get_running_loop().run_in_executor(_thumb_pool, _make_thumbnail, target)
        except Exception as exc:
            raise _HTTPException(status_code=415, detail="thumbnail unavailable") from exc
    return FileResponse(target, headers={"Cache-Control": "private, max-age=86400"})

# Static SPA hosting (built by the frontend stage into /app/static).
STATIC_DIR = Path(get_settings().static_dir)
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
