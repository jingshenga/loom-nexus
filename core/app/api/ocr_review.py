"""Admin-only review queue for locally classified OCR card images."""
import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app import db
from app.config import get_settings
from app.extractor import review_automation
from app.api.auth import bearer_identity, require_admin

router = APIRouter(
    prefix="/api/admin/ocr", tags=["admin-ocr"],
    dependencies=[Depends(require_admin)],
)


class EditOcrBody(BaseModel):
    fields: dict = Field(default_factory=dict)
    normalized_text: str | None = None


class DecisionBody(BaseModel):
    decision: str
    class_code: str | None = None
    fields: dict | None = None
    normalized_text: str | None = None


class BulkDecisionBody(BaseModel):
    decision: str
    class_code: str | None = None
    ids: list[int] = Field(default_factory=list)
    all_matching: bool = False
    status: str = "pending"
    exclude_ids: list[int] = Field(default_factory=list)
    category: str | None = None
    confidence_min: float | None = None
    confidence_max: float | None = None


def _decode(value, fallback):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value) if value is not None else fallback
    except (TypeError, ValueError):
        return fallback


async def _set_human_label(conn, asset_id: int, class_code: str, reviewer_id: int | None) -> None:
    category = await conn.fetchrow(
        "SELECT code FROM ocr_classification_categories "
        "WHERE code=$1 AND is_active AND is_trainable",
        class_code,
    )
    if category is None:
        raise HTTPException(status_code=422, detail="请选择有效的人工分类类别")
    await conn.execute(
        "UPDATE ocr_classification_labels SET superseded_at=NOW() "
        "WHERE ocr_id=$1 AND superseded_at IS NULL",
        asset_id,
    )
    await conn.execute(
        "INSERT INTO ocr_classification_labels(ocr_id,category_code,label_source,labeled_by) "
        "VALUES($1,$2,'manual',$3)",
        asset_id, class_code, reviewer_id,
    )
    await conn.execute(
        "UPDATE listing_media_ocr SET human_classification=$2 WHERE id=$1",
        asset_id, class_code,
    )


async def _sync_listing_ocr_text(conn, listing_id, old_text, new_text, include):
    if listing_id is None:
        return
    listing = await conn.fetchrow(
        "SELECT description,manual_fields FROM listings WHERE id=$1 FOR UPDATE", listing_id,
    )
    if listing is None:
        return
    manual_fields = _decode(listing["manual_fields"], {})
    if manual_fields.get("description"):
        return
    description = listing["description"] or ""
    old_marker = "图片识别内容：" + (old_text or "").strip()
    if old_marker != "图片识别内容：" and old_marker in description:
        description = description.replace(old_marker, "", 1).strip()
    new_marker = "图片识别内容：" + (new_text or "").strip()
    if include and new_marker != "图片识别内容：" and new_marker not in description:
        description = "\n\n".join(part for part in (description.strip(), new_marker) if part)
    if description != (listing["description"] or ""):
        await conn.execute(
            "UPDATE listings SET description=$2, updated_at=NOW() WHERE id=$1",
            listing_id, description,
        )


