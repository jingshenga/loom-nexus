"""Admin-only review queue for locally classified OCR card images."""
import json

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import db
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
    fields: dict | None = None
    normalized_text: str | None = None


class BulkDecisionBody(BaseModel):
    decision: str
    ids: list[int] = Field(default_factory=list)
    all_matching: bool = False
    status: str = "pending"
    exclude_ids: list[int] = Field(default_factory=list)


def _decode(value, fallback):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value) if value is not None else fallback
    except (TypeError, ValueError):
        return fallback


async def _sync_listing_ocr_text(conn, listing_id, old_text, new_text, include):
    if listing_id is None:
        return
    listing = await conn.fetchrow(
        "SELECT description FROM listings WHERE id=$1 FOR UPDATE", listing_id,
    )
    if listing is None:
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
async def queue(status: str = "pending", limit: int = 40, offset: int = 0) -> dict:
    if status not in {"all", "pending", "confirmed", "not_card", "failed"}:
        raise HTTPException(status_code=400, detail="invalid OCR queue status")
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    rows = await pool.fetch(
        "SELECT o.id, o.raw_id, o.listing_id, o.media_path, o.status, "
        "o.classification, o.review_status, o.raw_text, o.normalized_text, "
        "o.fields_json, o.evidence_json, o.confidence, o.engine_version, "
        "o.error_text, COALESCE(rm.raw_text, l.description) AS message_text, "
        "COALESCE(rm.posted_at, l.created_at) AS posted_at, "
        "COALESCE(s.title, CASE WHEN l.duty_collection_job_id IS NOT NULL "
        "THEN '签到资料自动采集' END) AS source_title, l.nickname "
        "FROM listing_media_ocr o "
        "LEFT JOIN raw_messages rm ON rm.id=o.raw_id "
        "LEFT JOIN ingest_sources s ON s.id=rm.source_id "
        "LEFT JOIN LATERAL (SELECT id, nickname, description, created_at, duty_collection_job_id "
        " FROM listings WHERE id=o.listing_id OR source_msg_id=o.raw_id "
        " ORDER BY CASE WHEN id=o.listing_id THEN 0 ELSE 1 END, id DESC LIMIT 1) l ON TRUE "
        "WHERE CASE WHEN $1='pending' THEN o.review_status='pending' "
        " WHEN $1='confirmed' THEN o.review_status='confirmed' "
        " WHEN $1='not_card' THEN o.review_status='not_card' "
        " WHEN $1='failed' THEN o.status='failed' ELSE TRUE END "
        "ORDER BY CASE WHEN o.status='failed' THEN 0 ELSE 1 END, o.id "
        "LIMIT GREATEST(1,$2::int) OFFSET GREATEST(0,$3::int)",
        status, limit, offset,
    )
    total = await pool.fetchval(
        "SELECT count(*) FROM listing_media_ocr o "
        "WHERE CASE WHEN $1='pending' THEN o.review_status='pending' "
        " WHEN $1='confirmed' THEN o.review_status='confirmed' "
        " WHEN $1='not_card' THEN o.review_status='not_card' "
        " WHEN $1='failed' THEN o.status='failed' ELSE TRUE END",
        status,
    )
    items = []
    for row in rows:
        items.append({
            "id": row["id"],
            "raw_id": row["raw_id"],
            "listing_id": row["listing_id"],
            "media_path": row["media_path"],
            "status": row["status"],
            "classification": row["classification"],
            "review_status": row["review_status"],
            "raw_text": row["raw_text"],
            "normalized_text": row["normalized_text"],
            "fields": _decode(row["fields_json"], {}),
            "evidence": _decode(row["evidence_json"], {}),
            "confidence": float(row["confidence"]),
            "engine_version": row["engine_version"],
            "error": row["error_text"],
            "message_text": row["message_text"],
            "posted_at": row["posted_at"].isoformat() if row["posted_at"] else None,
            "source_title": row["source_title"],
            "nickname": row["nickname"],
        })
    return {"items": items, "total": total}


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

    if body.decision == "confirm":
        classification, review_status, visibility = "info_card", "confirmed", "admin"
    else:
        classification, review_status, visibility = "other", "not_card", "public"
    identity = bearer_identity(authorization)
    fields = body.fields if body.fields is not None else _decode(row["fields_json"], {})
    normalized_text = body.normalized_text if body.normalized_text is not None else row["normalized_text"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _sync_listing_ocr_text(
                conn, row["listing_id"], row["normalized_text"], normalized_text,
                body.decision == "confirm",
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


@router.post("/bulk-decision")
async def bulk_decide(
    body: BulkDecisionBody, authorization: str = Header(default=""),
) -> dict:
    if body.decision not in {"confirm", "not_card"}:
        raise HTTPException(status_code=400, detail="invalid decision")
    if body.status not in {"all", "pending", "confirmed", "not_card", "failed"}:
        raise HTTPException(status_code=400, detail="invalid OCR queue status")
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
            "AND NOT (o.id = ANY($2::bigint[])) ORDER BY o.id"
        )
        query_args = (body.status, body.exclude_ids)
    else:
        rows_sql = (
            "SELECT id, raw_id, listing_id, media_path, normalized_text, review_status "
            "FROM listing_media_ocr WHERE id = ANY($1::bigint[]) ORDER BY id"
        )
        query_args = (body.ids,)

    classification = "info_card" if body.decision == "confirm" else "other"
    review_status = "confirmed" if body.decision == "confirm" else "not_card"
    visibility = "admin" if body.decision == "confirm" else "public"
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(rows_sql, *query_args)
            for row in rows:
                await _sync_listing_ocr_text(
                    conn, row["listing_id"], row["normalized_text"], row["normalized_text"],
                    body.decision == "confirm",
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
