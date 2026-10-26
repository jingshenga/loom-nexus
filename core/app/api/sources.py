"""Admin source management: discover dialogs, bind/activate sources.

Sources define which groups/channels the gateway accounts collect
from; dialogs come from the bridge's dialog_cache refresh.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db, sqlres
from app.api.auth import require_admin

router = APIRouter(
    prefix="/api/admin/sources", tags=["admin-sources"],
    dependencies=[Depends(require_admin)],
)

SQL_SOURCES = sqlres.load("admin_sources.sql")
SQL_DIALOGS = sqlres.load("dialog_list.sql")
SQL_ENSURE = sqlres.load("ensure_source.sql")
SQL_ASSIGN = sqlres.load("assign_source.sql")
SQL_ACTIVE = sqlres.load("source_set_active.sql")
SQL_SOURCE_OF = sqlres.load("source_of.sql")
SQL_BF_ENQUEUE = sqlres.load("backfill_enqueue.sql")
SQL_BF_STATUS = sqlres.load("backfill_status.sql")
SQL_BF_ALL = sqlres.load("backfill_all.sql")


def _pool():
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    return pool


@router.get("")
async def list_sources() -> dict:
    pool = _pool()
    rows = await pool.fetch(SQL_SOURCES)
    return {
        "items": [
            {
                "id": r["source_id"],
                "peer_id": r["peer_id"],
                "title": r["title"],
                "username": r["username"],
                "is_active": r["is_active"],
                "account_id": r["account_id"],
                "account_name": r["account_name"],
                "last_msg_id": r["last_msg_id"],
                "raw_count": r["raw_count"],
            }
            for r in rows
        ]
    }


@router.get("/dialogs")
async def list_dialogs() -> dict:
    pool = _pool()
    rows = await pool.fetch(SQL_DIALOGS)
    return {
        "items": [
            {
                "account_id": r["account_id"],
                "account_name": r["account_name"],
                "peer_id": r["peer_id"],
                "title": r["title"],
                "username": r["username"],
                "kind": r["kind"],
            }
            for r in rows
        ]
    }


class BindBody(BaseModel):
    peer_id: int
    title: str = None
    username: str = None
    account_id: int


@router.post("")
async def bind_source(body: BindBody) -> dict:
    pool = _pool()
    rows = await pool.fetch(
        SQL_ENSURE, body.peer_id, body.title or "", body.username,
    )
    source_id = rows[0]["source_id"]
    await pool.fetch(SQL_ASSIGN, source_id, body.account_id)
    return {"source_id": source_id, "status": "bound"}


class ActiveBody(BaseModel):
    is_active: bool


@router.post("/{source_id}/active")
async def set_active(source_id: int, body: ActiveBody) -> dict:
    pool = _pool()
    rows = await pool.fetch(SQL_ACTIVE, source_id, body.is_active)
    if not rows[0]["ok"]:
        raise HTTPException(status_code=404, detail="source not found")
    return {"ok": True, "is_active": body.is_active}


class BackfillBody(BaseModel):
    limit: int = 50


@router.post("/{source_id}/backfill")
async def start_backfill(source_id: int, body: BackfillBody) -> dict:
    """Queue a history pull (text + media) for rule-tuning samples."""
    pool = _pool()
    rows = await pool.fetch(SQL_SOURCE_OF, source_id)
    if not rows:
        raise HTTPException(status_code=404, detail="source not found")
    account_id = rows[0]["assigned_account_id"]
    if account_id is None:
        raise HTTPException(status_code=409, detail="source has no assigned account")
    job = await pool.fetch(
        SQL_BF_ENQUEUE, source_id, account_id, rows[0]["peer_id"],
        max(1, min(body.limit, 200)), "history",
    )
    return {"job_id": job[0]["job_id"], "status": "pending"}


@router.post("/backfill-media-all")
async def backfill_media_all(body: BackfillBody = None) -> dict:
    """Queue a MEDIA-ONLY pass for every active bound source."""
    pool = _pool()
    limit = max(1, min((body.limit if body else 100), 200))
    queued = 0
    rows = await pool.fetch(SQL_SOURCES)
    for s in rows:
        if not s["is_active"] or s["account_id"] is None:
            continue
        await pool.fetch(
            SQL_BF_ENQUEUE, s["source_id"], s["account_id"], s["peer_id"], limit, "media",
        )
        queued += 1
    return {"queued": queued, "limit": limit, "mode": "media"}


@router.post("/backfill-all")
async def backfill_all(body: BackfillBody = None) -> dict:
    """Queue a history pull for EVERY active bound source."""
    pool = _pool()
    limit = max(1, min((body.limit if body else 50), 200))
    rows = await pool.fetch(SQL_BF_ALL, limit)
    return {"queued": rows[0]["queued"], "limit": limit}


@router.get("/{source_id}/backfill/status")
async def backfill_status(source_id: int) -> dict:
    pool = _pool()
    rows = await pool.fetch(SQL_BF_STATUS, source_id)
    if not rows:
        return {"status": "none"}
    return {
        "status": rows[0]["status"],
        "fetched": rows[0]["fetched"],
        "error": rows[0]["error"],
    }
