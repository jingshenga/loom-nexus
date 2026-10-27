"""Public square APIs: published listings, detail, favorites."""
import re
import time
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException

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


def _row_to_item(row, display_filters=None, is_admin=True) -> dict:
    patterns = (display_filters or {}).get(row["source_id"])
    tier = row["presence_tier"] if "presence_tier" in row.keys() else None
    # the full description (incl. the card summary line) is admin-only;
    # regular users see fields + media + chat entry, not the raw text
    description = _apply_excludes(row["description"], patterns)
    if not is_admin:
        description = None
    return {
        "id": row["id"],
        "nickname": row["nickname"],
        "district": row["district"],
        "price_amount": float(row["price_amount"]) if row["price_amount"] is not None else None,
        "price_currency": row["price_currency"],
        "description": description,
        "media": _decode(row["media_json"], []),
        "published_at": row["published_at"].isoformat() if row["published_at"] else None,
        "online": tier == "online",
        "online_tier": tier or "unknown",
        "duty_label": row["duty_label"] if "duty_label" in row.keys() else None,
        "favorited": bool(row["favorited"]) if "favorited" in row.keys() else False,
    }


@router.get("/listings")
async def list_listings(
    authorization: str = Header(default=""),
    q: str = None,
    district: str = None,
    min_price: float = None,
    max_price: float = None,
    online: bool = None,
    duty: Literal["today", "yesterday"] = "today",
    limit: int = 24,
    offset: int = 0,
) -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    rows = await pool.fetch(
        SQL_LIST, q, district, min_price, max_price, limit, offset, online,
        current_user_id(authorization), duty,
    )
    total = await pool.fetchval(
        SQL_COUNT, q, district, min_price, max_price, online, duty,
    )
    identity = bearer_identity(authorization)
    is_admin = bool(identity and identity[1])
    filters = await _compiled_display_filters(pool)
    return {
        "items": [_row_to_item(r, filters, is_admin) for r in rows],
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
    item = _row_to_item(rows[0], filters, bool(identity and identity[1]))
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
    return {
        "items": [_row_to_item(r, filters, bool(identity and identity[1])) for r in rows]
    }


@router.post("/favorites/{listing_id}")
async def toggle_favorite(listing_id: int, authorization: str = Header(default="")) -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    user_id = current_user_id(authorization)
    rows = await pool.fetch(SQL_FAV_TOGGLE, user_id, listing_id)
    return {"favorited": rows[0]["favorited"]}
