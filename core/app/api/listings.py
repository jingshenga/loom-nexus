"""Public square APIs: published listings, detail, favorites."""
import re
import time
from collections import defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app import db, sqlres

from app.api.auth import bearer_identity, current_user_id, require_user

router = APIRouter(
    prefix="/api", tags=["public"],
    dependencies=[Depends(require_user)],
)

SQL_LIST = sqlres.load("public_listings.sql")
SQL_COUNT = sqlres.load("public_listings_count.sql")
SQL_DETAIL = sqlres.load("listing_detail.sql")
SQL_DISTRICTS = sqlres.load("districts.sql")
SQL_FAV_LIST = sqlres.load("favorites_list.sql")
SQL_FAV_TOGGLE = sqlres.load("favorite_toggle.sql")
SQL_DISPLAY_RULES = sqlres.load("display_rules.sql")
SQL_OCR_ATTRIBUTES = """
SELECT l.id AS listing_id, o.media_path, o.fields_json, o.evidence_json, o.confidence
FROM listings l
JOIN listing_media_ocr o ON (
    o.listing_id = l.id OR o.raw_id = l.source_msg_id OR o.profile_result_id = (
        SELECT max(r.id) FROM profile_collection_results r
        WHERE r.job_id = l.profile_collection_job_id
    )
)
WHERE l.id = ANY($1::bigint[]) AND o.status = 'done'
  AND COALESCE(o.human_classification, o.classification) = 'info_card'
ORDER BY o.id
"""

# rule-level display filters (regex per source) cached briefly so the
# public endpoints don't re-read bindings on every page view
_DISPLAY_TTL_S = 30.0
_display_cache = {"at": 0.0, "map": {}}


def _decode(value, fallback):
    if value is None:
        return fallback
    if isinstance(value, (dict, list, int, float)):
        return value
    import json

    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


async def _compiled_display_filters(pool) -> dict:
    """{source_id: [compiled regex, ...]} from the bound rule's
    display.exclude_patterns; first (highest-priority) declaring rule
    wins per source. Invalid regexes are skipped, never fatal."""
    now = time.monotonic()
    if now - _display_cache["at"] < _DISPLAY_TTL_S:
        return _display_cache["map"]
    mapping: dict = {}
    try:
        rows = await pool.fetch(SQL_DISPLAY_RULES)
        for r in rows:
            sid = r["source_id"]
            if sid in mapping:
                continue
            cfg = _decode(r["display_cfg"], None) or {}
            compiled = []
            for pattern in cfg.get("exclude_patterns") or []:
                try:
                    compiled.append(re.compile(pattern))
                except re.error:
                    continue
            mapping[sid] = compiled
    except Exception:
        pass  # degrade to unfiltered descriptions
    _display_cache.update(at=now, map=mapping)
    return mapping


def _apply_excludes(text, patterns) -> str:
    """Drop whole description lines matching any exclude pattern; a
    filter that would blank the card entirely is ignored."""
    if not text or not patterns:
        return text
    kept = [
        line for line in text.splitlines()
        if not any(p.search(line) for p in patterns)
    ]
    filtered = "\n".join(kept).strip()
    return filtered or text


