"""nexus-core entrypoint: FastAPI app with health API and static hosting."""
import asyncio
import os
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import db
from app.api import accounts, auth, chat, chat_media, gateway, health, listings, probe, review, rules, settings, sources, users
from app.config import get_settings

logger = logging.getLogger("nexus-core")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    extractor_task = None
    stop = asyncio.Event()
    try:
        await db.init_pool()
    except Exception as exc:
        logger.warning("database pool unavailable at startup: %s", exc)
    else:
        await auth.ensure_admin_account()
        from app.extractor.pipeline import Extractor

        extractor_task = asyncio.create_task(
            Extractor(
                db.get_pool(), settings.media_dir, settings.extract_ocr_enabled,
            ).run(stop, settings.extract_batch, settings.extract_interval_s),
            name="extractor",
        )
    yield
    stop.set()
    if extractor_task is not None:
        extractor_task.cancel()
        try:
            await extractor_task
        except (asyncio.CancelledError, Exception):
            pass
    await db.close_pool()


app = FastAPI(title="nexus-core", version="0.1.0", lifespan=lifespan)
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(listings.router)
app.include_router(review.router)
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


@app.get("/media/{file_path:path}")
async def media_file(file_path: str, t: str = ""):
    from fastapi.responses import FileResponse
    from fastapi import HTTPException as _HTTPException

    if auth.verify_token(t) is None:
        raise _HTTPException(status_code=401, detail="login required")
    target = (MEDIA_DIR / file_path).resolve()
    if not str(target).startswith(str(MEDIA_DIR.resolve()) + os.sep):
        raise _HTTPException(status_code=404, detail="not found")
    if not target.is_file():
        raise _HTTPException(status_code=404, detail="not found")
    return FileResponse(target)

# Static SPA hosting (built by the frontend stage into /app/static).
STATIC_DIR = Path(get_settings().static_dir)
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
