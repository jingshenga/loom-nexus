"""Contact bio penetration queue (type C manual safety gate).

Pending probes carry the contact bio and candidate room links; an
admin must explicitly approve before the bridge joins anything.
"""
import json

from fastapi import APIRouter, Depends, HTTPException

from app import db, sqlres
from app.api.auth import require_admin

router = APIRouter(
    prefix="/api/admin/probe", tags=["admin-probe"],
    dependencies=[Depends(require_admin)],
)

SQL_QUEUE = sqlres.load("probe_queue.sql")
SQL_DECIDE = sqlres.load("probe_decide.sql")


@router.get("/queue")
async def queue(status: str = "pending") -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    rows = await pool.fetch(SQL_QUEUE, status)
    items = []
    for r in rows:
        links = r["candidate_links"]
        if isinstance(links, str):
            links = json.loads(links)
        items.append({
            "id": r["id"],
            "contact_peer_id": r["contact_peer_id"],
            "bio_text": r["bio_text"],
            "links": links or [],
            "status": r["status"],
            "created_at": r["created_at"].isoformat(),
        })
    return {"items": items}


@router.post("/{probe_id}/approve")
async def approve(probe_id: int) -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    rows = await pool.fetch(SQL_DECIDE, probe_id, "approved")
    outcome = rows[0]["outcome"]
    if outcome != "approved":
        raise HTTPException(status_code=409, detail=f"cannot approve: {outcome}")
    return {"ok": True}


@router.post("/{probe_id}/reject")
async def reject(probe_id: int) -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    rows = await pool.fetch(SQL_DECIDE, probe_id, "rejected")
    outcome = rows[0]["outcome"]
    if outcome != "rejected":
        raise HTTPException(status_code=409, detail=f"cannot reject: {outcome}")
    return {"ok": True}
