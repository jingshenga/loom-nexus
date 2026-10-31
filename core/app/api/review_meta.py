"""Small metadata endpoints for review workbench filters and paging."""
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


@router.get("/sources")
async def review_sources() -> dict:
    rows = await _pool().fetch(
        "SELECT DISTINCT s.id,s.title,s.username "
        "FROM ingest_sources s JOIN source_rule_bindings b ON b.source_id=s.id "
        "JOIN ingest_rules r ON r.id=b.rule_id "
        "WHERE r.rule_type IN ('listing','on_duty','profile_collection','checkin_reverse') "
        "ORDER BY s.title,s.id"
    )
    return {"items": [dict(row) for row in rows]}


@router.get("/queue-total")
async def queue_total(
    status: str = "pending", nickname: str | None = None,
    source: str | None = None, media: str | None = None,
    bucket: str | None = None,
) -> dict:
    if status not in {"pending", "published", "rejected", "archived"}:
        raise HTTPException(status_code=400, detail="invalid review status")
    if media not in {None, "photo", "video"}:
        raise HTTPException(status_code=400, detail="invalid media filter")
    total = await _pool().fetchval(
        "SELECT count(*) FROM listings l "
        "LEFT JOIN raw_messages rm ON rm.id=l.source_msg_id "
        "LEFT JOIN ingest_sources s ON s.id=rm.source_id "
        "LEFT JOIN profile_collection_jobs pcj ON pcj.id=l.profile_collection_job_id "
        "LEFT JOIN ingest_rules ir ON ir.id=pcj.rule_id "
        "WHERE l.status=$1::text "
        "AND (($5::text IS NULL AND l.profile_collection_job_id IS NULL) "
        " OR ($5::text IS NOT NULL AND l.evidence_json->>'review_bucket'=$5::text)) "
        "AND ($2::text IS NULL OR l.nickname ILIKE '%'||$2::text||'%') "
        "AND ($3::text IS NULL OR COALESCE(s.title,CASE WHEN ir.rule_type='checkin_reverse' "
        "THEN '签到来源反查 V2' WHEN ir.rule_type='profile_collection' THEN 'TG号采集 V2' "
        "WHEN l.duty_collection_job_id IS NOT NULL THEN 'Telegram 个人资料/关联频道' END,'') "
        "ILIKE '%'||$3::text||'%') "
        "AND ($4::text IS NULL OR EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(l.media_json,'[]'::jsonb)) m "
        "WHERE m->>'kind'=$4::text))",
        status, nickname or None, source or None, media, bucket or None,
    )
    return {"total": total}
