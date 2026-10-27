"""Admin review workbench APIs (token-protected)."""
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel

from app import db, sqlres
from app.api.auth import require_admin

router = APIRouter(
    prefix="/api/admin/review", tags=["admin-review"],
    dependencies=[Depends(require_admin)],
)

SQL_QUEUE = sqlres.load("review_queue.sql")
SQL_PUBLISH = sqlres.load("publish.sql")
SQL_REJECT = sqlres.load("reject.sql")
SQL_UPDATE = sqlres.load("update_fields.sql")
SQL_WITHDRAW = sqlres.load("withdraw.sql")
SQL_BATCH_DELETE = sqlres.load("batch_delete.sql")
SQL_RECOLLECT_TARGETS = sqlres.load("recollect_targets.sql")
SQL_RECOLLECT_ENQUEUE = sqlres.load("recollect_enqueue.sql")
SQL_RECOLLECT_STATUS = sqlres.load("recollect_status.sql")


def _decode(value, fallback):
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    import json

    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


@router.get("/queue")
async def queue(
    status: str = "pending", limit: int = 30, offset: int = 0,
    nickname: str = None,
) -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    rows = await pool.fetch(SQL_QUEUE, status, limit, offset, nickname or None)
    items = []
    for r in rows:
        items.append({
            "id": r["id"],
            "nickname": r["nickname"],
            "district": r["district"],
            "price_amount": float(r["price_amount"]) if r["price_amount"] is not None else None,
            "price_currency": r["price_currency"],
            "description": r["description"],
            "media": _decode(r["media_json"], []),
            "evidence": _decode(r["evidence_json"], {}),
            "confidence": float(r["confidence"]) if r["confidence"] is not None else None,
            "status": r["status"],
            "contact_username": r["contact_username"],
            "created_at": r["created_at"].isoformat(),
            "source_text": r["raw_text"],
            "source_title": r["source_title"],
            "source_posted_at": r["raw_posted_at"].isoformat() if r["raw_posted_at"] else None,
        })
    return {"items": items}


class FieldEdits(BaseModel):
    nickname: str = None
    district: str = None
    price_amount: float = None
    price_currency: str = None
    description: str = None


class BatchDeleteBody(BaseModel):
    ids: list


class RecollectBody(BaseModel):
    ids: list[int]


@router.post("/recollect")
async def recollect(body: RecollectBody) -> dict:
    """Queue a Telegram refresh for up to 20 selected cards."""
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    ids = sorted(set(body.ids))
    if not ids or len(ids) > 20:
        raise HTTPException(status_code=400, detail="select between 1 and 20 cards")
    async with pool.acquire() as conn:
        async with conn.transaction():
            targets = await conn.fetch(SQL_RECOLLECT_TARGETS, ids)
            if len(targets) != len(ids):
                raise HTTPException(
                    status_code=409,
                    detail="some cards have no active Telegram source/account",
                )
            jobs = await conn.fetch(SQL_RECOLLECT_ENQUEUE, ids)
            if len(jobs) != len(ids):
                raise HTTPException(status_code=409, detail="a selected card is already being recollected")
    return {
        "jobs": [
            {"job_id": row["job_id"], "listing_id": row["listing_id"]}
            for row in jobs
        ]
    }


@router.get("/recollect/status")
async def recollect_status(job_ids: list[int] = Query(default=[])) -> dict:
    if not job_ids or len(job_ids) > 20:
        raise HTTPException(status_code=400, detail="job_ids required (max 20)")
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    rows = await pool.fetch(SQL_RECOLLECT_STATUS, job_ids)
    return {
        "jobs": [
            {
                "job_id": row["job_id"],
                "listing_id": row["listing_id"],
                "status": row["status"],
                "error": row["error"],
                "updated_at": row["updated_at"].isoformat(),
            }
            for row in rows
        ]
    }


@router.post("/batch-delete")
async def batch_delete(body: BatchDeleteBody) -> dict:
    """Delete listing cards in bulk; raw messages are kept."""
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    if not body.ids:
        raise HTTPException(status_code=400, detail="ids required")
    rows = await pool.fetch(SQL_BATCH_DELETE, body.ids)
    return {"deleted": rows[0]["deleted"]}


@router.patch("/{listing_id}")
async def edit_fields(listing_id: int, edits: FieldEdits) -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    rows = await pool.fetch(
        SQL_UPDATE, listing_id, edits.nickname, edits.district,
        edits.price_amount, edits.price_currency, edits.description,
    )
    if not rows[0]["ok"]:
        raise HTTPException(status_code=404, detail="listing not found")
    return {"ok": True}


@router.post("/{listing_id}/publish")
async def publish(listing_id: int) -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    rows = await pool.fetch(SQL_PUBLISH, listing_id)
    if not rows[0]["ok"]:
        raise HTTPException(status_code=409, detail="cannot publish (archived?)")
    return {"ok": True}


@router.post("/{listing_id}/reject")
async def reject(listing_id: int) -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    rows = await pool.fetch(SQL_REJECT, listing_id)
    if not rows[0]["ok"]:
        raise HTTPException(status_code=409, detail="only pending items can be rejected")
    return {"ok": True}


@router.post("/{listing_id}/withdraw")
async def withdraw(listing_id: int) -> dict:
    """Manual withdraw: published -> archived (leaves the square)."""
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    rows = await pool.fetch(SQL_WITHDRAW, listing_id)
    if not rows[0]["ok"]:
        raise HTTPException(status_code=409, detail="only published items can be withdrawn")
    return {"ok": True, "status": "archived"}
