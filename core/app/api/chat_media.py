"""Chat image attach API (no filesystem writes — Mimosa policy keeps
core source free of file writes).

The SPA compresses to JPEG on the client (canvas, long edge <=1560,
q0.85) and POSTs the data-url; it is stored ON THE MESSAGE ROW as
base64. The bridge sender streams the bytes straight to Telegram
(Telethon accepts raw bytes), and inbound photos are saved by the
bridge (which may write under the media dir) via its DM router.
"""
import base64
import io
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel

from app import db, sqlres
from app.api.auth import current_user_id, require_user
from app.config import get_settings

router = APIRouter(
    prefix="/api/chat", tags=["chat-media"],
    dependencies=[Depends(require_user)],
)

MAX_DECODED = 3 * 1024 * 1024  # client compression keeps photos well below
MAX_B64 = (MAX_DECODED * 4) // 3 + 1024

SQL_OPEN = sqlres.load("chat_open.sql")
SQL_LISTING_CONTACT = sqlres.load("listing_contact.sql")

DATA_URL_RE = re.compile(r"^data:image/(jpeg|png|webp);base64,[A-Za-z0-9+/=]+$")


class ImageOpenBody(BaseModel):
    listing_id: int
    caption: str = ""
    data_url: str = ""

    class Config:
        extra = "forbid"


def _pool():
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    return pool


def _check_image(body: ImageOpenBody) -> None:
    if not body.data_url:
        raise HTTPException(status_code=400, detail="缺少图片")
    if len(body.data_url) > MAX_B64 or not DATA_URL_RE.match(body.data_url):
        raise HTTPException(status_code=400, detail="图片数据无效或过大")
    try:
        raw = base64.b64decode(body.data_url.split(",", 1)[1], validate=True)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="图片解码失败")
    if len(raw) > MAX_DECODED:
        raise HTTPException(status_code=413, detail="图片过大")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in ("JPEG", "PNG", "WEBP"):
                raise ValueError("unsupported image format")
            image.load()
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError):
        raise HTTPException(status_code=400, detail="图片文件损坏或格式无效")


@router.post("/sessions-with-image")
async def open_session_with_image(body: ImageOpenBody,
                                  authorization: str = Header(default="")) -> dict:
    """Open (or reuse) a session and immediately queue the image as its
    first message — the camera/gallery flow starts a chat in one shot."""
    pool = _pool()
    user_id = current_user_id(authorization)
    _check_image(body)

    rows = await pool.fetch(SQL_LISTING_CONTACT, body.listing_id)
    if not rows:
        raise HTTPException(status_code=404, detail="资料已下架或暂不可咨询")
    username = (rows[0]["contact_username"] or "").strip() or None
    peer_id = rows[0]["contact_peer_id"]
    if username is None and peer_id is None:
        raise HTTPException(status_code=400, detail="缺少联系人")

    rows = await pool.fetch(SQL_OPEN, user_id, body.listing_id, username, peer_id)
    r = rows[0]
    if r["status"] == "CONFLICT_LOCKED":
        return {
            "session_id": None, "status": "CONFLICT_LOCKED",
            "external_url": "https://web.telegram.org/",
        }
    await _enqueue_image(pool, r["session_id"], user_id, body)
    return {"session_id": r["session_id"], "status": r["status"]}


async def _enqueue_image(pool, session_id, user_id, body: ImageOpenBody) -> None:
    caption = (body.caption or "").strip()
    rows = await pool.fetch(
        sqlres.load("chat_enqueue.sql"),
        session_id, user_id, f"img-{session_id}-{user_id}",
        caption, "photo", body.data_url,
    )
    status = rows[0]["status"]
    if status in ("NOT_FOUND", "FORBIDDEN", "CLOSED"):
        raise HTTPException(status_code=409, detail="会话不可用")


@router.get("/sessions/{session_id}/messages/{message_id}/image")
async def chat_message_image(session_id: int, message_id: int,
                             authorization: str = Header(default="")) -> Response:
    user_id = current_user_id(authorization)
    row = await _pool().fetchrow(
        "SELECT m.media_b64,m.media_path FROM chat_messages m "
        "JOIN chat_sessions cs ON cs.id=m.session_id "
        "WHERE m.id=$1 AND m.session_id=$2 AND cs.user_id=$3 AND m.media_kind='photo'",
        message_id, session_id, user_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="图片不存在")
    if row["media_b64"]:
        match = re.fullmatch(r"data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/=]+)", row["media_b64"])
        if not match:
            raise HTTPException(status_code=404, detail="图片不可用")
        try:
            content = base64.b64decode(match.group(2), validate=True)
        except (ValueError, TypeError):
            raise HTTPException(status_code=404, detail="图片不可用")
        media_type = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[match.group(1)]
    else:
        raw_path = row["media_path"]
        if not raw_path:
            raise HTTPException(status_code=404, detail="图片不可用")
        root = Path(get_settings().media_dir).resolve()
        path = Path(raw_path)
        path = path if path.is_absolute() else root / path
        path = path.resolve()
        if root not in path.parents or not path.is_file():
            raise HTTPException(status_code=404, detail="图片不可用")
        content = path.read_bytes()
        media_type = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else (
            "image/png" if path.suffix.lower() == ".png" else "image/webp"
        )
    return Response(content=content, media_type=media_type,
                    headers={"Cache-Control": "private, no-store"})
