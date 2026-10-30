"""Admin review workbench APIs (token-protected)."""
import base64
import binascii
import json
import re
import uuid
from pathlib import Path
from typing import Literal
from PIL import Image

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel

from app import db, sqlres
from app.config import get_settings
from app.api.auth import current_user_id, require_admin

router = APIRouter(
    prefix="/api/admin/review", tags=["admin-review"],
    dependencies=[Depends(require_admin)],
)

SQL_QUEUE = sqlres.load("review_queue.sql")
SQL_DUPLICATE_CANDIDATES = sqlres.load("duplicate_candidates.sql")
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
            "contact_peer_id": r["contact_peer_id"],
            "contact_username": r["contact_username"],
            "created_at": r["created_at"].isoformat(),
            "source_text": r["raw_text"],
            "source_title": r["source_title"],
            "source_posted_at": r["raw_posted_at"].isoformat() if r["raw_posted_at"] else None,
        })
    return {"items": items}


@router.get("/duplicates")
async def duplicate_candidates() -> dict:
    """List active cards with matching non-Telegram profile content."""
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    rows = await pool.fetch(SQL_DUPLICATE_CANDIDATES)

    def card(row, prefix: str) -> dict:
        return {
            "id": row[f"{prefix}_id"],
            "status": row[f"{prefix}_status"],
            "contact_peer_id": row[f"{prefix}_peer_id"],
            "contact_username": row[f"{prefix}_username"],
            "nickname": row[f"{prefix}_nickname"],
            "district": row[f"{prefix}_district"],
            "price_amount": float(row[f"{prefix}_price"])
                if row[f"{prefix}_price"] is not None else None,
            "price_currency": row[f"{prefix}_currency"],
            "description": row[f"{prefix}_description"],
            "media": _decode(row[f"{prefix}_media"], []),
            "source_text": row[f"{prefix}_source_text"],
            "source_title": row[f"{prefix}_source_title"],
            "collected_at": row[f"{prefix}_collected_at"].isoformat(),
            "posted_at": row[f"{prefix}_posted_at"].isoformat()
                if row[f"{prefix}_posted_at"] else None,
        }

    return {"items": [{"left": card(row, "left"), "right": card(row, "right")}
                       for row in rows]}


class DuplicateDecision(BaseModel):
    decision: Literal["keep_latest", "keep_both"]


@router.post("/duplicates/{first_id}/{second_id}")
async def decide_duplicate(
    first_id: int, second_id: int, body: DuplicateDecision,
    authorization: str = Header(default=""),
) -> dict:
    """Record an admin decision; retaining the latest archives the older card."""
    if first_id == second_id:
        raise HTTPException(status_code=400, detail="two different cards are required")
    left_id, right_id = sorted((first_id, second_id))
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")

    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                "SELECT l.id, l.status, l.created_at, l.nickname, l.district, "
                "l.price_amount, l.price_currency, l.contact_peer_id, "
                "l.contact_username, COALESCE(NULLIF(rm.raw_text, ''), l.description, '') AS source_text "
                "FROM listings l LEFT JOIN raw_messages rm ON rm.id=l.source_msg_id "
                "WHERE l.id = ANY($1::bigint[]) ORDER BY l.id FOR UPDATE OF l",
                [left_id, right_id],
            )
            if len(rows) != 2:
                raise HTTPException(status_code=404, detail="card not found")
            if any(row["status"] not in {"published", "pending"} for row in rows):
                raise HTTPException(status_code=409, detail="cards are no longer active")

            def signature(value: str) -> str:
                value = re.sub(r"https?://\S+|@[A-Za-z0-9_]+", " ", value, flags=re.I)
                return re.sub(r"\s+", "", value.casefold())

            first, second = rows
            same_identity = (
                first["contact_peer_id"] is not None
                and first["contact_peer_id"] == second["contact_peer_id"]
            ) or (
                (first["contact_peer_id"] is None or second["contact_peer_id"] is None)
                and (first["contact_username"] or "").casefold()
                    == (second["contact_username"] or "").casefold()
            )
            same_profile = (
                first["nickname"] is not None
                and second["nickname"] is not None
                and first["nickname"].strip().casefold() == second["nickname"].strip().casefold()
                and first["district"] == second["district"]
                and first["price_amount"] == second["price_amount"]
                and first["price_currency"] == second["price_currency"]
                and signature(first["source_text"]) == signature(second["source_text"])
                and signature(first["source_text"]) != ""
                and not same_identity
            )
            if not same_profile:
                raise HTTPException(status_code=409, detail="cards are not a current duplicate candidate")

            latest = max(rows, key=lambda row: (row["created_at"], row["id"]))
            archived_id = None
            if body.decision == "keep_latest":
                archived_id = next(row["id"] for row in rows if row["id"] != latest["id"])
                await conn.execute(
                    "UPDATE listings SET status='archived', updated_at=NOW() "
                    "WHERE id=$1 AND status IN ('published', 'pending')",
                    archived_id,
                )
            await conn.execute(
                "INSERT INTO listing_duplicate_decisions "
                "(left_listing_id, right_listing_id, decision, decided_by) "
                "VALUES ($1, $2, $3, $4) "
                "ON CONFLICT (left_listing_id, right_listing_id) DO UPDATE SET "
                "decision=EXCLUDED.decision, decided_by=EXCLUDED.decided_by, "
                "decided_at=NOW()",
                left_id, right_id, body.decision, current_user_id(authorization),
            )
    return {"ok": True, "decision": body.decision,
            "preferred_id": latest["id"], "archived_id": archived_id}


