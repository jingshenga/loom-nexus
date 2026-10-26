"""Chat image attach API (no filesystem writes — Mimosa policy keeps
core source free of file writes).

The SPA compresses to JPEG on the client (canvas, long edge <=1560,
q0.85) and POSTs the data-url; it is stored ON THE MESSAGE ROW as
base64. The bridge sender streams the bytes straight to Telegram
(Telethon accepts raw bytes), and inbound photos are saved by the
bridge (which may write under the media dir) via its DM router.
"""
import base64
import re

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app import db, sqlres
from app.api.auth import current_user_id, require_user

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
    listing_id: int = None
    contact_username: str = None
    contact_peer_id: int = None
    caption: str = ""
    data_url: str = ""


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


@router.post("/sessions-with-image")
async def open_session_with_image(body: ImageOpenBody,
                                  authorization: str = Header(default="")) -> dict:
    """Open (or reuse) a session and immediately queue the image as its
    first message — the camera/gallery flow starts a chat in one shot."""
    pool = _pool()
    user_id = current_user_id(authorization)
    _check_image(body)

    username = (body.contact_username or "").strip() or None
    peer_id = body.contact_peer_id
    if username is None and body.listing_id is not None:
        rows = await pool.fetch(SQL_LISTING_CONTACT, body.listing_id)
        if not rows:
            raise HTTPException(status_code=404, detail="listing not found")
        username = rows[0]["contact_username"]
        peer_id = peer_id or rows[0]["contact_peer_id"]
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
        caption, "photo", None, body.data_url,
    )
    status = rows[0]["status"]
    if status in ("NOT_FOUND", "FORBIDDEN", "CLOSED"):
        raise HTTPException(status_code=409, detail="会话不可用")
