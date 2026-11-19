"""Read-only evidence aggregation and transactional manual listing decisions."""

import json
import re
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app import db
from app.api.auth import current_user_id, require_admin
from app.api.review import (
    SQL_PUBLISH, SQL_REJECT, WORKBENCH_ATTRIBUTE_KEYS, _decode, missing_publish_fields,
)

router = APIRouter(
    prefix="/api/admin/moderation/workbench", tags=["admin-moderation"],
    dependencies=[Depends(require_admin)],
)

# OCR can belong to a listing, its Telegram message, or its profile collection result.
OCR_LINK = "(o.listing_id=l.id OR o.raw_id=rm.id OR o.profile_result_id=p.id)"
SOURCE_JOINS = """
LEFT JOIN profile_collection_jobs j ON j.id=l.profile_collection_job_id
LEFT JOIN duty_collection_jobs d ON d.id=l.duty_collection_job_id
LEFT JOIN raw_messages rm ON rm.id=COALESCE(l.source_msg_id,d.raw_id)
LEFT JOIN ingest_sources s ON s.id=COALESCE(rm.source_id,j.source_id,d.source_id)
LEFT JOIN profile_collection_results p ON p.job_id=l.profile_collection_job_id
"""
SOURCE_TITLE = """COALESCE(s.title, CASE
    WHEN l.profile_collection_job_id IS NOT NULL OR l.duty_collection_job_id IS NOT NULL
    THEN 'Telegram 个人资料/关联频道' END)"""
SQL_QUEUE = f"""
SELECT l.id, l.nickname, l.district, l.created_at, l.price_amount,
       l.confidence, {SOURCE_TITLE} AS source_title,
       ocr.has_ocr, ocr.needs_review
FROM listings l
{SOURCE_JOINS}
CROSS JOIN LATERAL (
    SELECT count(*) > 0 AS has_ocr,
           COALESCE(bool_or(o.review_status='pending'), false) AS needs_review
    FROM listing_media_ocr o WHERE {OCR_LINK}
) ocr
WHERE l.status='pending'
AND CASE $1::text
    WHEN 'low_confidence' THEN l.confidence < 0.79
    WHEN 'missing_price' THEN l.price_amount IS NULL
    WHEN 'has_ocr' THEN ocr.has_ocr
    ELSE TRUE END
ORDER BY l.created_at DESC NULLS LAST, l.id DESC
LIMIT $2 OFFSET $3
"""
SQL_DETAIL = f"""
SELECT l.*, rm.sender_peer_id, {SOURCE_TITLE} AS source_title, rm.posted_at AS original_posted_at,
       COALESCE(rm.raw_text, p.description, l.description) AS original_text,
       CASE WHEN rm.raw_text IS NOT NULL THEN 'telegram'
            WHEN p.description IS NOT NULL THEN 'profile' ELSE 'listing' END AS original_text_source,
       COALESCE(rm.media_json, '[]'::jsonb) || COALESCE(p.media_json, '[]'::jsonb) AS original_media
FROM listings l
{SOURCE_JOINS}
WHERE l.id=$1 AND l.status='pending'
"""
SQL_OCR = f"""
SELECT o.id, o.media_path, o.raw_text, o.fields_json, o.evidence_json,
       o.confidence, o.status, o.review_status,
       COALESCE(o.human_classification,o.classification) AS classification
FROM listings l
{SOURCE_JOINS}
JOIN listing_media_ocr o ON {OCR_LINK}
WHERE l.id=$1
ORDER BY o.id
"""
SQL_REQUIRED = (
    "SELECT field_key,label FROM listing_field_definitions "
    "WHERE is_active AND required_for_publish ORDER BY field_key"
)

BASE_FIELDS = ("nickname", "district", "price_amount", "price_currency", "description", "contact_username")
LIST_FIELDS = {
    "price_1p_text", "price_2p_text", "service_text", "features_text",
    "restriction_text", "fixed_intro_terms",
}
TextLine = Annotated[str, Field(max_length=1000)]


class AttributeEdits(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)

    age_years: int | None = Field(None, ge=0, le=120)
    height_cm: int | None = Field(None, ge=50, le=250)
    weight_kg: float | None = Field(None, gt=0, le=500)
    bust_size: str | None = Field(None, max_length=24)
    price_1p_text: list[TextLine] = Field(default_factory=list, max_length=50)
    price_2p_text: list[TextLine] = Field(default_factory=list, max_length=50)
    service_text: list[TextLine] = Field(default_factory=list, max_length=50)
    features_text: list[TextLine] = Field(default_factory=list, max_length=50)
    restriction_text: list[TextLine] = Field(default_factory=list, max_length=50)
    fixed_intro_terms: list[TextLine] = Field(default_factory=list, max_length=50)


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: Literal["approve", "save", "reject", "sample"]
    expected_updated_at: AwareDatetime | None
    nickname: str | None = Field(None, max_length=128)
    district: str | None = Field(None, max_length=64)
    price_amount: Decimal | None = Field(None, ge=0, max_digits=10, decimal_places=2)
    price_currency: str | None = Field(None, max_length=16)
    description: str | None = Field(None, max_length=30000)
    contact_username: str | None = Field(None, max_length=128)
    attributes: AttributeEdits = Field(default_factory=AttributeEdits)
    required_fields: dict[str, str | None] = Field(default_factory=dict, max_length=100)


