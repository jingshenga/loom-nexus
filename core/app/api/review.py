"""Admin review workbench APIs (token-protected)."""
import base64
import binascii
import io
import json
import re
import uuid
from datetime import date
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
SQL_REVIEW_DETAIL = sqlres.load("review_detail.sql")
SQL_DUPLICATE_CANDIDATES = sqlres.load("duplicate_candidates.sql")
SQL_DUPLICATE_IDENTITY_GROUPS = sqlres.load("duplicate_identity_groups.sql")
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
SQL_HISTORY_SOURCES = sqlres.load("history_library_sources.sql")
SQL_HISTORY_MESSAGES = sqlres.load("history_library_messages.sql")
SQL_HISTORY_TOTAL = sqlres.load("history_library_total.sql")

WORKBENCH_ATTRIBUTE_KEYS = {
    "age_years", "height_cm", "weight_kg", "bust_size", "price_1p_text",
    "price_2p_text", "service_text", "features_text", "restriction_text", "fixed_intro_terms",
}


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


def _publishable_media_count(media: list) -> int:
    return sum(
        1 for item in media
        if item.get("kind") in {"photo", "video"}
        and (item.get("path") or item.get("poster"))
        and item.get("visibility", "public") == "public"
        and item.get("ocr_classification") not in {"info_card", "uncertain"}
        and item.get("ocr_review_status", "not_required") != "pending"
    )


def _save_uploaded_photo(image_data: str, image_mime: str) -> dict:
    if len(image_data) > 14_000_000 or image_mime not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(status_code=400, detail="image must be JPEG, PNG or WebP under 10 MB")
    try:
        raw = base64.b64decode(image_data, validate=True)
        if len(raw) > 10 * 1024 * 1024:
            raise ValueError("image too large")
        image = Image.open(io.BytesIO(raw))
        if image.width * image.height > 40_000_000:
            raise ValueError("image dimensions are too large")
        image.verify()
        formats = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}
        if image.format != formats[image_mime]:
            raise ValueError("image type does not match content")
    except (ValueError, binascii.Error, OSError, Image.DecompressionBombError) as exc:
        raise HTTPException(status_code=400, detail="invalid image") from exc
    target_dir = Path(get_settings().media_dir) / "manual"
    target_dir.mkdir(parents=True, exist_ok=True)
    extension = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[image_mime]
    filename = f"admin_{uuid.uuid4().hex}{extension}"
    (target_dir / filename).write_bytes(raw)
    return {
        "kind": "photo", "path": f"manual/{filename}", "visibility": "admin",
        "visibility_override": True,
    }


@router.get("/queue")
async def queue(
    status: str = "pending", limit: int = 30, offset: int = 0,
    nickname: str = None, source: str = None, media: str = None,
    sort: Literal["newest", "oldest"] = "newest", bucket: str = None,
    collected_from: date | None = None, collected_to: date | None = None,
    original_from: date | None = None, original_to: date | None = None,
    sort_by: Literal["original", "collected", "media"] = "original",
    sort_then: Literal["none", "original", "collected", "media"] = "none",
    min_media: int | None = None, max_media: int | None = None,
) -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    if media not in {None, "photo", "video"}:
        raise HTTPException(status_code=400, detail="invalid media filter")
    if min_media is not None and min_media < 0 or max_media is not None and max_media < 0:
        raise HTTPException(status_code=400, detail="media count must be non-negative")
    if min_media is not None and max_media is not None and min_media > max_media:
        raise HTTPException(status_code=400, detail="minimum media count exceeds maximum")
    if collected_from and collected_to and collected_from > collected_to:
        raise HTTPException(status_code=400, detail="collection date range is invalid")
    if original_from and original_to and original_from > original_to:
        raise HTTPException(status_code=400, detail="original message date range is invalid")
    rows = await pool.fetch(
        SQL_QUEUE, status, limit, offset, nickname or None,
        source or None, media, sort, bucket or None,
        collected_from, collected_to, min_media, max_media,
        original_from, original_to, sort_by, sort_then,
    )
    items = []
    for r in rows:
        media = _decode(r["media_json"], [])
        cover = next((entry for entry in media if entry.get("kind") == "photo"), None)
        if cover is None:
            cover = next((entry for entry in media if entry.get("kind") == "video" and entry.get("poster")), None)
        items.append({
            "id": r["id"],
            "nickname": r["nickname"],
            "district": r["district"],
            "price_amount": float(r["price_amount"]) if r["price_amount"] is not None else None,
            "price_currency": r["price_currency"],
            "description": r["description"],
            "custom_fields": _decode(r["custom_fields"], {}),
            "media": [cover] if cover else [],
            "media_count": len(media),
            "evidence": _decode(r["evidence_json"], {}),
            "confidence": float(r["confidence"]) if r["confidence"] is not None else None,
            "status": r["status"],
            "contact_peer_id": r["contact_peer_id"],
            "contact_username": r["contact_username"],
            "bot_url": r["bot_url"],
            "created_at": r["created_at"].isoformat(),
            "source_text": r["raw_text"],
            "source_title": r["source_title"],
            "source_posted_at": r["raw_posted_at"].isoformat() if r["raw_posted_at"] else None,
        })
    return {"items": items}


