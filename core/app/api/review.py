"""Admin review workbench APIs (token-protected)."""
from fastapi import APIRouter, Depends, Header, HTTPException
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
async def queue(status: str = "pending", limit: int = 30, offset: int = 0) -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    rows = await pool.fetch(SQL_QUEUE, status, limit, offset)
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
