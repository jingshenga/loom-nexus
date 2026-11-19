"""Public square APIs: published listings, detail, favorites."""
import math
import re
import time
from collections import defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app import db, sqlres
from app.extractor.ocr_fields import format_fee_text

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
SELECT l.id AS listing_id, o.media_path, o.fields_json, o.evidence_json, o.confidence, o.review_status,
       COALESCE(o.human_classification, o.classification) AS classification
FROM listings l
JOIN listing_media_ocr o ON (
    o.listing_id = l.id OR o.raw_id = l.source_msg_id OR o.profile_result_id = (
        SELECT max(r.id) FROM profile_collection_results r
        WHERE r.job_id = l.profile_collection_job_id
    )
)
WHERE l.id = ANY($1::bigint[]) AND o.status = 'done'
  AND ($2::boolean OR COALESCE(o.human_classification, o.classification) = 'info_card')
ORDER BY o.confidence DESC NULLS LAST, o.id
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


# ---------------------------------------------------------------------------
# structured attributes: OCR card fields + extraction evidence
# ---------------------------------------------------------------------------
# The structured data produced by the extractor (body profile, 1p/2p
# tariffs, services, restrictions) lives in listings.evidence_json and
# in the per-image listing_media_ocr.fields_json cards. Everything here
# is defensive: legacy cards without any of it degrade to null/empty.

_CARD_SCALAR_KEYS = (
    "age_years", "height_cm", "weight_kg", "weight_value", "bust_size",
    "price_1p", "price_1p_display", "price_2p", "price_2p_display",
)
_CARD_LIST_KEYS = (
    "price_1p_text", "price_2p_text", "cash_text", "price_text",
    "service_text", "features_text", "restriction_text", "fixed_intro_terms",
)
_RESTRICT_TAG = re.compile(r"禁[\u4e00-\u9fff]{1,6}")
_SERVICE_LABEL = re.compile(
    r"^(?:服务详情|服务内容|服务项目|服务|课表|项目|套餐|特点|特征|特色)[:：]?\s*"
)
# non-admin payloads carry no contact handles or phone numbers
_HANDLE_RE = re.compile(r"(?:@[A-Za-z0-9_]{3,}|(?:https?://)?t\.me/\S+)")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\- ]{6,}\d)(?!\d)")


def _redact(text: str) -> str:
    return _PHONE_RE.sub("[联系]", _HANDLE_RE.sub("[联系]", text or ""))


def _as_int(value):
    try:
        return int(float(re.sub(r"\s*(?:岁|厘米|cm)$", "", str(value).strip(), flags=re.I)))
    except (TypeError, ValueError, OverflowError):
        return None


def _as_float(value):
    try:
        weight = float(re.sub(r"\s*(?:公斤|kg)$", "", str(value).strip(), flags=re.I))
        return round(weight, 1) if math.isfinite(weight) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _attr_list(values, limit=8) -> list:
    out = []
    for value in values or []:
        text = str(value).strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _merge_card_fields(cards) -> dict:
    """Merge per-image OCR cards: best-confidence card wins for scalar
    body values; list fields (fee/service/restriction lines) union so
    multi-image albums contribute everything."""
    scalars, lists = {}, {}
    for card in cards or []:
        fields = card.get("fields") if isinstance(card, dict) else None
        if not isinstance(fields, dict):
            continue
        for key in _CARD_SCALAR_KEYS:
            if key not in scalars and fields.get(key) not in (None, ""):
                scalars[key] = fields[key]
        for key in _CARD_LIST_KEYS:
            value = fields.get(key)
            if isinstance(value, list):
                bucket = lists.setdefault(key, [])
                for item in value:
                    text = str(item).strip()
                    if text and text not in bucket:
                        bucket.append(text)
    return {**scalars, **lists}


def _service_tags(card_fields) -> list:
    tags = []
    for key in ("service_text", "features_text"):
        for line in card_fields.get(key) or []:
            body = _SERVICE_LABEL.sub("", str(line))
            for token in re.split(r"[\s，,、/／;；|]+", body):
                token = token.strip("。，,；;：:!？?·.")
                if token == "[联系]":
                    continue
                if 1 <= len(token) <= 10 and re.search(r"[\u4e00-\u9fffA-Za-z0-9]", token):
                    tags.append(token)
    return _attr_list(tags, 16)


def _restriction_tags(card_fields) -> list:
    tags = []
    for line in card_fields.get("restriction_text") or []:
        found = _RESTRICT_TAG.findall(str(line))
        if found:
            tags.extend(found)
        elif len(str(line)) <= 12:
            tags.append(str(line).strip())
    tags.extend(str(term) for term in card_fields.get("fixed_intro_terms") or [])
    return _attr_list(tags, 12)


def _quote_list(card_fields, display_key, lines_key) -> list:
    """Clean 1P/2P display strings ("600 元 (40分钟)"): the structured
    quote display value wins; legacy dirty lines ("1P800 2P1500") are
    re-formatted, and unparsable ones are dropped entirely."""
    display = card_fields.get(display_key)
    if display not in (None, ""):
        text = str(display).strip()
        return [text] if text else []
    out = []
    for line in card_fields.get(lines_key) or []:
        text = format_fee_text(line)
        if text and text not in out:
            out.append(text)
        if len(out) >= 6:
            break
    return out