@router.get("/queue/{listing_id}")
async def review_detail(listing_id: int) -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    row = await pool.fetchrow(SQL_REVIEW_DETAIL, listing_id)
    if row is None:
        raise HTTPException(status_code=404, detail="review card not found")
    return {
        "id": row["id"], "nickname": row["nickname"], "district": row["district"],
        "price_amount": float(row["price_amount"]) if row["price_amount"] is not None else None,
        "price_currency": row["price_currency"], "description": row["description"],
        "custom_fields": _decode(row["custom_fields"], {}),
        "media": _decode(row["media_json"], []),
        "effective_media_count": _publishable_media_count(_decode(row["media_json"], [])),
        "evidence": _decode(row["evidence_json"], {}),
        "confidence": float(row["confidence"]) if row["confidence"] is not None else None,
        "status": row["status"], "contact_peer_id": row["contact_peer_id"],
        "contact_username": row["contact_username"], "bot_url": row["bot_url"],
        "created_at": row["created_at"].isoformat(), "source_text": row["raw_text"],
        "source_title": row["source_title"],
        "source_posted_at": row["raw_posted_at"].isoformat() if row["raw_posted_at"] else None,
    }


@router.get("/duplicates")
async def duplicate_candidates() -> dict:
    """List cards with matching Telegram identities, profile content, or history nicknames."""
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

    return {"items": [{"left": card(row, "left"), "right": card(row, "right"),
                        "match_reason": row["match_reason"]}
                       for row in rows]}


@router.get("/duplicates/identity-groups")
async def duplicate_identity_groups() -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    rows = await pool.fetch(SQL_DUPLICATE_IDENTITY_GROUPS)
    groups = {}
    for row in rows:
        group = groups.setdefault(row["identity_key"], {
            "identity_key": row["identity_key"],
            "contact_peer_id": row["contact_peer_id"],
            "contact_username": row["contact_username"], "cards": [],
        })
        group["cards"].append({
            "id": row["id"], "status": row["status"], "nickname": row["nickname"],
            "contact_username": row["contact_username"], "contact_peer_id": row["contact_peer_id"],
            "collected_at": row["created_at"].isoformat(),
            "posted_at": row["posted_at"].isoformat() if row["posted_at"] else None,
            "source_title": row["source_title"],
            "person_group_id": row["person_group_id"],
        })
    return {"items": list(groups.values())}


@router.get("/person-groups")
async def person_groups() -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    rows = await pool.fetch(
        "SELECT g.id AS group_id, g.display_name, l.id, l.nickname, l.status, "
        "l.contact_username, l.created_at, rm.posted_at, s.title AS source_title "
        "FROM listing_person_groups g "
        "JOIN listing_person_group_members m ON m.group_id=g.id "
        "JOIN listings l ON l.id=m.listing_id "
        "LEFT JOIN raw_messages rm ON rm.id=l.source_msg_id "
        "LEFT JOIN ingest_sources s ON s.id=rm.source_id "
        "ORDER BY g.id, rm.posted_at DESC NULLS LAST, l.created_at DESC, l.id DESC"
    )
    groups = {}
    for row in rows:
        group = groups.setdefault(row["group_id"], {
            "id": row["group_id"], "display_name": row["display_name"], "members": [],
        })
        group["members"].append({
            "id": row["id"], "nickname": row["nickname"], "status": row["status"],
            "contact_username": row["contact_username"],
            "collected_at": row["created_at"].isoformat(),
            "posted_at": row["posted_at"].isoformat() if row["posted_at"] else None,
            "source_title": row["source_title"],
        })
    return {"items": list(groups.values())}


