"""Proxied private-chat APIs with anti-collision semantics.

Session creation returns CONFLICT_LOCKED when no idle gateway account
is available for the requested contact — the frontend must then show
the warning card and disable input. Includes a WebSocket endpoint
streaming new messages for a session (1s database polling under the
hood; fully adequate for NAS-scale usage).
"""
import asyncio
import json

from fastapi import APIRouter, Header, HTTPException, WebSocket, WebSocketDisconnect, Depends
from pydantic import BaseModel

from app import db, sqlres

from app.api.auth import (
    bearer_identity,
    current_user_id,
    require_admin,
    require_user,
    verify_token,
)

router = APIRouter(
    prefix="/api/chat", tags=["chat"],
    dependencies=[Depends(require_user)],
)

SQL_OPEN = sqlres.load("chat_open.sql")
SQL_ENQUEUE = sqlres.load("chat_enqueue.sql")
SQL_HISTORY = sqlres.load("chat_history.sql")
SQL_SESSIONS = sqlres.load("chat_sessions.sql")
SQL_LISTING_CONTACT = sqlres.load("listing_contact.sql")
SQL_CLOSE = sqlres.load("chat_close.sql")
SQL_MARK_READ = sqlres.load("chat_mark_read.sql")
SQL_RETRY = sqlres.load("retry_message.sql")

EXTERNAL_CLIENT_URL = "https://web.telegram.org/"


class OpenBody(BaseModel):
    listing_id: int = None
    contact_username: str = None
    contact_peer_id: int = None


class SendBody(BaseModel):
    content: str = ""
    client_msg_id: str = None
    data_url: str = ""  # optional compressed image (camera / gallery)


def _pool():
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    return pool


def _user_id(authorization: str) -> int:
    """The acting user is the token identity; client-supplied ids are
    never trusted."""
    return current_user_id(authorization)


@router.post("/sessions")
async def open_session(body: OpenBody, authorization: str = Header(default="")) -> dict:
    pool = _pool()
    user_id = _user_id(authorization)
    # contacts are identified by their extracted @username; the peer id
    # is an optional cache (filled by presence resolution / first DM)
    username = (body.contact_username or "").strip() or None
    peer_id = body.contact_peer_id
    if username is None and body.listing_id is not None:
        rows = await pool.fetch(SQL_LISTING_CONTACT, body.listing_id)
        if not rows:
            raise HTTPException(status_code=404, detail="listing not found or not published")
        username = rows[0]["contact_username"]
        peer_id = peer_id or rows[0]["contact_peer_id"]
    if username is None and peer_id is None:
        raise HTTPException(status_code=400, detail="listing_id / contact_username / contact_peer_id required")
    rows = await pool.fetch(SQL_OPEN, user_id, body.listing_id, username, peer_id)
    r = rows[0]
    return {
        "session_id": r["session_id"],
        "status": r["status"],
        "account_id": r["account_id"],
        "contact_username": username,
        "external_url": EXTERNAL_CLIENT_URL if r["status"] == "CONFLICT_LOCKED" else None,
    }


@router.get("/sessions")
async def list_sessions(authorization: str = Header(default="")) -> dict:
    pool = _pool()
    rows = await pool.fetch(SQL_SESSIONS, _user_id(authorization))
    return {
        "items": [
            {
                "session_id": r["session_id"],
                "contact_peer_id": r["contact_peer_id"],
                "contact_username": r["contact_username"],
                "account_id": r["account_id"],
                "status": r["status"],
                "listing_id": r["listing_id"],
                "nickname": r["nickname"],
                "last_msg_at": r["last_msg_at"].isoformat() if r["last_msg_at"] else None,
                "unread": r["unread"],
                "last_content": (r["last_content"] or "")[:80],
            }
            for r in rows
        ]
    }


@router.post("/sessions/{session_id}/close")
async def close_session(session_id: int, authorization: str = Header(default="")) -> dict:
    pool = _pool()
    rows = await pool.fetch(SQL_CLOSE, session_id, _user_id(authorization))
    if rows[0]["outcome"] != "closed":
        raise HTTPException(status_code=404, detail="session not found or not open")
    return {"ok": True, "status": "closed"}