def _pool():
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    return pool


def _object(value):
    data = _decode(value, {})
    return data if isinstance(data, dict) else {}


def _attribute_values(custom, ocr, evidence=None):
    values = {}
    for asset in ocr:
        if asset["status"] != "done" or asset["classification"] != "info_card":
            continue
        fields = asset["fields"]
        for key in WORKBENCH_ATTRIBUTE_KEYS:
            value = fields.get(key)
            if value is None:
                continue
            if key in LIST_FIELDS:
                lines = value if isinstance(value, list) else [value]
                values[key] = list(dict.fromkeys(
                    line for line in [*values.get(key, []), *lines] if isinstance(line, str) and line.strip()
                ))
            else:
                values.setdefault(key, value)
    for key, entry in (evidence or {}).items():
        if key in WORKBENCH_ATTRIBUTE_KEYS - LIST_FIELDS and isinstance(entry, dict) and entry.get("value") is not None:
            values[key] = entry["value"]
    values.update({key: custom[key] for key in WORKBENCH_ATTRIBUTE_KEYS if key in custom})
    return values


def _ocr_item(row):
    evidence = _object(row["evidence_json"])
    by_line = {line.strip(): {"text": line.strip(), "score": None, "fields": []}
               for line in (row["raw_text"] or "").splitlines() if line.strip()}
    # Some engines persist the full text as a single line. Add evidence
    # separately when its original line is not in that text.
    for key, entry in evidence.items():
        if isinstance(entry, dict) and isinstance(entry.get("line"), str):
            line = entry["line"].strip()
            if line:
                match = by_line.setdefault(line, {"text": line, "score": None, "fields": []})
                if entry.get("score") is not None:
                    match["score"] = entry["score"]
                match["fields"].append(key)
    return {
        "id": row["id"], "media_path": row["media_path"], "status": row["status"],
        "classification": row["classification"], "review_status": row["review_status"],
        "confidence": row["confidence"], "raw_text": row["raw_text"],
        "fields": _object(row["fields_json"]), "lines": list(by_line.values()),
    }


def _media(row, ocr):
    media = {}
    for value in (row["original_media"], row["media_json"]):
        items = _decode(value, [])
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("kind") in {"photo", "video"}:
                path = item.get("path")
                if isinstance(path, str) and path:
                    media[path] = item
    posters = {item.get("poster") for item in media.values()}
    for asset in ocr:
        path = asset["media_path"]
        if path and path not in posters:
            media.setdefault(path, {"kind": "photo", "path": path})
    return list(media.values())


def _queue_item(row):
    issues = []
    if row["price_amount"] is None:
        issues.append("缺价格")
    if row["confidence"] is not None and row["confidence"] < Decimal("0.79"):
        issues.append("低置信度")
    if row["needs_review"]:
        issues.append("待人工复核")
    return {**dict(row), "issues": issues}