@router.get("/queue")
async def queue(
    status: str = "pending", limit: int = 60, offset: int = 0,
    category: str | None = None, confidence_min: float | None = None,
    confidence_max: float | None = None, sort: str = "newest",
    listing_id: int | None = None,
    media_path: list[str] = Query(default=[]),
) -> dict:
    if status not in {"all", "pending", "confirmed", "not_card", "failed", "automatic"}:
        raise HTTPException(status_code=400, detail="invalid OCR queue status")
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    if sort not in {"newest", "oldest", "confidence_asc", "confidence_desc"}:
        raise HTTPException(status_code=400, detail="invalid OCR sort")
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    if confidence_min is not None and not 0 <= confidence_min <= 1:
        raise HTTPException(status_code=400, detail="invalid minimum confidence")
    if confidence_max is not None and not 0 <= confidence_max <= 1:
        raise HTTPException(status_code=400, detail="invalid maximum confidence")
    if listing_id is not None and listing_id <= 0:
        raise HTTPException(status_code=400, detail="invalid listing id")
    if confidence_min is not None and confidence_max is not None and confidence_min > confidence_max:
        raise HTTPException(status_code=400, detail="minimum confidence exceeds maximum")
    filters = (
        " AND ($4::text IS NULL OR COALESCE(o.human_classification,o.classification)=$4) "
        " AND ($5::numeric IS NULL OR COALESCE(o.predicted_confidence,o.confidence)>=$5) "
        " AND ($6::numeric IS NULL OR COALESCE(o.predicted_confidence,o.confidence)<=$6) "
        " AND ($8::bigint IS NULL OR o.listing_id=$8 OR EXISTS ("
        "SELECT 1 FROM listings lf WHERE lf.id=$8 AND lf.source_msg_id=o.raw_id)) "
        " AND (cardinality($9::text[]) = 0 OR o.media_path = ANY($9::text[])) "
    )
    count_filters = (
        " AND ($2::text IS NULL OR COALESCE(o.human_classification,o.classification)=$2) "
        " AND ($3::numeric IS NULL OR COALESCE(o.predicted_confidence,o.confidence)>=$3) "
        " AND ($4::numeric IS NULL OR COALESCE(o.predicted_confidence,o.confidence)<=$4) "
        " AND ($5::bigint IS NULL OR o.listing_id=$5 OR EXISTS ("
        "SELECT 1 FROM listings lf WHERE lf.id=$5 AND lf.source_msg_id=o.raw_id)) "
        " AND (cardinality($6::text[]) = 0 OR o.media_path = ANY($6::text[])) "
    )
    rows = await pool.fetch(
        "SELECT o.id, o.raw_id, o.listing_id, o.media_path, o.status, "
        "o.classification, o.human_classification, o.predicted_classification, "
        "o.predicted_confidence, o.classifier_version_id, o.classifier_signals_json, "
        "o.review_status, o.raw_text, o.normalized_text, o.predicted_model_version, auto.reason AS auto_reason, "
        "o.fields_json, o.evidence_json, o.confidence, o.engine_version, "
        "o.error_text, COALESCE(rm.raw_text, l.description) AS message_text, "
        "COALESCE(rm.posted_at, l.created_at) AS posted_at, "
        "COALESCE(s.title, CASE WHEN l.duty_collection_job_id IS NOT NULL "
        "THEN '签到资料自动采集' END) AS source_title, l.nickname "
        "FROM listing_media_ocr o "
        "LEFT JOIN nexus_auto_ocr_decisions auto ON auto.ocr_id=o.id AND auto.undone_at IS NULL "
        "LEFT JOIN raw_messages rm ON rm.id=o.raw_id "
        "LEFT JOIN ingest_sources s ON s.id=rm.source_id "
        "LEFT JOIN LATERAL (SELECT id, nickname, description, created_at, duty_collection_job_id "
        " FROM listings WHERE id=o.listing_id OR source_msg_id=o.raw_id "
        " ORDER BY CASE WHEN id=o.listing_id THEN 0 ELSE 1 END, id DESC LIMIT 1) l ON TRUE "
        "WHERE CASE WHEN $1='pending' THEN o.review_status='pending' "
        " WHEN $1='confirmed' THEN o.review_status='confirmed' "
        " WHEN $1='not_card' THEN o.review_status='not_card' "
        " WHEN $1='automatic' THEN auto.id IS NOT NULL AND o.human_classification IS NULL "
        " WHEN $1='failed' THEN o.status='failed' ELSE TRUE END " + filters +
        "ORDER BY CASE WHEN o.status='failed' THEN 0 ELSE 1 END, "
        "CASE WHEN o.review_status='pending' AND COALESCE(o.human_classification,o.classification)='info_card' THEN 0 "
        "     WHEN o.review_status='pending' AND COALESCE(o.human_classification,o.classification)='profile_photo' THEN 1 "
        "     WHEN o.review_status='pending' THEN 2 ELSE 3 END, "
        "CASE WHEN $7='confidence_asc' THEN COALESCE(o.predicted_confidence,o.confidence) END ASC NULLS LAST, "
        "CASE WHEN $7='confidence_desc' THEN COALESCE(o.predicted_confidence,o.confidence) END DESC NULLS LAST, "
        "CASE WHEN $7='oldest' THEN o.id END ASC, CASE WHEN $7='newest' THEN o.id END DESC, o.id "
        "LIMIT GREATEST(1,$2::int) OFFSET GREATEST(0,$3::int)",
        status, limit, offset, category, confidence_min, confidence_max, sort, listing_id, media_path,
    )
    total = await pool.fetchval(
        "SELECT count(*) FROM listing_media_ocr o "
        "LEFT JOIN nexus_auto_ocr_decisions auto ON auto.ocr_id=o.id AND auto.undone_at IS NULL "
        "WHERE CASE WHEN $1='pending' THEN o.review_status='pending' "
        " WHEN $1='confirmed' THEN o.review_status='confirmed' "
        " WHEN $1='not_card' THEN o.review_status='not_card' "
        " WHEN $1='automatic' THEN auto.id IS NOT NULL AND o.human_classification IS NULL "
        " WHEN $1='failed' THEN o.status='failed' ELSE TRUE END " + count_filters,
        status, category, confidence_min, confidence_max, listing_id, media_path,
    )
    items = []
    for row in rows:
        confidence = row["predicted_confidence"]
        if confidence is None:
            confidence = row["confidence"]
        items.append({
            "id": row["id"],
            "raw_id": row["raw_id"],
            "listing_id": row["listing_id"],
            "media_path": row["media_path"],
            "status": row["status"],
            "classification": row["classification"],
            "human_classification": row["human_classification"],
            "predicted_classification": row["predicted_classification"] or row["classification"],
            "confidence": float(confidence) if confidence is not None else None,
            "confidence_source": "model" if row["classifier_version_id"] is not None or row["predicted_model_version"] else "legacy_rule",
            "classifier_version_id": row["classifier_version_id"],
            "predicted_model_version": row["predicted_model_version"],
            "classifier_signals": _decode(row["classifier_signals_json"], {}),
            "review_status": row["review_status"],
            "auto_reason": row["auto_reason"] if not row["human_classification"] else None,
            "raw_text": row["raw_text"],
            "normalized_text": row["normalized_text"],
            "fields": _decode(row["fields_json"], {}),
            "evidence": _decode(row["evidence_json"], {}),
            "engine_version": row["engine_version"],
            "error": row["error_text"],
            "message_text": row["message_text"],
            "posted_at": row["posted_at"].isoformat() if row["posted_at"] else None,
            "source_title": row["source_title"],
            "nickname": row["nickname"],
        })
    return {"items": items, "total": total}


