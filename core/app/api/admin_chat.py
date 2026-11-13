"""Administrator-only chat delivery diagnostics."""
from fastapi import APIRouter, Depends, HTTPException, Query

from app import db
from app.api.auth import require_admin

router = APIRouter(
    prefix="/api/admin/chat", tags=["admin-chat"],
    dependencies=[Depends(require_admin)],
)


def _pool():
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    return pool


@router.get("/sessions")
async def list_sessions(limit: int = Query(default=100, ge=1, le=200),
                        offset: int = Query(default=0, ge=0)) -> dict:
    rows = await _pool().fetch(
        "SELECT cs.id AS session_id,cs.user_id,su.username,su.chat_alias,"
        "cs.contact_username,cs.contact_peer_id,cs.assigned_account_id,"
        "ba.account_uid,ba.display_name AS gateway_account,cs.status,"
        "cs.delivery_channel,cs.created_at,cs.last_msg_at,"
        "(SELECT m.content FROM chat_messages m WHERE m.session_id=cs.id "
        "ORDER BY m.id DESC LIMIT 1) AS last_content "
        "FROM chat_sessions cs JOIN site_users su ON su.id=cs.user_id "
        "LEFT JOIN bridge_accounts ba ON ba.id=cs.assigned_account_id "
        "ORDER BY COALESCE(cs.last_msg_at,cs.created_at) DESC,cs.id DESC LIMIT $1 OFFSET $2",
        limit, offset,
    )
    return {"items": [dict(row) for row in rows]}


@router.get("/sessions/{session_id}/messages")
async def session_messages(session_id: int) -> dict:
    rows = await _pool().fetch(
        "SELECT m.id,m.direction,m.content,m.media_kind,m.status,m.platform_msg_id,"
        "m.outbound_alias_snapshot,m.created_at,m.channel,m.error_text,m.attempts,"
        "oj.status AS delivery_status,oj.run_after,oj.last_error "
        "FROM chat_messages m LEFT JOIN outbox_jobs oj ON oj.chat_msg_id=m.id "
        "WHERE m.session_id=$1 ORDER BY m.id DESC LIMIT 200", session_id,
    )
    if not rows:
        exists = await _pool().fetchval("SELECT EXISTS(SELECT 1 FROM chat_sessions WHERE id=$1)", session_id)
        if not exists:
            raise HTTPException(status_code=404, detail="会话不存在")
    items = []
    for row in rows:
        item = dict(row)
        alias = item.get("outbound_alias_snapshot")
        content = item.get("content") or ""
        if item.get("direction") == "out" and alias:
            item["telegram_text"] = f"【{alias}】{content}"
        else:
            item["telegram_text"] = content
        items.append(item)
    return {"items": items}


@router.get("/users/{user_id}/alias-audit")
async def alias_audit(user_id: int) -> dict:
    rows = await _pool().fetch(
        "SELECT id,old_alias,new_alias,changed_at,is_initial FROM chat_alias_audit "
        "WHERE user_id=$1 ORDER BY id DESC", user_id,
    )
    return {"items": [dict(row) for row in rows]}
