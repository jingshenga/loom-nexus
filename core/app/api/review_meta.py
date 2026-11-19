"""Small metadata endpoints for review workbench filters and paging."""
import json
import re
from datetime import date
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException

from app import db
from app.api.auth import require_admin

router = APIRouter(
    prefix="/api/admin/review", tags=["admin-review"],
    dependencies=[Depends(require_admin)],
)


def _pool():
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    return pool


class FieldDefinition(BaseModel):
    key: str
    label: str
    aliases: list[str] = Field(default_factory=list)
    extraction_source: str = "manual"
    required_for_publish: bool = False
    is_active: bool = True


class FieldDefinitionsBody(BaseModel):
    items: list[FieldDefinition]


@router.get("/fields")
async def review_fields() -> dict:
    rows = await _pool().fetch(
        "SELECT field_key,label,aliases,extraction_source,required_for_publish,is_active "
        "FROM listing_field_definitions ORDER BY field_key"
    )
    items = []
    for row in rows:
        item = dict(row)
        aliases = item["aliases"]
        item["aliases"] = aliases if isinstance(aliases, list) else json.loads(aliases or "[]")
        item["key"] = item.pop("field_key")
        items.append(item)
    return {"items": items}


@router.put("/fields")
async def save_review_fields(body: FieldDefinitionsBody) -> dict:
    keys = set()
    for item in body.items:
        key = item.key.strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,47}", key):
            raise HTTPException(status_code=422, detail=f"字段键无效：{item.key}")
        if key in keys:
            raise HTTPException(status_code=422, detail=f"字段键重复：{key}")
        keys.add(key)
        if not item.label.strip() or item.extraction_source not in {"manual", "telegram", "ocr"}:
            raise HTTPException(status_code=422, detail=f"字段 {key} 的名称或来源无效")
        if any(not alias.strip() for alias in item.aliases):
            raise HTTPException(status_code=422, detail=f"字段 {key} 的别名不能为空")
    pool = _pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE listing_field_definitions SET is_active=FALSE,updated_at=NOW() "
                "WHERE field_key <> ALL($1::varchar[])", list(keys),
            )
            for item in body.items:
                await conn.execute(
                    "INSERT INTO listing_field_definitions "
                    "(field_key,label,aliases,extraction_source,required_for_publish,is_active,updated_at) "
                    "VALUES($1,$2,$3::jsonb,$4,$5,$6,NOW()) "
                    "ON CONFLICT(field_key) DO UPDATE SET label=EXCLUDED.label,aliases=EXCLUDED.aliases, "
                    "extraction_source=EXCLUDED.extraction_source,required_for_publish=EXCLUDED.required_for_publish, "
                    "is_active=EXCLUDED.is_active,updated_at=NOW()",
                    item.key.strip().lower(), item.label.strip(), json.dumps(item.aliases, ensure_ascii=False),
                    item.extraction_source, item.required_for_publish, item.is_active,
                )
    return {"ok": True}


@router.get("/sources")
async def review_sources() -> dict:
    rows = await _pool().fetch(
        "SELECT DISTINCT s.id,s.title,s.username "
        "FROM ingest_sources s JOIN source_rule_bindings b ON b.source_id=s.id "
        "JOIN ingest_rules r ON r.id=b.rule_id "
        "WHERE r.rule_type IN ('listing','on_duty','profile_collection','bot_collection','checkin_reverse') "
        "ORDER BY s.title,s.id"
    )
    return {"items": [dict(row) for row in rows]}