@router.get("/automatic-summary")
async def automatic_summary() -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    row = await pool.fetchrow("""
        SELECT count(*) FILTER (WHERE a.undone_at IS NULL AND o.human_classification IS NULL) AS current_count,
               count(*) FILTER (WHERE a.undone_at IS NOT NULL) AS undone_count,
               count(*) FILTER (WHERE o.human_classification IS NOT NULL AND o.human_classification<>a.category) AS corrected_count
        FROM nexus_auto_ocr_decisions a JOIN listing_media_ocr o ON o.id=a.ocr_id
        """)
    return dict(row)


@router.patch("/{asset_id}")
async def edit(asset_id: int, body: EditOcrBody) -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT listing_id, review_status, normalized_text "
                "FROM listing_media_ocr WHERE id=$1 FOR UPDATE",
                asset_id,
            )
            if row is None:
                raise HTTPException(status_code=404, detail="OCR item not found")
            new_text = body.normalized_text if body.normalized_text is not None else row["normalized_text"]
            if row["review_status"] == "confirmed":
                await _sync_listing_ocr_text(
                    conn, row["listing_id"], row["normalized_text"], new_text, True,
                )
            await conn.execute(
                "UPDATE listing_media_ocr SET fields_json=$2::jsonb, "
                "normalized_text=$3, updated_at=NOW() WHERE id=$1",
                asset_id, json.dumps(body.fields, ensure_ascii=False), new_text,
            )
    return {"ok": True}