@router.delete("/person-groups/{group_id}/members/{listing_id}")
async def remove_person_group_member(group_id: int, listing_id: int) -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    async with pool.acquire() as conn:
        async with conn.transaction():
            deleted = await conn.fetchval(
                "DELETE FROM listing_person_group_members "
                "WHERE group_id=$1 AND listing_id=$2 RETURNING listing_id",
                group_id, listing_id,
            )
            if deleted is None:
                raise HTTPException(status_code=404, detail="group member not found")
            remaining = await conn.fetchval(
                "SELECT count(*) FROM listing_person_group_members WHERE group_id=$1", group_id,
            )
            if remaining < 2:
                await conn.execute("DELETE FROM listing_person_groups WHERE id=$1", group_id)
    return {"ok": True}


@router.post("/duplicates/{first_id}/{second_id}/group")
async def group_duplicate_cards(first_id: int, second_id: int) -> dict:
    """Confirm two candidate cards are one person and join their groups."""
    if first_id == second_id:
        raise HTTPException(status_code=400, detail="two different cards are required")
    left_id, right_id = sorted((first_id, second_id))
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                "SELECT l.id,l.nickname,l.district,l.price_amount,l.price_currency,"
                "l.contact_peer_id,l.contact_username,l.profile_collection_job_id,l.source_msg_id,"
                "COALESCE(NULLIF(rm.raw_text,''),l.description,'') AS source_text "
                "FROM listings l LEFT JOIN raw_messages rm ON rm.id=l.source_msg_id "
                "WHERE l.id=ANY($1::bigint[]) ORDER BY l.id FOR UPDATE OF l",
                [left_id, right_id],
            )
            if len(rows) != 2:
                raise HTTPException(status_code=404, detail="card not found")
            first, second = rows
            same_identity = (
                first["contact_peer_id"] is not None
                and first["contact_peer_id"] == second["contact_peer_id"]
            ) or (
                bool(first["contact_username"] and second["contact_username"])
                and first["contact_username"].casefold() == second["contact_username"].casefold()
            )
            def signature(value: str) -> str:
                value = re.sub(r"https?://\S+|@[A-Za-z0-9_]+", " ", value, flags=re.I)
                return re.sub(r"\s+", "", value.casefold())
            same_profile = (
                first["nickname"] and second["nickname"]
                and first["nickname"].strip().casefold() == second["nickname"].strip().casefold()
                and first["district"] == second["district"]
                and first["price_amount"] == second["price_amount"]
                and first["price_currency"] == second["price_currency"]
                and signature(first["source_text"]) == signature(second["source_text"])
                and signature(first["source_text"]) != ""
                and not same_identity
            )
            history_candidate = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM raw_messages rm "
                "JOIN checkin_history_scans hs ON hs.source_id=rm.source_id "
                "WHERE rm.id=ANY($1::bigint[]))",
                [first["source_msg_id"], second["source_msg_id"]],
            )
            same_nickname = (
                (first["profile_collection_job_id"] is not None
                 or second["profile_collection_job_id"] is not None or history_candidate)
                and first["nickname"] and second["nickname"]
                and re.sub(r"[\s\W_]+", "", first["nickname"].casefold())
                    == re.sub(r"[\s\W_]+", "", second["nickname"].casefold())
            )
            if not (same_identity or same_profile or same_nickname):
                raise HTTPException(status_code=409, detail="cards are not current duplicate candidates")

            group_ids = await conn.fetch(
                "SELECT DISTINCT group_id FROM listing_person_group_members "
                "WHERE listing_id=ANY($1::bigint[]) ORDER BY group_id",
                [left_id, right_id],
            )
            if not group_ids:
                group_id = await conn.fetchval(
                    "INSERT INTO listing_person_groups (display_name) VALUES($1) RETURNING id",
                    first["nickname"] or second["nickname"] or "",
                )
            else:
                group_id = group_ids[0]["group_id"]
                for other in group_ids[1:]:
                    await conn.execute(
                        "UPDATE listing_person_group_members SET group_id=$1 WHERE group_id=$2",
                        group_id, other["group_id"],
                    )
                    await conn.execute("DELETE FROM listing_person_groups WHERE id=$1", other["group_id"])
            await conn.execute(
                "INSERT INTO listing_person_group_members (group_id,listing_id) "
                "VALUES($1,$2),($1,$3) ON CONFLICT(listing_id) DO UPDATE SET group_id=EXCLUDED.group_id",
                group_id, left_id, right_id,
            )
            member_ids = await conn.fetch(
                "SELECT listing_id FROM listing_person_group_members WHERE group_id=$1 ORDER BY listing_id",
                group_id,
            )
    return {"ok": True, "group_id": group_id, "member_ids": [row["listing_id"] for row in member_ids]}