def _visible_media(media, is_admin: bool) -> list:
    items = [item for item in media if is_admin or item.get("kind") != "photo"
             or item.get("visibility") == "public"]

    def order(item):
        if isinstance(item.get("manual_order"), int):
            return (0, item["manual_order"], 0, 0)
        kind = 0 if item.get("kind") == "photo" else 1
        visibility = {"public": 0, "pending": 1, "admin": 2}.get(
            item.get("visibility"), 1,
        )
        try:
            score = float(item.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        return (1, kind, visibility, -score)

    return sorted(items, key=order)


def _number(value, minimum, maximum, integer=False):
    if isinstance(value, str):
        value = re.sub(r"\s*(?:岁|厘米|cm|公斤|kg)$", "", value.strip(), flags=re.I)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not minimum <= number <= maximum or (integer and not number.is_integer()):
        return None
    return int(number) if integer else number


def _texts(value):
    values = value if isinstance(value, list) else [value]
    result = []
    for value in values:
        if not isinstance(value, str):
            continue
        clean = re.sub(r"https?://\S+|t\.me/\S+|@[A-Za-z0-9_]{3,}|(?<!\d)1[3-9](?:[\s-]?\d){9}(?!\d)",
                       "[已隐藏]", value.strip(), flags=re.I).strip()
        if clean and clean not in result:
            result.append(clean)
    return result


def _attributes(custom_fields, ocr_entries):
    fields = {}
    for entry in ocr_entries:
        source = _decode(entry["fields_json"], {})
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            if key in {"service_text", "features_text", "restriction_text", "fixed_intro_terms",
                       "price_1p_text", "price_2p_text", "price_text", "cash_text"}:
                fields[key] = [*fields.get(key, []), *_texts(value)]
            elif key not in fields or fields[key] in (None, ""):
                fields[key] = value
    custom = _decode(custom_fields, {})
    if isinstance(custom, dict):
        fields.update(custom)
    profile = {
        "age": _number(fields.get("age_years"), 0, 120, integer=True),
        "height": _number(fields.get("height_cm"), 50, 250, integer=True),
        "weight": _number(fields.get("weight_kg", fields.get("weight_value")), 0, 500),
        "bust": (_texts(fields.get("bust_size")) or [None])[0],
    }
    def merged(*keys):
        result = []
        for key in keys:
            for value in _texts(fields.get(key)):
                if value not in result:
                    result.append(value)
        return result
    return {
        "profile": profile,
        "tariffs": {
            "p1": merged("price_1p_text"), "p2": merged("price_2p_text"),
            "cash_note": merged("cash_text"), "raw_fees": merged("price_text"),
        },
        "services": merged("service_text", "features_text"),
        "restrictions": merged("fixed_intro_terms", "restriction_text"),
    }


async def _ocr_by_listing(pool, rows):
    ids = [row["id"] for row in rows]
    if not ids:
        return {}
    entries = defaultdict(list)
    for row in await pool.fetch(SQL_OCR_ATTRIBUTES, ids):
        entries[row["listing_id"]].append(row)
    return entries


def _row_to_item(row, display_filters=None, is_admin=True, include_full_media=False,
                 ocr_entries=None) -> dict:
    ocr_entries = ocr_entries or []
    patterns = (display_filters or {}).get(row["source_id"])
    tier = row["presence_tier"] if "presence_tier" in row.keys() else None
    # the full description (incl. the card summary line) is admin-only;
    # regular users see fields + media + chat entry, not the raw text
    description = _apply_excludes(row["description"], patterns)
    if not is_admin:
        description = None
    media = _visible_media(_decode(row["media_json"], []), is_admin)
    item = {
        "id": row["id"],
        "nickname": row["nickname"],
        "district": row["district"],
        "price_amount": float(row["price_amount"]) if row["price_amount"] is not None else None,
        "price_currency": row["price_currency"],
        "bot_url": row["bot_url"] if "bot_url" in row.keys() else "",
        "description": description,
        "attributes": _attributes(row["custom_fields"] if "custom_fields" in row.keys() else {}, ocr_entries),
        "media": media if include_full_media else media[:1],
        "media_count": len(media),
        "published_at": row["published_at"].isoformat() if row["published_at"] else None,
        "online": tier == "online",
        "online_tier": tier or "unknown",
        "duty_label": row["duty_label"] if "duty_label" in row.keys() else None,
        "favorited": bool(row["favorited"]) if "favorited" in row.keys() else False,
    }
    if is_admin:
        item["evidence_json"] = _decode(row["evidence_json"], {}) if "evidence_json" in row.keys() else {}
        item["ocr_evidence"] = [{
            "media_path": entry["media_path"],
            "fields": _decode(entry["fields_json"], {}),
            "evidence": _decode(entry["evidence_json"], {}),
            "confidence": float(entry["confidence"]) if entry["confidence"] is not None else None,
        } for entry in ocr_entries]
    return item


@router.get("/listings")
async def list_listings(
    authorization: str = Header(default=""),
    q: str = None,
    district: str = None,
    min_price: float = None,
    max_price: float = None,
    online: bool = None,
    duty: Literal["all", "today", "yesterday"] = "all",
    min_age: int | None = Query(default=None, ge=0, le=120),
    max_age: int | None = Query(default=None, ge=0, le=120),
    restriction: Literal["禁嗑药", "禁无套", "禁醉酒", "禁桩基", "禁拔套", "禁鸽子", "禁变态"] | None = None,
    limit: int = 24,
    offset: int = 0,
) -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    if min_age is not None and max_age is not None and min_age > max_age:
        raise HTTPException(status_code=400, detail="minimum age exceeds maximum")
    rows = await pool.fetch(
        SQL_LIST, q, district, min_price, max_price, limit, offset, online,
        current_user_id(authorization), duty, min_age, max_age, restriction,
    )
    total = await pool.fetchval(
        SQL_COUNT, q, district, min_price, max_price, online, duty,
        min_age, max_age, restriction,
    )
    identity = bearer_identity(authorization)
    is_admin = bool(identity and identity[1])
    filters = await _compiled_display_filters(pool)
    ocr = await _ocr_by_listing(pool, rows)
    return {
        "items": [_row_to_item(r, filters, is_admin, ocr_entries=ocr.get(r["id"])) for r in rows],
        "total": total,
    }


@router.get("/listings/{listing_id}")
async def listing_detail(
    listing_id: int, authorization: str = Header(default="")
) -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    rows = await pool.fetch(SQL_DETAIL, listing_id)
    if not rows:
        raise HTTPException(status_code=404, detail="not found or not published")
    identity = bearer_identity(authorization)
    filters = await _compiled_display_filters(pool)
    ocr = await _ocr_by_listing(pool, rows)
    item = _row_to_item(rows[0], filters, bool(identity and identity[1]),
                        include_full_media=True, ocr_entries=ocr.get(listing_id))
    # the raw handle stays admin-only; regular users reach the contact
    # through the proxied chat instead
    item["contact_username"] = (
        rows[0]["contact_username"] if (identity and identity[1]) else None
    )
    item["confidence"] = float(rows[0]["confidence"]) if rows[0]["confidence"] is not None else None
    item["online_tier"] = rows[0]["presence_tier"] or "unknown"
    item["online"] = item["online_tier"] == "online"
    return item


@router.get("/meta/districts")
async def districts() -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    rows = await pool.fetch(SQL_DISTRICTS)
    return {"districts": [r["district"] for r in rows]}


@router.get("/favorites")
async def favorites(authorization: str = Header(default="")) -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    user_id = current_user_id(authorization)
    rows = await pool.fetch(SQL_FAV_LIST, user_id)
    identity = bearer_identity(authorization)
    filters = await _compiled_display_filters(pool)
    ocr = await _ocr_by_listing(pool, rows)
    return {
        "items": [_row_to_item(r, filters, bool(identity and identity[1]),
                               ocr_entries=ocr.get(r["id"])) for r in rows]
    }


@router.post("/favorites/{listing_id}")
async def toggle_favorite(listing_id: int, authorization: str = Header(default="")) -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    user_id = current_user_id(authorization)
    rows = await pool.fetch(SQL_FAV_TOGGLE, user_id, listing_id)
    return {"favorited": rows[0]["favorited"]}