@router.get("/next")
async def next_card(
    filter: Literal["all", "low_confidence", "missing_price", "has_ocr"] = "all",
    listing_id: Annotated[int | None, Query(gt=0)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    async with _pool().acquire() as conn:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            rows = await conn.fetch(SQL_QUEUE, filter, limit + 1, offset)
            queue = [_queue_item(row) for row in rows[:limit]]
            if listing_id is None and queue:
                listing_id = queue[0]["id"]
            if listing_id is None:
                return {"queue": queue, "has_more": len(rows) > limit, "card": None}

            row = await conn.fetchrow(SQL_DETAIL, listing_id)
            if row is None:
                raise HTTPException(404, "该卡片已不在待审队列")
            ocr = [_ocr_item(asset) for asset in await conn.fetch(SQL_OCR, listing_id)]
            custom_fields = _object(row["custom_fields"])
            required = await conn.fetch(SQL_REQUIRED)
            missing = missing_publish_fields(row, custom_fields, required)
            return {
                "queue": queue, "has_more": len(rows) > limit,
                "card": {
                    "id": listing_id, "updated_at": row["updated_at"],
                    "source_title": row["source_title"],
                    "original_text": row["original_text"],
                    "original_text_source": row["original_text_source"],
                    "original_posted_at": row["original_posted_at"],
                    "fields": {key: row[key] for key in BASE_FIELDS},
                    "attributes": _attribute_values(custom_fields, ocr, _object(row["evidence_json"])),
                    "required_attribute_keys": [field["field_key"] for field in required
                                                if field["field_key"] in WORKBENCH_ATTRIBUTE_KEYS
                                                and not str(custom_fields.get(field["field_key"]) or "").strip()],
                    "required_fields": [{**dict(field), "value": custom_fields.get(field["field_key"])}
                                        for field in required if field["field_key"] not in WORKBENCH_ATTRIBUTE_KEYS],
                    "missing_publish_fields": missing,
                    "media": _media(row, ocr), "ocr": ocr,
                },
            }


@router.post("/{listing_id}/decision")
async def decide(listing_id: int, body: Decision, authorization: str = Header(default="")) -> dict:
    async with _pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT * FROM listings WHERE id=$1 FOR UPDATE", listing_id)
            if row is None:
                raise HTTPException(404, "卡片不存在")
            if row["status"] != "pending" or row["updated_at"] != body.expected_updated_at:
                raise HTTPException(409, "卡片已更新，请重新加载后审核")

            before_fields = {key: row[key] for key in BASE_FIELDS}
            fields = before_fields | body.model_dump(include=set(BASE_FIELDS), exclude_unset=True)
            if "contact_username" in body.model_fields_set:
                username = (fields["contact_username"] or "").lstrip("@").lower()
                if username and not re.fullmatch(r"[a-z][a-z0-9_]{4,31}", username):
                    raise HTTPException(422, "TG 号格式无效")
                fields["contact_username"] = username or None
            check_currency = body.action == "approve" or bool({"price_amount", "price_currency"} & body.model_fields_set)
            if check_currency and fields["price_amount"] is not None and not (fields["price_currency"] or "").strip():
                raise HTTPException(422, "填写价格时必须填写货币")

            required = await conn.fetch(SQL_REQUIRED)
            known_keys = {field["field_key"] for field in required} - WORKBENCH_ATTRIBUTE_KEYS
            if body.required_fields.keys() - known_keys:
                raise HTTPException(422, "包含未定义的必填字段")
            before_custom = _object(row["custom_fields"])
            custom = before_custom | body.attributes.model_dump(exclude_unset=True) | body.required_fields
            if body.action == "sample":
                snapshot = await conn.fetchrow(SQL_DETAIL, listing_id)
                ocr = [_ocr_item(asset) for asset in await conn.fetch(SQL_OCR, listing_id)]
                expected = {
                    "fields": fields,
                    "attributes": _attribute_values(custom, ocr, _object(row["evidence_json"])),
                    "custom_fields": {key: custom[key] for key in known_keys if key in custom},
                }
                await conn.execute(
                    """INSERT INTO golden_rule_samples
                       (listing_id,raw_text,sender_peer_id,media_json,ocr_json,expected_json,saved_by)
                       VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,$6::jsonb,$7)
                       ON CONFLICT (listing_id) DO UPDATE SET
                         raw_text=excluded.raw_text,sender_peer_id=excluded.sender_peer_id,
                         media_json=excluded.media_json,ocr_json=excluded.ocr_json,
                         expected_json=excluded.expected_json,saved_by=excluded.saved_by,saved_at=now()""",
                    listing_id, snapshot["original_text"] or "", snapshot["sender_peer_id"],
                    json.dumps(_media(snapshot, ocr), ensure_ascii=False, default=str),
                    json.dumps(ocr, ensure_ascii=False, default=str),
                    json.dumps(expected, ensure_ascii=False, default=str),
                    current_user_id(authorization),
                )
                return {"id": listing_id, "decision": "sample_saved"}

            if body.action == "approve":
                missing = missing_publish_fields(fields, custom, required)
                if missing:
                    raise HTTPException(409, "上架条件未满足：" + "、".join(missing))

            manual = _object(row["manual_fields"])
            changed = {key: True for key in BASE_FIELDS if fields[key] != before_fields[key]}
            if custom != before_custom:
                changed["custom_fields"] = True
            if changed:
                await conn.execute(
                    """UPDATE listings SET nickname=$2, district=$3, price_amount=$4, price_currency=$5,
                       description=$6, contact_username=$7, custom_fields=$8::jsonb, manual_fields=$9::jsonb,
                       updated_at=clock_timestamp() WHERE id=$1""",
                    listing_id, *(fields[key] for key in BASE_FIELDS),
                    json.dumps(custom, ensure_ascii=False), json.dumps(manual | changed),
                )

            status, result = "pending", "saved"
            if body.action == "approve":
                status, result, sql = "published", "approved", SQL_PUBLISH
            elif body.action == "reject":
                status, result, sql = "rejected", "rejected", SQL_REJECT
            if status != "pending" and not await conn.fetchval(sql, listing_id):
                raise HTTPException(409, "卡片状态已改变")
            if changed or status != "pending":
                before = before_fields | {"custom_fields": before_custom, "status": row["status"]}
                after = fields | {"custom_fields": custom, "status": status}
                await conn.execute(
                    """INSERT INTO listing_edit_history (listing_id,editor_user_id,before_json,after_json)
                       VALUES ($1,$2,$3::jsonb,$4::jsonb)""",
                    listing_id, current_user_id(authorization),
                    json.dumps(before, ensure_ascii=False, default=str),
                    json.dumps(after, ensure_ascii=False, default=str),
                )
            updated_at = await conn.fetchval("SELECT updated_at FROM listings WHERE id=$1", listing_id)
    return {"id": listing_id, "decision": result, "status": status, "updated_at": updated_at}