class FieldEdits(BaseModel):
    nickname: str = None
    district: str = None
    price_amount: float = None
    price_currency: str = None
    description: str = None


class ListingContentEdit(BaseModel):
    nickname: str
    district: str
    price_amount: float | None = None
    price_currency: str
    description: str
    media: list[dict]
    manual_order: bool = False


class BatchDeleteBody(BaseModel):
    ids: list


class RecollectBody(BaseModel):
    ids: list[int]


class RotatePhotoBody(BaseModel):
    path: str
    clockwise: bool


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


@router.patch("/{listing_id}/content")
async def edit_content(
    listing_id: int, body: ListingContentEdit,
    authorization: str = Header(default=""),
) -> dict:
    """Last-resort admin edits for a published card and its media display."""
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    if body.price_amount is not None and body.price_amount < 0:
        raise HTTPException(status_code=400, detail="price cannot be negative")
    if body.price_amount is not None and not body.price_currency.strip():
        raise HTTPException(status_code=400, detail="currency is required for a price")

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, source_msg_id, nickname, district, price_amount, "
                "price_currency, description, media_json, manual_fields FROM listings "
                "WHERE id=$1 FOR UPDATE",
                listing_id,
            )
            if row is None:
                raise HTTPException(status_code=404, detail="listing not found")
            old_media = _decode(row["media_json"], [])
            old_by_path = {entry.get("path"): entry for entry in old_media}
            paths = [entry.get("path") for entry in body.media]
            if (len(paths) != len(old_media) or len(set(paths)) != len(paths)
                    or set(paths) != set(old_by_path)):
                raise HTTPException(status_code=400, detail="media items cannot be added or removed here")

            updated_media = []
            for index, submitted in enumerate(body.media):
                entry = dict(old_by_path[submitted["path"]])
                if entry.get("kind") == "photo":
                    visibility = submitted.get("visibility", entry.get("visibility"))
                    if visibility not in {"public", "admin", "pending"}:
                        raise HTTPException(status_code=400, detail="invalid media visibility")
                    entry["visibility"] = visibility
                    if submitted.get("visibility_override"):
                        entry["visibility_override"] = True
                    elif "visibility_override" in submitted:
                        entry.pop("visibility_override", None)
                if body.manual_order:
                    entry["manual_order"] = index
                else:
                    entry.pop("manual_order", None)
                updated_media.append(entry)

            before = {
                key: row[key]
                for key in ("nickname", "district", "price_amount", "price_currency", "description")
            }
            before["media"] = old_media
            after = {
                "nickname": body.nickname,
                "district": body.district,
                "price_amount": body.price_amount,
                "price_currency": body.price_currency,
                "description": body.description,
                "media": updated_media,
            }
            manual_fields = _decode(row["manual_fields"], {})
            edited_values = {
                "nickname": body.nickname.strip(),
                "district": body.district.strip(),
                "price_amount": body.price_amount,
                "price_currency": body.price_currency.strip(),
                "description": body.description,
            }
            for field, value in edited_values.items():
                if value != row[field]:
                    manual_fields[field] = True
            if body.manual_order or any(entry.get("visibility_override") for entry in updated_media):
                manual_fields["media"] = True
            else:
                manual_fields.pop("media", None)
            encoded = json.dumps(updated_media, ensure_ascii=False)
            await conn.execute(
                "UPDATE listings SET nickname=$2, district=$3, price_amount=$4, "
                "price_currency=$5, description=$6, media_json=$7::jsonb, "
                "manual_fields=$8::jsonb, updated_at=NOW() WHERE id=$1",
                listing_id, body.nickname.strip(), body.district.strip(),
                body.price_amount, body.price_currency.strip(), body.description,
                encoded, json.dumps(manual_fields),
            )
            if row["source_msg_id"] is not None:
                await conn.execute(
                    "UPDATE raw_messages SET media_json=$2::jsonb WHERE id=$1",
                    row["source_msg_id"], encoded,
                )
            await conn.execute(
                "INSERT INTO listing_edit_history "
                "(listing_id, editor_user_id, before_json, after_json) "
                "VALUES ($1, $2, $3::jsonb, $4::jsonb)",
                listing_id, current_user_id(authorization),
                json.dumps(before, ensure_ascii=False, default=str),
                json.dumps(after, ensure_ascii=False, default=str),
            )
    return {"ok": True}