@router.get("/sessions/{session_id}/messages")
async def history(session_id: int, authorization: str = Header(default="")) -> dict:
    pool = _pool()
    user_id = _user_id(authorization)
    identity = bearer_identity(authorization)
    is_admin = bool(identity and identity[1])
    rows = await pool.fetch(SQL_HISTORY, session_id, user_id, 200)
    if rows:
        # opening the conversation counts as reading it
        await pool.fetch(SQL_MARK_READ, session_id, user_id, rows[-1]["id"])
    return {
        "items": [
            {
                "id": r["id"],
                "direction": r["direction"],
                "content": r["content"],
                # regular users only see sending / delivered / failed;
                # retry internals, error text and the delivery channel
                # are admin-only detail
                "status": (
                    r["status"] if is_admin
                    else ("sending" if r["status"] == "queued" else r["status"])
                ),
                "client_msg_id": r["client_msg_id"],
                "created_at": r["created_at"].isoformat(),
                "media_kind": r["media_kind"],
                "media_path": r["media_path"],
                **({
                    "error_text": r["error_text"],
                    "attempts": r["attempts"],
                    "channel": r["channel"],
                } if is_admin else {}),
            }
            for r in rows
        ]
    }


class RetryBody(BaseModel):
    to_bot: bool = False


@router.post("/sessions/{session_id}/messages/{message_id}/retry",
             dependencies=[Depends(require_admin)])
async def retry_message(session_id: int, message_id: int, body: RetryBody,
                        authorization: str = Header(default="")) -> dict:
    """Requeue a failed message; to_bot pins the session onto the
    contact's bot delivery channel."""
    pool = _pool()
    user_id = current_user_id(authorization)
    rows = await pool.fetch(
        sqlres.load("retry_message.sql"),
        session_id, message_id, user_id, body.to_bot,
    )
    if not rows[0]["ok"]:
        raise HTTPException(status_code=404, detail="message not failed or not yours")
    return {"ok": True, "status": "queued"}


@router.post("/sessions/{session_id}/messages")
async def send(session_id: int, body: SendBody, authorization: str = Header(default="")) -> dict:
    pool = _pool()
    user_id = _user_id(authorization)
    if not (body.content or "").strip() and not body.data_url:
        raise HTTPException(status_code=400, detail="empty message")
    rows = await pool.fetch(
        SQL_ENQUEUE, session_id, user_id, body.client_msg_id,
        (body.content or "").strip(),
        "photo" if body.data_url else None,
        body.data_url or None,
    )
    r = rows[0]
    if r["status"] in ("NOT_FOUND", "FORBIDDEN"):
        raise HTTPException(status_code=404, detail="session not found")
    if r["status"] == "CLOSED":
        raise HTTPException(status_code=409, detail="session closed")
    return {"message_id": r["message_id"], "job_id": r["job_id"], "status": r["status"]}


@router.websocket("/ws/{session_id}")
async def ws_chat(websocket: WebSocket, session_id: int, token: str = ""):
    identity = verify_token(token)
    pool = db.get_pool()
    if pool is None or identity is None:
        await websocket.close(code=4400)
        return
    user_id = identity[0]
    await websocket.accept()
    last_id = 0
    last_status = {}
    try:
        while True:
            rows = await pool.fetch(SQL_HISTORY, session_id, user_id, 200)
            for r in rows:
                payload = {
                    "id": r["id"],
                    "direction": r["direction"],
                    "content": r["content"],
                    "status": r["status"],
                    "created_at": r["created_at"].isoformat(),
                    "media_kind": r["media_kind"],
                    "media_path": r["media_path"],
                }
                if r["id"] > last_id:
                    last_id = r["id"]
                    last_status[r["id"]] = r["status"]
                    await websocket.send_json(payload)
                elif last_status.get(r["id"]) != r["status"]:
                    last_status[r["id"]] = r["status"]
                    payload["update"] = True
                    await websocket.send_json(payload)
            await asyncio.sleep(1)
    except (WebSocketDisconnect, RuntimeError):
        return
    except json.JSONDecodeError:
        return