class DuplicateDecision(BaseModel):
    decision: Literal["keep_latest", "keep_selected", "merge_selected", "keep_both"]
    keep_listing_id: int | None = None


class PersonGroupConfirmBody(BaseModel):
    listing_ids: list[int]


@router.post("/person-groups/confirm")
async def confirm_identity_group(body: PersonGroupConfirmBody) -> dict:
    listing_ids = sorted(set(body.listing_ids))
    if len(listing_ids) < 2 or len(listing_ids) > 300:
        raise HTTPException(status_code=400, detail="choose between 2 and 300 cards")
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                "SELECT id,nickname,contact_username,contact_peer_id "
                "FROM listings WHERE id=ANY($1::bigint[]) ORDER BY id FOR UPDATE",
                listing_ids,
            )
            if len(rows) != len(listing_ids):
                raise HTTPException(status_code=404, detail="one or more cards were not found")
            usernames = {row["contact_username"].strip().casefold() for row in rows
                         if row["contact_username"] and row["contact_username"].strip()}
            if len(usernames) != 1 or any(
                row["contact_username"] is None
                or row["contact_username"].strip().casefold() not in usernames
                for row in rows
            ):
                raise HTTPException(status_code=409, detail="cards do not share one exact Telegram username")
            group_ids = await conn.fetch(
                "SELECT DISTINCT group_id FROM listing_person_group_members "
                "WHERE listing_id=ANY($1::bigint[]) ORDER BY group_id",
                listing_ids,
            )
            if group_ids:
                group_id = group_ids[0]["group_id"]
                for other in group_ids[1:]:
                    await conn.execute(
                        "UPDATE listing_person_group_members SET group_id=$1 WHERE group_id=$2",
                        group_id, other["group_id"],
                    )
                    await conn.execute("DELETE FROM listing_person_groups WHERE id=$1", other["group_id"])
            else:
                group_id = await conn.fetchval(
                    "INSERT INTO listing_person_groups (display_name) VALUES($1) RETURNING id",
                    rows[0]["nickname"] or "",
                )
            await conn.executemany(
                "INSERT INTO listing_person_group_members (group_id,listing_id) VALUES($1,$2) "
                "ON CONFLICT(listing_id) DO UPDATE SET group_id=EXCLUDED.group_id",
                [(group_id, listing_id) for listing_id in listing_ids],
            )
    return {"ok": True, "group_id": group_id, "member_ids": listing_ids}