@router.post("/{listing_id}/rotate-photo")
async def rotate_photo(listing_id: int, body: RotatePhotoBody) -> dict:
    """Create a rotated photo copy and update only its listing media entry."""
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    media_root = Path(get_settings().media_dir).resolve()
    source = (media_root / body.path).resolve()
    if not source.is_relative_to(media_root) or not source.is_file():
        raise HTTPException(status_code=404, detail="photo not found")

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT l.source_msg_id, l.media_json "
                "FROM listings l JOIN raw_messages rm ON rm.id = l.source_msg_id "
                "WHERE l.id = $1 FOR UPDATE OF rm",
                listing_id,
            )
            if row is None:
                raise HTTPException(status_code=404, detail="listing not found")
            media = _decode(row["media_json"], [])
            item = next(
                (entry for entry in media if entry.get("kind") == "photo" and entry.get("path") == body.path),
                None,
            )
            if item is None:
                raise HTTPException(status_code=404, detail="photo is not part of this listing")

            target = source.with_name(f"{source.stem}_manual_{uuid.uuid4().hex[:8]}.jpg")
            try:
                with Image.open(source) as image:
                    image.rotate(-90 if body.clockwise else 90, expand=True).convert("RGB").save(
                        target, "JPEG", quality=95,
                    )
            except Exception as exc:
                raise HTTPException(status_code=422, detail="photo could not be rotated") from exc

            item["path"] = str(target.relative_to(media_root))
            item["orientation"] = "manual"
            item["orientation_checked"] = True
            for key in ("focus_x", "focus_y", "score"):
                item.pop(key, None)
            encoded = json.dumps(media, ensure_ascii=False)
            await conn.execute(
                "UPDATE listings SET media_json=$2::jsonb WHERE id=$1",
                listing_id, encoded,
            )
            await conn.execute(
                "UPDATE raw_messages SET media_json=$2::jsonb WHERE id=$1",
                row["source_msg_id"], encoded,
            )
    return {"media": media}


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