@router.post("/{asset_id}/decision")
async def decide(
    asset_id: int, body: DecisionBody, authorization: str = Header(default=""),
) -> dict:
    if body.decision not in {"confirm", "not_card", "retry"}:
        raise HTTPException(status_code=400, detail="invalid decision")
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    row = await pool.fetchrow(
        "SELECT raw_id, listing_id, media_path, normalized_text, review_status, fields_json "
        "FROM listing_media_ocr WHERE id=$1",
        asset_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="OCR item not found")

    if body.decision == "retry":
        await pool.execute(
            "UPDATE listing_media_ocr SET status='pending', review_status='pending', "
            "error_text=NULL, updated_at=NOW() WHERE id=$1",
            asset_id,
        )
        if row["raw_id"] is not None:
            await pool.fetch(
                "SELECT media_ocr_set_flags($1,$2,'pending','pending','uncertain')",
                row["raw_id"], row["media_path"],
            )
        return {"ok": True}

    class_code = body.class_code or ("info_card" if body.decision == "confirm" else "other")
    is_card = class_code == "info_card"
    classification, review_status, visibility = (
        (class_code, "confirmed", "admin") if is_card
        else (class_code, "not_card", "public")
    )
    identity = bearer_identity(authorization)
    sha = await asyncio.to_thread(
        review_automation.fingerprint, Path(get_settings().media_dir).resolve(), row["media_path"],
    )
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT raw_id,listing_id,media_path,normalized_text,fields_json FROM listing_media_ocr "
                "WHERE id=$1 FOR UPDATE", asset_id,
            )
            if row is None:
                raise HTTPException(status_code=404, detail="OCR item not found")
            fields = body.fields if body.fields is not None else _decode(row["fields_json"], {})
            normalized_text = body.normalized_text if body.normalized_text is not None else row["normalized_text"]
            await _set_human_label(conn, asset_id, class_code, identity[0] if identity else None)
            await review_automation.record_learning(conn, asset_id, sha)
            await _sync_listing_ocr_text(
                conn, row["listing_id"], row["normalized_text"], normalized_text,
                is_card,
            )
            await conn.execute(
                "UPDATE listing_media_ocr SET status='done', classification=$2, review_status=$3, "
                "fields_json=$4::jsonb, normalized_text=$5, reviewed_by=$6, "
                "reviewed_at=NOW(), updated_at=NOW() WHERE id=$1",
                asset_id, classification, review_status,
                json.dumps(fields, ensure_ascii=False), normalized_text,
                identity[0] if identity else None,
            )
            if row["raw_id"] is not None:
                await conn.fetchval(
                    "SELECT media_ocr_set_flags($1,$2,$3,'done',$4)",
                    row["raw_id"], row["media_path"], visibility, classification,
                )
    return {"ok": True}