@router.post("/duplicates/{first_id}/{second_id}")
async def decide_duplicate(
    first_id: int, second_id: int, body: DuplicateDecision,
    authorization: str = Header(default=""),
) -> dict:
    """Record an admin decision; retaining the latest archives the older card."""
    if first_id == second_id:
        raise HTTPException(status_code=400, detail="two different cards are required")
    if body.decision in {"keep_selected", "merge_selected"} and body.keep_listing_id not in {first_id, second_id}:
        raise HTTPException(status_code=400, detail="selected card must belong to this duplicate pair")
    if body.decision not in {"keep_selected", "merge_selected"} and body.keep_listing_id is not None:
        raise HTTPException(status_code=400, detail="keep_listing_id is only valid for a selected-card decision")
    left_id, right_id = sorted((first_id, second_id))
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")

    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                "SELECT l.id, l.status, l.created_at, l.nickname, l.district, "
                "l.price_amount, l.price_currency, l.contact_peer_id, l.profile_collection_job_id, "
                "l.contact_username, l.source_msg_id, l.bot_url, l.description, l.custom_fields, "
                "l.media_json, l.manual_fields, "
                "COALESCE(NULLIF(rm.raw_text, ''), l.description, '') AS source_text "
                "FROM listings l LEFT JOIN raw_messages rm ON rm.id=l.source_msg_id "
                "WHERE l.id = ANY($1::bigint[]) ORDER BY l.id FOR UPDATE OF l",
                [left_id, right_id],
            )
            if len(rows) != 2:
                raise HTTPException(status_code=404, detail="card not found")
            if any(row["status"] not in {"published", "pending", "duplicate_pending", "archived"} for row in rows):
                raise HTTPException(status_code=409, detail="cards are no longer active")

            def signature(value: str) -> str:
                value = re.sub(r"https?://\S+|@[A-Za-z0-9_]+", " ", value, flags=re.I)
                return re.sub(r"\s+", "", value.casefold())

            first, second = rows
            same_identity = (
                first["contact_peer_id"] is not None
                and first["contact_peer_id"] == second["contact_peer_id"]
            ) or (
                bool(first["contact_username"] and second["contact_username"])
                and first["contact_username"].casefold() == second["contact_username"].casefold()
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
            history_candidate = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM raw_messages rm "
                "JOIN checkin_history_scans hs ON hs.source_id=rm.source_id "
                "WHERE rm.id = ANY($1::bigint[]))",
                [first["source_msg_id"], second["source_msg_id"]],
            )
            same_nickname = (
                (first["profile_collection_job_id"] is not None
                 or second["profile_collection_job_id"] is not None
                 or history_candidate)
                and first["nickname"] is not None
                and second["nickname"] is not None
                and re.sub(r"[\s\W_]+", "", first["nickname"].casefold())
                    == re.sub(r"[\s\W_]+", "", second["nickname"].casefold())
            )
            if not (same_identity or same_nickname or same_profile):
                raise HTTPException(status_code=409, detail="cards are not a current duplicate candidate")

            merge_info = {"fields_filled": [], "conflicts": [], "media_added": 0}
            if body.decision == "merge_selected":
                target_id = body.keep_listing_id
                source_id = second_id if target_id == first_id else first_id
                target = next(row for row in rows if row["id"] == target_id)
                source = next(row for row in rows if row["id"] == source_id)
                if target["status"] == "archived":
                    raise HTTPException(status_code=409, detail="an archived card cannot be the merge target")

                scalar_fields = (
                    ("contact_peer_id", "TG 数字 ID"), ("contact_username", "TG号"),
                    ("nickname", "昵称"), ("district", "区域"),
                    ("price_amount", "价格"), ("price_currency", "价格币种"),
                    ("description", "介绍"), ("bot_url", "Bot地址"),
                )
                updates = {}
                manual_fields = _decode(target["manual_fields"], {})
                for field, label in scalar_fields:
                    target_value, source_value = target[field], source[field]
                    target_empty = target_value is None or (isinstance(target_value, str) and not target_value.strip())
                    source_empty = source_value is None or (isinstance(source_value, str) and not source_value.strip())
                    if target_empty and not source_empty:
                        updates[field] = source_value
                        manual_fields[field] = True
                        merge_info["fields_filled"].append(label)
                    elif not target_empty and not source_empty and target_value != source_value:
                        # A default CNY value should not block a missing price from its source card.
                        if field == "price_currency" and target["price_amount"] is None and source["price_amount"] is not None:
                            updates[field] = source_value
                            manual_fields[field] = True
                            if label not in merge_info["fields_filled"]:
                                merge_info["fields_filled"].append(label)
                        else:
                            merge_info["conflicts"].append(label)

                target_custom = _decode(target["custom_fields"], {})
                source_custom = _decode(source["custom_fields"], {})
                merged_custom = dict(target_custom)
                for key, source_value in source_custom.items():
                    target_value = target_custom.get(key)
                    target_empty = target_value is None or (isinstance(target_value, str) and not target_value.strip())
                    source_empty = source_value is None or (isinstance(source_value, str) and not source_value.strip())
                    if target_empty and not source_empty:
                        merged_custom[key] = source_value
                        merge_info["fields_filled"].append(f"自定义字段：{key}")
                    elif not target_empty and not source_empty and target_value != source_value:
                        merge_info["conflicts"].append(f"自定义字段：{key}")
                if merged_custom != target_custom:
                    updates["custom_fields"] = json.dumps(merged_custom, ensure_ascii=False)
                    manual_fields["custom_fields"] = True

                target_media = _decode(target["media_json"], [])
                source_media = _decode(source["media_json"], [])

                def media_key(item):
                    for key in ("file_unique_id", "file_id", "path"):
                        value = item.get(key)
                        if value:
                            return (key, str(value))
                    return ("json", json.dumps(item, sort_keys=True, ensure_ascii=False))

                existing_media = {media_key(item) for item in target_media}
                merged_media = list(target_media)
                for item in source_media:
                    key = media_key(item)
                    if key in existing_media:
                        continue
                    imported = dict(item)
                    imported.pop("manual_order", None)
                    merged_media.append(imported)
                    existing_media.add(key)
                    merge_info["media_added"] += 1
                if merge_info["media_added"]:
                    updates["media_json"] = json.dumps(merged_media, ensure_ascii=False)
                    manual_fields["media"] = True

                if manual_fields != _decode(target["manual_fields"], {}):
                    updates["manual_fields"] = json.dumps(manual_fields, ensure_ascii=False)
                if updates:
                    assignments = []
                    values = [target_id]
                    for field, value in updates.items():
                        values.append(value)
                        cast = "::jsonb" if field in {"custom_fields", "media_json", "manual_fields"} else ""
                        assignments.append(f"{field}=${len(values)}{cast}")
                    await conn.execute(
                        f"UPDATE listings SET {', '.join(assignments)}, updated_at=NOW() WHERE id=$1",
                        *values,
                    )
                if target["status"] == "duplicate_pending":
                    await conn.execute(
                        "UPDATE listings SET status='pending', updated_at=NOW() WHERE id=$1",
                        target_id,
                    )

            latest = max(rows, key=lambda row: (row["created_at"], row["id"]))
            archived_id = None
            kept_id = latest["id"] if body.decision == "keep_latest" else body.keep_listing_id
            if body.decision in {"keep_latest", "keep_selected", "merge_selected"}:
                kept_id = kept_id or latest["id"]
                archived_id = next(row["id"] for row in rows if row["id"] != kept_id)
                await conn.execute(
                    "UPDATE listings SET status='archived', updated_at=NOW() "
                    "WHERE id=$1 AND status IN ('published', 'pending', 'duplicate_pending')",
                    archived_id,
                )
                await conn.execute(
                    "UPDATE listings SET status='pending', updated_at=NOW() "
                    "WHERE id=$1 AND status='duplicate_pending'",
                    kept_id,
                )
            else:
                await conn.execute(
                    "UPDATE listings SET status='pending', updated_at=NOW() "
                    "WHERE id = ANY($1::bigint[]) AND status='duplicate_pending'",
                    [left_id, right_id],
                )
            await conn.execute(
                "INSERT INTO listing_duplicate_decisions "
                "(left_listing_id, right_listing_id, decision, decided_by, kept_listing_id) "
                "VALUES ($1, $2, $3, $4, $5) "
                "ON CONFLICT (left_listing_id, right_listing_id) DO UPDATE SET "
                "decision=EXCLUDED.decision, decided_by=EXCLUDED.decided_by, "
                "kept_listing_id=EXCLUDED.kept_listing_id, decided_at=NOW()",
                left_id, right_id, body.decision, current_user_id(authorization),
                kept_id if body.decision in {"keep_latest", "keep_selected", "merge_selected"} else None,
            )
    return {"ok": True, "decision": body.decision,
            "preferred_id": kept_id if body.decision != "keep_both" else None,
            "archived_id": archived_id, **merge_info}