@router.get("/queue-total")
async def queue_total(
    status: str = "pending", nickname: str | None = None,
    source: str | None = None, media: str | None = None,
    bucket: str | None = None,
    collected_from: date | None = None, collected_to: date | None = None,
    original_from: date | None = None, original_to: date | None = None,
    min_media: int | None = None, max_media: int | None = None,
) -> dict:
    if status not in {"pending", "published", "rejected", "archived"}:
        raise HTTPException(status_code=400, detail="invalid review status")
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
    total = await _pool().fetchval(
        "SELECT count(*) FROM listings l "
        "LEFT JOIN raw_messages rm ON rm.id=l.source_msg_id "
        "LEFT JOIN ingest_sources s ON s.id=rm.source_id "
        "LEFT JOIN profile_collection_jobs pcj ON pcj.id=l.profile_collection_job_id "
        "LEFT JOIN ingest_rules ir ON ir.id=pcj.rule_id "
        "WHERE l.status=$1::text "
        "AND (l.status<>'pending' OR NOT nexus_auto_duplicate_folded(l.id)) "
        "AND ($5::text IS NULL OR l.evidence_json->>'review_bucket'=$5::text) "
        "AND ($2::text IS NULL OR l.nickname ILIKE '%'||$2::text||'%') "
        "AND ($3::text IS NULL OR COALESCE(s.title,CASE WHEN ir.rule_type='checkin_reverse' "
        "THEN '签到来源反查 V2' WHEN ir.rule_type='profile_collection' THEN 'TG号采集 V2' "
        "WHEN ir.rule_type='bot_collection' THEN 'Bot地址采集' "
        "WHEN l.duty_collection_job_id IS NOT NULL THEN 'Telegram 个人资料/关联频道' END,'') "
        "ILIKE '%'||$3::text||'%') "
        "AND ($4::text IS NULL OR EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(l.media_json,'[]'::jsonb)) m "
        "WHERE m->>'kind'=$4::text)) "
        "AND ($6::date IS NULL OR (l.created_at AT TIME ZONE 'Asia/Shanghai')::date >= $6::date) "
        "AND ($7::date IS NULL OR (l.created_at AT TIME ZONE 'Asia/Shanghai')::date <= $7::date) "
        "AND ($10::date IS NULL OR (rm.posted_at AT TIME ZONE 'Asia/Shanghai')::date >= $10::date) "
        "AND ($11::date IS NULL OR (rm.posted_at AT TIME ZONE 'Asia/Shanghai')::date <= $11::date) "
        "AND ($8::int IS NULL OR (SELECT count(*) FROM jsonb_array_elements(COALESCE(l.media_json,'[]'::jsonb)) m "
        "WHERE m->>'kind' IN ('photo','video') AND COALESCE(m->>'path',m->>'poster','') <> '') >= $8::int) "
        "AND ($9::int IS NULL OR (SELECT count(*) FROM jsonb_array_elements(COALESCE(l.media_json,'[]'::jsonb)) m "
        "WHERE m->>'kind' IN ('photo','video') AND COALESCE(m->>'path',m->>'poster','') <> '') <= $9::int) "
        "AND ($1::text <> 'pending' OR NOT EXISTS ("
        "SELECT 1 FROM listings other WHERE other.id<>l.id "
        "AND other.status IN ('pending','duplicate_pending','published','archived') "
        "AND NOT EXISTS (SELECT 1 FROM listing_person_group_members a JOIN listing_person_group_members b "
        "ON a.group_id=b.group_id WHERE a.listing_id=l.id AND b.listing_id=other.id) "
        "AND NULLIF(l.nickname,'') IS NOT NULL AND NULLIF(other.nickname,'') IS NOT NULL "
        "AND regexp_replace(lower(l.nickname),'[[:space:][:punct:]_]+','','g') "
        "= regexp_replace(lower(other.nickname),'[[:space:][:punct:]_]+','','g') "
        "AND (l.profile_collection_job_id IS NOT NULL OR other.profile_collection_job_id IS NOT NULL "
        "OR EXISTS (SELECT 1 FROM raw_messages history_raw "
        "JOIN checkin_history_scans hs ON hs.source_id=history_raw.source_id "
        "WHERE history_raw.id=l.source_msg_id OR history_raw.id=other.source_msg_id)) "
        "AND NOT EXISTS (SELECT 1 FROM listing_duplicate_decisions d "
        "WHERE d.left_listing_id=LEAST(l.id,other.id) AND d.right_listing_id=GREATEST(l.id,other.id))"
        "))",
        status, nickname or None, source or None, media, bucket or None,
        collected_from, collected_to, min_media, max_media, original_from, original_to,
    )
    return {"total": total}
