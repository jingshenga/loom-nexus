"""Admin review workbench APIs (token-protected)."""
import base64
import binascii
import json
import re
import uuid
from pathlib import Path
from PIL import Image

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app import db, sqlres
from app.config import get_settings
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
SQL_DUTY_RECOVERY = sqlres.load("duty_recovery_queue.sql")
SQL_DUTY_RECOVERY_UPDATE = sqlres.load("duty_recovery_update.sql")
SQL_DUTY_MANUAL_LISTING = sqlres.load("duty_manual_listing.sql")
SQL_DUTY_SCAN_ENQUEUE = sqlres.load("duty_recovery_scan_enqueue.sql")


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


@router.get("/duty-recovery")
async def duty_recovery() -> dict:
    """List on-duty collection misses for administrator-assisted recovery."""
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    rows = await pool.fetch(SQL_DUTY_RECOVERY)
    items = []
    for row in rows:
        item = dict(row)
        for key, value in item.items():
            if hasattr(value, "isoformat"):
                item[key] = value.isoformat()
        item["candidates"] = _decode(item.get("candidates"), [])
        items.append(item)
    return {"items": items}


class DutyRecoveryUpdate(BaseModel):
    contact_peer_id: int | None = None
    profile_url: str | None = None
    channel_url: str | None = None
    bot_url: str | None = None


@router.patch("/duty-recovery/{job_id}")
async def update_duty_recovery(job_id: int, body: DutyRecoveryUpdate) -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    links = [value.strip() if value else None for value in (body.profile_url, body.channel_url, body.bot_url)]
    for value in links:
        if value and not re.fullmatch(r"https?://t\.me/[A-Za-z0-9_]+(?:/\d+)?(?:\?[^\s]*)?", value, re.I):
            raise HTTPException(status_code=400, detail="links must be Telegram t.me URLs")
    rows = await pool.fetch(
        SQL_DUTY_RECOVERY_UPDATE, job_id, body.contact_peer_id,
        *links,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="recovery job not found")
    return {"ok": True}


class DutyScanBody(BaseModel):
    target: int


@router.post("/duty-recovery/{job_id}/scan")
async def scan_duty_recovery(job_id: int, body: DutyScanBody) -> dict:
    if body.target not in {20, 50, 100}:
        raise HTTPException(status_code=400, detail="target must be 20, 50 or 100")
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    row = await pool.fetchrow(
        "SELECT j.channel_url, COALESCE(s.scanned_count, 0) AS scanned_count, "
        "COALESCE(s.scan_target, 0) AS scan_target, COALESCE(s.status, 'idle') AS scan_status "
        "FROM duty_collection_jobs j LEFT JOIN duty_recovery_scans s ON s.duty_job_id = j.id "
        "WHERE j.id = $1::bigint", job_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="recovery job not found")
    if not row["channel_url"]:
        raise HTTPException(status_code=409, detail="save the public channel link first")
    if row["scan_status"] == "processing":
        raise HTTPException(status_code=409, detail="scan is already running")
    if body.target <= row["scanned_count"]:
        raise HTTPException(status_code=409, detail="requested scan depth has already been reached")
    rows = await pool.fetch(SQL_DUTY_SCAN_ENQUEUE, job_id, body.target)
    if not rows:
        raise HTTPException(status_code=409, detail="scan could not be queued")
    return {"queued": True, "target": body.target}


class ManualDutyListing(BaseModel):
    contact_peer_id: int | None = None
    contact_username: str = ""
    nickname: str = ""
    district: str = ""
    price_amount: float | None = None
    price_currency: str = "CNY"
    description: str = ""
    source_url: str = ""
    bot_url: str = ""
    image_data: str = ""
    image_mime: str = ""


@router.post("/duty-recovery/{job_id}/manual-card")
async def create_manual_duty_card(job_id: int, body: ManualDutyListing) -> dict:
    """Create a pending card from administrator supplied evidence and image."""
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    recovery = await pool.fetchrow(
        "SELECT contact_username, contact_peer_id, bot_url, status "
        "FROM duty_collection_jobs WHERE id = $1::bigint", job_id,
    )
    if recovery is None:
        raise HTTPException(status_code=404, detail="recovery job not found")
    if recovery["status"] not in {"not_found", "failed"}:
        raise HTTPException(status_code=409, detail="this recovery item has already been resolved")
    media = []
    if body.image_data:
        if len(body.image_data) > 14_000_000 or body.image_mime not in {"image/jpeg", "image/png", "image/webp"}:
            raise HTTPException(status_code=400, detail="image must be JPEG, PNG or WebP under 10 MB")
        try:
            import io

            raw = base64.b64decode(body.image_data, validate=True)
            if len(raw) > 10 * 1024 * 1024:
                raise ValueError("image too large")
            image = Image.open(io.BytesIO(raw))
            if image.width * image.height > 40_000_000:
                raise ValueError("image dimensions are too large")
            image.verify()
            formats = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}
            if image.format != formats[body.image_mime]:
                raise ValueError("image type does not match content")
        except (ValueError, binascii.Error, OSError, Image.DecompressionBombError) as exc:
            raise HTTPException(status_code=400, detail="invalid image") from exc
        media_dir = Path(get_settings().media_dir)
        target_dir = media_dir / "manual"
        target_dir.mkdir(parents=True, exist_ok=True)
        extension = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[body.image_mime]
        filename = f"duty_{job_id}_{uuid.uuid4().hex}{extension}"
        (target_dir / filename).write_bytes(raw)
        media = [{"kind": "photo", "path": f"manual/{filename}"}]

    source_url = body.source_url.strip()
    bot_url = body.bot_url.strip() or recovery["bot_url"] or ""
    for label, value in (("source_url", source_url), ("bot_url", bot_url)):
        if value and not re.fullmatch(r"https?://t\.me/[A-Za-z0-9_]+(?:/\d+)?(?:\?[^\s]*)?", value, re.I):
            raise HTTPException(status_code=400, detail=f"{label} must be a Telegram link")
    evidence = {
        "manual_duty_recovery": True,
        "recovery_job_id": job_id,
        "source_url": source_url,
        "bot_url": bot_url,
        "contact_username": recovery["contact_username"],
    }
    rows = await pool.fetch(
        SQL_DUTY_MANUAL_LISTING, body.contact_peer_id or recovery["contact_peer_id"], recovery["contact_username"],
        body.nickname, body.district, body.price_amount, body.price_currency,
        body.description, json.dumps(media), json.dumps(evidence), job_id,
    )
    return {"id": rows[0]["id"], "status": "pending"}


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