@router.get("/history/sources")
async def history_library_sources() -> dict:
    """Return the two configured check-in history pools for browsing."""
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    rows = await pool.fetch(SQL_HISTORY_SOURCES)
    return {"items": [dict(row) for row in rows]}


@router.get("/history")
async def history_library(
    source_id: int | None = None, q: str = "",
    limit: int = 50, offset: int = 0,
) -> dict:
    """Search only locally stored messages from configured check-in history sources."""
    if not 1 <= limit <= 100 or offset < 0:
        raise HTTPException(status_code=400, detail="invalid paging parameters")
    q = q.strip()
    if len(q) > 120:
        raise HTTPException(status_code=400, detail="search text is too long")
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    total = await pool.fetchval(SQL_HISTORY_TOTAL, source_id, q or None)
    rows = await pool.fetch(SQL_HISTORY_MESSAGES, source_id, q or None, limit, offset)
    items = []
    for row in rows:
        item = dict(row)
        for key, value in item.items():
            if hasattr(value, "isoformat"):
                item[key] = value.isoformat()
        item["media"] = _decode(item.pop("media_json"), [])
        items.append(item)
    return {"items": items, "total": total}


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
    contact_username: str | None = None
    bot_url: str | None = None
    custom_fields: dict | None = None
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


@router.post("/bot-recollect")
async def bot_recollect(body: RecollectBody) -> dict:
    """Queue Bot-only profile reads for selected card identities."""
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    ids = sorted(set(body.ids))
    if not ids or len(ids) > 20:
        raise HTTPException(status_code=400, detail="select between 1 and 20 cards")
    jobs = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            for listing_id in ids:
                target = await conn.fetchrow(
                    "SELECT l.contact_username, s.id AS source_id, s.assigned_account_id AS account_id, r.id AS rule_id "
                    "FROM listings l "
                    "LEFT JOIN raw_messages rm ON rm.id=l.source_msg_id "
                    "LEFT JOIN profile_collection_jobs old_job ON old_job.id=l.profile_collection_job_id "
                    "LEFT JOIN duty_collection_jobs duty_job ON duty_job.id=l.duty_collection_job_id "
                    "JOIN ingest_sources s ON s.id=COALESCE(rm.source_id,old_job.source_id,duty_job.source_id) AND s.is_active "
                    "JOIN bridge_accounts a ON a.id=s.assigned_account_id AND a.status='active' "
                    "JOIN source_rule_bindings b ON b.source_id=s.id "
                    "JOIN ingest_rules r ON r.id=b.rule_id AND r.rule_type='bot_collection' AND r.is_active "
                    "WHERE l.id=$1 AND NULLIF(l.contact_username,'') IS NOT NULL "
                    "ORDER BY b.priority DESC,r.id LIMIT 1",
                    listing_id,
                )
                if target is None:
                    raise HTTPException(
                        status_code=409,
                        detail=f"卡片 #{listing_id} 没有可用 TG 号、Bot 规则或采集账号",
                    )
                row = await conn.fetchrow(
                    "INSERT INTO profile_collection_jobs "
                    "(rule_id,duty_date,source_id,account_id,contact_username,target_peer_id) "
                    "VALUES($1,(NOW() AT TIME ZONE 'Asia/Shanghai')::date,$2,$3,$4,NULL) "
                    "ON CONFLICT (rule_id,duty_date,lower(contact_username)) WHERE target_msg_id IS NULL "
                    "DO UPDATE SET status=CASE WHEN profile_collection_jobs.status IN ('pending','processing','deferred') "
                    "THEN profile_collection_jobs.status ELSE 'pending' END, "
                    "next_attempt_at=CASE WHEN profile_collection_jobs.status='deferred' "
                    "THEN profile_collection_jobs.next_attempt_at ELSE NULL END, error=NULL, updated_at=NOW() "
                    "RETURNING id,status",
                    target["rule_id"], target["source_id"], target["account_id"], target["contact_username"],
                )
                jobs.append({"job_id": row["id"], "listing_id": listing_id, "status": row["status"]})
    return {"jobs": jobs, "queued": len(jobs)}


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