def _raw_fees(card_fields) -> list:
    """price_text lines that are NOT 1P/2P quotes (包夜 / 包时 / BT …)
    keep their original text; tiered quotes live in p1/p2 instead."""
    out = []
    for line in card_fields.get("price_text") or []:
        if format_fee_text(line):
            continue
        text = str(line).strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= 8:
            break
    return out


def build_attributes(evidence, card_fields, corrections=None) -> dict:
    """Standardized attributes payload shared by every viewer.

    `evidence` is the listing-level extraction evidence (its values
    already merged regex-over-OCR in the extractor, so regex wins);
    `card_fields` is the merged per-image OCR card data. Missing keys
    degrade to null / empty lists, never raise."""
    corrections = corrections or {}
    card_fields = dict(card_fields or {})
    for key in _CARD_LIST_KEYS:
        if key in corrections:
            value = corrections[key]
            card_fields[key] = value if isinstance(value, list) else ([value] if isinstance(value, str) else [])
    for key in ("price_1p_display", "price_2p_display"):
        if key in corrections:
            card_fields[key] = corrections[key]
    if "weight_kg" not in card_fields and "weight_value" in card_fields:
        card_fields["weight_kg"] = card_fields["weight_value"]

    def pick(key, cast):
        if key in corrections:
            value = corrections[key]
            return cast(value) if value not in (None, "") else None
        for source in (evidence, card_fields):
            value = (source or {}).get(key)
            # evidence entries wrap their payload in {"value": ...}
            if isinstance(value, dict) and "value" in value:
                value = value.get("value")
            if value in (None, ""):
                continue
            converted = cast(value)
            if converted is not None:
                return converted
        return None

    return {
        "profile": {
            "age": pick("age_years", _as_int),
            "height": pick("height_cm", _as_int),
            "weight": pick("weight_kg", _as_float),
            "bust": pick("bust_size", lambda v: str(v).strip() or None),
        },
        "tariffs": {
            # 干净的 "600 元 (40分钟)" 式价格串；1P/2P 是轮次而非人数
            "p1": _quote_list(card_fields, "price_1p_display", "price_1p_text"),
            "p2": _quote_list(card_fields, "price_2p_display", "price_2p_text"),
            "cash_note": _attr_list(card_fields.get("cash_text"), 4),
            "raw_fees": _raw_fees(card_fields),
        },
        "services": _service_tags(card_fields),
        "restrictions": _restriction_tags(card_fields),
    }


def _sanitize_attributes(attributes: dict) -> dict:
    """Strip handles/phone numbers from every string a regular user sees."""
    def walk(value):
        if isinstance(value, str):
            return _redact(value)
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, dict):
            return {key: walk(item) for key, item in value.items()}
        return value

    return walk(attributes)


def _row_attributes(row, is_admin: bool, ocr_entries=()) -> dict:
    keys = row.keys()
    evidence = _decode(row["evidence_json"] if "evidence_json" in keys else None, {}) or {}
    raw_cards = _decode(row["ocr_cards"] if "ocr_cards" in keys else None, []) or []
    card_fields = _merge_card_fields(
        [{"fields": _decode(card["fields_json"], {})} for card in ocr_entries
         if card.get("classification", "info_card") == "info_card"]
        + [{"fields": card} for card in raw_cards if isinstance(card, dict)]
    )
    custom = _decode(row.get("custom_fields"), {})
    evidence = evidence if isinstance(evidence, dict) else {}
    custom = custom if isinstance(custom, dict) else {}
    if not is_admin:
        evidence = _sanitize_attributes(evidence)
        card_fields = _sanitize_attributes(card_fields)
        custom = _sanitize_attributes(custom)
    return build_attributes(evidence, card_fields, custom)


async def _ocr_by_listing(pool, rows, *, include_all=False):
    ids = [row["id"] for row in rows]
    if not ids:
        return {}
    entries = defaultdict(list)
    for row in await pool.fetch(SQL_OCR_ATTRIBUTES, ids, include_all):
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
        "media": media if include_full_media else media[:1],
        "media_count": len(media),
        "published_at": row["published_at"].isoformat() if row["published_at"] else None,
        "online": tier == "online",
        "online_tier": tier or "unknown",
        "duty_label": row["duty_label"] if "duty_label" in row.keys() else None,
        "favorited": bool(row["favorited"]) if "favorited" in row.keys() else False,
        # sanitized (handles/phones stripped) for regular users; admins
        # additionally get the raw evidence/OCR trace on the detail page
        "attributes": _row_attributes(row, is_admin, ocr_entries),
    }
    if is_admin:
        item["evidence_json"] = _decode(row["evidence_json"], {}) if "evidence_json" in row.keys() else {}
        item["ocr_evidence"] = [{
            "media_path": entry["media_path"],
            "classification": entry.get("classification"),
            "review_status": entry.get("review_status"),
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
    is_admin = bool(identity and identity[1])
    ocr = await _ocr_by_listing(pool, rows, include_all=is_admin)
    item = _row_to_item(rows[0], filters, is_admin,
                        include_full_media=True, ocr_entries=ocr.get(listing_id))
    # the raw handle stays admin-only; regular users reach the contact
    # through the proxied chat instead
    item["contact_username"] = (
        rows[0]["contact_username"] if is_admin else None
    )
    item["confidence"] = float(rows[0]["confidence"]) if rows[0]["confidence"] is not None else None
    item["online_tier"] = rows[0]["presence_tier"] or "unknown"
    item["online"] = item["online_tier"] == "online"
    if is_admin:
        item["evidence"] = item["evidence_json"]
        item["ocr_trace"] = item["ocr_evidence"]
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
