"""Admin gateway dashboard APIs (token-protected, read-only)."""
import json

from fastapi import APIRouter, Depends, HTTPException

from app import db
from app.api.auth import require_admin
from app import sqlres

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
SQL_COLLECTION_SUMMARY = sqlres.load("gateway_collection_summary.sql")
SQL_COLLECTION_ACCOUNTS = sqlres.load("gateway_collection_accounts.sql")
SQL_COLLECTION_CURRENT = sqlres.load("gateway_collection_current.sql")
SQL_COLLECTION_SOURCES = sqlres.load("gateway_collection_sources.sql")
SQL_COLLECTION_ATTENTION = sqlres.load("gateway_collection_attention.sql")


def _iso(value):
    return value.isoformat() if value else None


def _decode_jsonb(value, fallback):
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


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
    summary_row = await pool.fetchrow(SQL_COLLECTION_SUMMARY)
    collection = {
        "summary": {
            key: int(summary_row[key] or 0)
            for key in (
                "total", "pending", "processing", "deferred", "fetched",
                "no_usable", "failed", "stale_processing",
            )
        },
        "rule_id": summary_row["rule_id"],
        "last_updated": _iso(summary_row["last_updated"]),
        "accounts": [
            {
                "id": r["account_id"], "display_name": r["display_name"],
                "username": r["username"], "status": r["status"],
                "cooldown_until": _iso(r["cooldown_until"]),
                "processing_jobs": int(r["processing_jobs"]),
                "fetched_24h": int(r["fetched_24h"]),
                "last_finished_at": _iso(r["last_finished_at"]),
                "eligible_sources": int(r["eligible_sources"]),
            }
            for r in await pool.fetch(SQL_COLLECTION_ACCOUNTS)
        ],
        "current_jobs": [
            {
                "job_id": r["job_id"], "rule_id": r["rule_id"],
                "source_id": r["source_id"], "source_title": r["source_title"],
                "account_id": r["account_id"], "account_name": r["account_name"],
                "contact_username": r["contact_username"], "status": r["status"],
                "stage": r["stage"], "progress_current": r["progress_current"],
                "progress_total": r["progress_total"], "progress_note": r["progress_note"],
                "started_at": _iso(r["started_at"]),
                "heartbeat_at": _iso(r["heartbeat_at"]),
                "heartbeat_age_seconds": r["heartbeat_age_seconds"],
                "stale": r["heartbeat_age_seconds"] is not None and r["heartbeat_age_seconds"] > 300,
            }
            for r in await pool.fetch(SQL_COLLECTION_CURRENT)
        ],
        "sources": [
            {
                "id": r["source_id"], "title": r["title"],
                "primary_account": r["primary_account"],
                "backup_accounts": _decode_jsonb(r["backup_accounts"], []),
                "pending": int(r["pending"]), "processing": int(r["processing"]),
                "deferred": int(r["deferred"]), "fetched": int(r["fetched"]),
                "needs_attention": int(r["needs_attention"]),
            }
            for r in await pool.fetch(SQL_COLLECTION_SOURCES)
        ],
        "attention": [
            {
                "job_id": r["job_id"], "rule_id": r["rule_id"],
                "source_id": r["source_id"], "source_title": r["source_title"],
                "account_id": r["account_id"], "account_name": r["account_name"],
                "contact_username": r["contact_username"], "status": r["status"],
                "progress_note": r["progress_note"], "retry_count": r["retry_count"],
                "error": r["error"], "next_attempt_at": _iso(r["next_attempt_at"]),
                "updated_at": _iso(r["updated_at"]),
            }
            for r in await pool.fetch(SQL_COLLECTION_ATTENTION)
        ],
    }
    return {"accounts": accounts, "sources": sources,
            "outbox": outbox, "extract": extract, "collection": collection}