def _normalize_bot_url(value: str | None) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    match = re.fullmatch(
        r"(?:(?:https?://)?(?:www\.)?t\.me/)?@?([A-Za-z0-9_]{1,32})",
        value,
        re.I,
    )
    if not match:
        raise HTTPException(status_code=400, detail="Bot 地址请填写 Telegram 用户名")
    return f"https://t.me/{match.group(1)}"


@router.patch("/duty-recovery/{job_id}")
async def update_duty_recovery(job_id: int, body: DutyRecoveryUpdate) -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    bot_url = _normalize_bot_url(body.bot_url) or None
    links = [value.strip() if value else None for value in (body.profile_url, body.channel_url, bot_url)]
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
    media: list[dict] | None = None
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
    media = [_save_uploaded_photo(item.get("image_data", ""), item.get("image_mime", ""))
             for item in (body.media or []) if item.get("image_data")]
    if body.image_data:
        media.append(_save_uploaded_photo(body.image_data, body.image_mime))

    source_url = body.source_url.strip()
    bot_url = _normalize_bot_url(body.bot_url) or recovery["bot_url"] or ""
    for label, value in (("source_url", source_url),):
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
        body.description, json.dumps(media), json.dumps(evidence), bot_url, job_id,
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
                "SELECT id, source_msg_id, nickname, district, price_amount, contact_username, "
                "price_currency, description, bot_url, custom_fields, media_json, manual_fields FROM listings "
                "WHERE id=$1 FOR UPDATE",
                listing_id,
            )
            if row is None:
                raise HTTPException(status_code=404, detail="listing not found")
            old_media = _decode(row["media_json"], [])
            contact_username = row["contact_username"]
            if body.contact_username is not None:
                contact_username = body.contact_username.strip().lstrip("@").lower()
                if contact_username and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", contact_username):
                    raise HTTPException(status_code=400, detail="TG号格式无效")
            bot_url = _normalize_bot_url(body.bot_url) if body.bot_url is not None else row["bot_url"]
            custom_fields = body.custom_fields if body.custom_fields is not None else _decode(row["custom_fields"], {})
            allowed_fields = await conn.fetch(
                "SELECT field_key FROM listing_field_definitions WHERE is_active"
            )
            allowed_keys = {item["field_key"] for item in allowed_fields} | WORKBENCH_ATTRIBUTE_KEYS
            if set(custom_fields) - allowed_keys:
                raise HTTPException(status_code=422, detail="卡片包含未定义的自定义字段")
            old_by_path = {entry.get("path"): entry for entry in old_media}
            paths = [entry.get("path") for entry in body.media if entry.get("path")]
            if len(set(paths)) != len(paths) or any(path not in old_by_path for path in paths):
                raise HTTPException(status_code=400, detail="invalid media path")

            updated_media = []
            for index, submitted in enumerate(body.media):
                if submitted.get("path") in old_by_path:
                    entry = dict(old_by_path[submitted["path"]])
                elif submitted.get("image_data"):
                    entry = _save_uploaded_photo(
                        submitted.get("image_data", ""), submitted.get("image_mime", ""),
                    )
                else:
                    raise HTTPException(status_code=400, detail="new media must be an uploaded image")
                if entry.get("kind") == "photo":
                    visibility = submitted.get("visibility", entry.get("visibility", "admin"))
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
            before["contact_username"] = row["contact_username"]
            before["bot_url"] = row["bot_url"]
            before["custom_fields"] = _decode(row["custom_fields"], {})
            after = {
                "nickname": body.nickname,
                "district": body.district,
                "price_amount": body.price_amount,
                "price_currency": body.price_currency,
                "description": body.description,
                "contact_username": contact_username,
                "bot_url": bot_url,
                "custom_fields": custom_fields,
                "media": updated_media,
            }
            manual_fields = _decode(row["manual_fields"], {})
            edited_values = {
                "nickname": body.nickname.strip(),
                "district": body.district.strip(),
                "price_amount": body.price_amount,
                "price_currency": body.price_currency.strip(),
                "description": body.description,
                "contact_username": contact_username,
            }
            for field, value in edited_values.items():
                if value != row[field]:
                    manual_fields[field] = True
            if bot_url != row["bot_url"]:
                manual_fields["bot_url"] = True
            if custom_fields != _decode(row["custom_fields"], {}):
                manual_fields["custom_fields"] = True
            if body.manual_order or any(entry.get("visibility_override") for entry in updated_media):
                manual_fields["media"] = True
            else:
                manual_fields.pop("media", None)
            encoded = json.dumps(updated_media, ensure_ascii=False)
            await conn.execute(
                "UPDATE listings SET nickname=$2, district=$3, price_amount=$4, "
                "price_currency=$5, description=$6, contact_username=$7, bot_url=$8, custom_fields=$9::jsonb, "
                "media_json=$10::jsonb, manual_fields=$11::jsonb, updated_at=NOW() WHERE id=$1",
                listing_id, body.nickname.strip(), body.district.strip(),
                body.price_amount, body.price_currency.strip(), body.description, contact_username,
                bot_url, json.dumps(custom_fields, ensure_ascii=False), encoded, json.dumps(manual_fields),
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


def missing_publish_fields(listing, custom_fields, required_fields) -> list[str]:
    missing = [
        label for label, value in (
            ("昵称", listing["nickname"]), ("价格", listing["price_amount"]),
            ("介绍", listing["description"]), ("TG号", listing["contact_username"]),
        ) if value is None or (isinstance(value, str) and not value.strip())
    ]
    missing.extend(
        row["label"] for row in required_fields
        if not str(custom_fields.get(row["field_key"]) or "").strip()
    )
    return missing


@router.post("/{listing_id}/publish")
async def publish(listing_id: int) -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    listing = await pool.fetchrow(
        "SELECT nickname,price_amount,description,contact_username,custom_fields "
        "FROM listings WHERE id=$1",
        listing_id,
    )
    if listing is None:
        raise HTTPException(status_code=404, detail="listing not found")
    required_fields = await pool.fetch(
        "SELECT field_key,label FROM listing_field_definitions "
        "WHERE is_active AND required_for_publish ORDER BY field_key"
    )
    missing = missing_publish_fields(listing, _decode(listing["custom_fields"], {}), required_fields)
    if missing:
        raise HTTPException(status_code=409, detail="上架条件未满足：" + "、".join(missing))
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
