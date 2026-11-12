"""Admin gateway dashboard APIs (token-protected, read-only)."""
from fastapi import APIRouter, Depends, HTTPException

from app import db
from app.api.auth import require_admin

router = APIRouter(
    prefix="/api/admin/gateway", tags=["admin-gateway"],
    dependencies=[Depends(require_admin)],
)

SQL_ACCOUNTS = """
SELECT id, account_uid, display_name, username, status, last_active_at
FROM bridge_accounts ORDER BY id
"""
SQL_SOURCES = """
SELECT s.id, s.peer_id, s.title, s.username, s.last_msg_id, s.is_active,
       a.username AS account_username
FROM ingest_sources s
LEFT JOIN bridge_accounts a ON a.id = s.assigned_account_id
ORDER BY s.id
"""
SQL_OUTBOX = """
SELECT status, COUNT(*) AS cnt FROM outbox_jobs GROUP BY status ORDER BY status
"""
SQL_EXTRACT = """
SELECT extract_state, COUNT(*) AS cnt
FROM raw_messages GROUP BY extract_state ORDER BY extract_state
"""


@router.get("/overview")
async def overview() -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    accounts = [
        {
            "id": r["id"], "uid": r["account_uid"],
            "display_name": r["display_name"], "username": r["username"],
            "status": r["status"],
            "last_active_at": r["last_active_at"].isoformat() if r["last_active_at"] else None,
        }
        for r in await pool.fetch(SQL_ACCOUNTS)
    ]
    sources = [
        {
            "id": r["id"], "peer_id": r["peer_id"], "title": r["title"],
            "username": r["username"], "last_msg_id": r["last_msg_id"],
            "is_active": r["is_active"], "account": r["account_username"],
        }
        for r in await pool.fetch(SQL_SOURCES)
    ]
    outbox = {r["status"]: r["cnt"] for r in await pool.fetch(SQL_OUTBOX)}
    extract = {r["extract_state"]: r["cnt"] for r in await pool.fetch(SQL_EXTRACT)}
    return {"accounts": accounts, "sources": sources,
            "outbox": outbox, "extract": extract}