@router.post("/{asset_id}/undo-automatic")
async def undo_automatic_ocr(asset_id: int, authorization: str = Header(default="")) -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    identity = bearer_identity(authorization)
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow("SELECT human_classification FROM listing_media_ocr WHERE id=$1 FOR UPDATE", asset_id)
        if row is None or row["human_classification"]:
            raise HTTPException(status_code=409, detail="图片已有人工判断或记录不存在")
        decision = await conn.fetchrow("""
            UPDATE nexus_auto_ocr_decisions SET undone_at=now(),undone_by=$2
            WHERE ocr_id=$1 AND undone_at IS NULL RETURNING previous_category
            """, asset_id, identity[0] if identity else None)
        if decision is None:
            raise HTTPException(status_code=409, detail="没有可撤销的自动判断")
        await conn.execute("""
            UPDATE listing_media_ocr SET classification=COALESCE($2,'uncertain'),review_status='pending',updated_at=now()
            WHERE id=$1
            """, asset_id, decision["previous_category"])
    return {"ok": True}


@router.post("/bulk-decision")
async def bulk_decide(
    body: BulkDecisionBody, authorization: str = Header(default=""),
) -> dict:
    if body.decision not in {"confirm", "not_card"}:
        raise HTTPException(status_code=400, detail="invalid decision")
    if body.status not in {"all", "pending", "confirmed", "not_card", "failed"}:
        raise HTTPException(status_code=400, detail="invalid OCR queue status")
    if any(value is not None and not 0 <= value <= 1 for value in (body.confidence_min, body.confidence_max)):
        raise HTTPException(status_code=400, detail="invalid confidence filter")
    if body.confidence_min is not None and body.confidence_max is not None and body.confidence_min > body.confidence_max:
        raise HTTPException(status_code=400, detail="minimum confidence exceeds maximum")
    if body.all_matching == bool(body.ids):
        raise HTTPException(status_code=400, detail="select ids or all_matching")
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")

    identity = bearer_identity(authorization)
    if body.all_matching:
        rows_sql = (
            "SELECT id, raw_id, listing_id, media_path, normalized_text, review_status "
            "FROM listing_media_ocr o "
            "WHERE CASE WHEN $1='pending' THEN o.review_status='pending' "
            " WHEN $1='confirmed' THEN o.review_status='confirmed' "
            " WHEN $1='not_card' THEN o.review_status='not_card' "
            " WHEN $1='failed' THEN o.status='failed' ELSE TRUE END "
            "AND ($2::text IS NULL OR COALESCE(o.human_classification,o.classification)=$2) "
            "AND ($3::numeric IS NULL OR COALESCE(o.predicted_confidence,o.confidence)>=$3) "
            "AND ($4::numeric IS NULL OR COALESCE(o.predicted_confidence,o.confidence)<=$4) "
            "AND NOT (o.id = ANY($5::bigint[])) ORDER BY o.id"
        )
        query_args = (body.status, body.category, body.confidence_min, body.confidence_max, body.exclude_ids)
    else:
        rows_sql = (
            "SELECT id, raw_id, listing_id, media_path, normalized_text, review_status "
            "FROM listing_media_ocr WHERE id = ANY($1::bigint[]) ORDER BY id"
        )
        query_args = (body.ids,)

    class_code = body.class_code or ("info_card" if body.decision == "confirm" else "other")
    is_card = class_code == "info_card"
    classification = class_code
    review_status = "confirmed" if is_card else "not_card"
    visibility = "admin" if is_card else "public"
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(rows_sql, *query_args)
            for row in rows:
                await _set_human_label(conn, row["id"], class_code, identity[0] if identity else None)
                await _sync_listing_ocr_text(
                    conn, row["listing_id"], row["normalized_text"], row["normalized_text"],
                    is_card,
                )
                await conn.execute(
                    "UPDATE listing_media_ocr SET status='done', classification=$2, review_status=$3, "
                    "reviewed_by=$4, reviewed_at=NOW(), updated_at=NOW() WHERE id=$1",
                    row["id"], classification, review_status,
                    identity[0] if identity else None,
                )
                if row["raw_id"] is not None:
                    await conn.fetchval(
                        "SELECT media_ocr_set_flags($1,$2,$3,'done',$4)",
                        row["raw_id"], row["media_path"], visibility, classification,
                    )
    return {"ok": True, "updated": len(rows)}
