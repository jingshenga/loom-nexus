"""Proxied private-chat APIs with anti-collision semantics.

Session creation returns CONFLICT_LOCKED when no idle gateway account
is available for the requested contact — the frontend must then show
the warning card and disable input. Includes a WebSocket endpoint
streaming new messages for a session (1s database polling under the
hood; fully adequate for NAS-scale usage).
"""
import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, WebSocket, WebSocketDisconnect, Depends
from pydantic import BaseModel, Field

from app import db, sqlres
from app.config import get_settings

from app.api.auth import (
    bearer_identity,
    current_user_id,
    require_admin,
    require_user,
    verify_token,
)
from app.api.chat_media import _check_image

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
SQL_CHAT_ALIAS = sqlres.load("chat_alias_get.sql")
SQL_CHAT_ALIAS_SET = sqlres.load("chat_alias_set.sql")
SQL_CHAT_DELETE = sqlres.load("chat_delete_session.sql")

EXTERNAL_CLIENT_URL = "https://web.telegram.org/"


class OpenBody(BaseModel):
    listing_id: int

    class Config:
        extra = "forbid"


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
    rows = await pool.fetch(SQL_LISTING_CONTACT, body.listing_id)
    if not rows:
        raise HTTPException(status_code=404, detail="资料已下架或暂不可咨询")
    username = (rows[0]["contact_username"] or "").strip() or None
    peer_id = rows[0]["contact_peer_id"]
    if username is None and peer_id is None:
        raise HTTPException(status_code=400, detail="listing_id / contact_username / contact_peer_id required")
    rows = await pool.fetch(SQL_OPEN, user_id, body.listing_id, username, peer_id)
    r = rows[0]
    return {
        "session_id": r["session_id"],
        "status": r["status"],
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
                "contact_username": r["contact_username"],
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


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: int, authorization: str = Header(default="")) -> dict:
    pool = _pool()
    rows = await pool.fetch(SQL_CHAT_DELETE, session_id, _user_id(authorization))
    result = rows[0]
    if result["outcome"] == "NOT_FOUND":
        raise HTTPException(status_code=404, detail="会话不存在")
    if result["outcome"] == "IN_FLIGHT":
        raise HTTPException(status_code=409, detail="消息正在投递，请稍后再删除")

    media_root = Path(get_settings().media_dir).resolve()
    for raw_path in result["media_paths"] or []:
        path = Path(raw_path)
        path = path if path.is_absolute() else media_root / path
        path = path.resolve()
        if media_root not in path.parents:
            continue
        referenced = await pool.fetchval(
            "SELECT EXISTS(SELECT 1 FROM chat_messages WHERE media_path=$1) "
            "OR EXISTS(SELECT 1 FROM listings l, jsonb_array_elements(COALESCE(l.media_json,'[]'::jsonb)) m "
            "WHERE m->>'path'=$1 OR m->>'poster'=$1)", raw_path,
        )
        if not referenced:
            path.unlink(missing_ok=True)
    return {"ok": True, "status": "deleted"}


@router.get("/alias")
async def get_chat_alias(authorization: str = Header(default="")) -> dict:
    rows = await _pool().fetch(SQL_CHAT_ALIAS, _user_id(authorization))
    if not rows:
        raise HTTPException(status_code=404, detail="用户不存在")
    row = rows[0]
    return {
        "chat_alias": row["chat_alias"],
        "is_custom": row["chat_alias_is_custom"],
        "changed_at": row["chat_alias_changed_at"].isoformat() if row["chat_alias_changed_at"] else None,
        "next_change_at": row["next_change_at"].isoformat() if row["next_change_at"] else None,
    }


class ChatAliasBody(BaseModel):
    chat_alias: str = Field(min_length=2, max_length=24)


@router.put("/alias")
async def set_chat_alias(body: ChatAliasBody, authorization: str = Header(default="")) -> dict:
    rows = await _pool().fetch(SQL_CHAT_ALIAS_SET, _user_id(authorization), body.chat_alias)
    row = rows[0]
    if row["outcome"] == "INVALID":
        raise HTTPException(status_code=422, detail="昵称需为 2–24 个中英文、数字、空格、下划线或短横线字符，且不能使用保留名称")
    if row["outcome"] == "TAKEN":
        raise HTTPException(status_code=409, detail="该咨询昵称已被使用")
    if row["outcome"] == "TOO_SOON":
        raise HTTPException(status_code=409, detail={
            "message": "咨询昵称每六个月只能修改一次",
            "next_change_at": row["next_change_at"].isoformat() if row["next_change_at"] else None,
        })
    if row["outcome"] == "NOT_FOUND":
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"chat_alias": row["current_alias"], "outcome": row["outcome"]}


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
                "media_url": f"/api/chat/sessions/{session_id}/messages/{r['id']}/image" if r["media_kind"] == "photo" else None,
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
    if body.data_url:
        _check_image(body)
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
                    "media_url": f"/api/chat/sessions/{session_id}/messages/{r['id']}/image" if r["media_kind"] == "photo" else None,
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
