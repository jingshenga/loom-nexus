"""Admin review of Bot links discovered from Telegram profiles."""
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app import db
from app.api.auth import current_user_id, require_admin

router = APIRouter(
    prefix="/api/admin/bots", tags=["admin-bot-review"],
    dependencies=[Depends(require_admin)],
)


class BotDecision(BaseModel):
    status: str
    reason: str = Field(default="", max_length=500)
    candidate_url: str | None = Field(default=None, max_length=256)


@router.get("")
async def list_bot_results(
    status: str = Query(default="all"), limit: int = 100, offset: int = 0, job_id: int | None = None,
) -> dict:
    if status not in {"all", "unverified", "verified", "unrelated", "unavailable", "no_bot", "needs_review"}:
        raise HTTPException(status_code=400, detail="invalid status")
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    rows = await pool.fetch(
        "SELECT b.job_id,b.contact_username,b.profile_url,b.bot_url,b.collected_at, "
        "b.verification_status,b.verification_reason,b.verified_at,b.discovery_json, "
        "j.status AS job_status,j.stage,j.error AS job_error, "
        "(SELECT l.id FROM listings l WHERE lower(l.contact_username)=lower(b.contact_username) "
        " ORDER BY (l.status='published') DESC,l.id DESC LIMIT 1) AS listing_id, "
        "(SELECT l.status FROM listings l WHERE lower(l.contact_username)=lower(b.contact_username) "
        " ORDER BY (l.status='published') DESC,l.id DESC LIMIT 1) AS listing_status "
        "FROM bot_collection_results b JOIN profile_collection_jobs j ON j.id=b.job_id "
        "WHERE ($1='all' OR ($1='no_bot' AND (b.discovery_json->>'outcome'='no_bot' "
        "OR (b.discovery_json='{}'::jsonb AND NULLIF(b.bot_url,'') IS NULL AND j.status='not_found'))) "
        "OR ($1='needs_review' AND b.verification_status='unverified' AND j.status='needs_review') "
        "OR ($1='unverified' AND b.verification_status='unverified' "
        "AND (b.bot_url<>'' OR j.status='needs_review')) "
        "OR ($1<>'unverified' AND b.verification_status=$1)) AND ($4::bigint IS NULL OR b.job_id=$4) "
        "ORDER BY CASE WHEN b.verification_status='unverified' AND (j.status='needs_review' OR NULLIF(b.bot_url,'') IS NOT NULL) THEN 0 ELSE 1 END, "
        "b.collected_at DESC,b.job_id DESC LIMIT $2 OFFSET $3",
        status, max(1, min(limit, 200)), max(0, offset), job_id,
    )
    items = []
    for row in rows:
        item = dict(row)
        if isinstance(item["discovery_json"], str):
            item["discovery_json"] = json.loads(item["discovery_json"])
        items.append(item)
    return {"items": items}


@router.get("/{job_id}/audit")
async def bot_audit(job_id: int) -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    rows = await pool.fetch(
        "SELECT a.id,a.old_status,a.new_status,a.reason,a.chosen_bot_url,a.created_at,u.username "
        "FROM bot_collection_audit a JOIN site_users u ON u.id=a.reviewer_id "
        "WHERE a.job_id=$1 ORDER BY a.id DESC", job_id,
    )
    return {"items": [dict(row) for row in rows]}


@router.post("/{job_id}/decision")
async def decide_bot(job_id: int, body: BotDecision,
                     authorization: str = Header(default="")) -> dict:
    if body.status not in {"verified", "unrelated", "unavailable"}:
        raise HTTPException(status_code=422, detail="invalid decision")
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    reviewer_id = current_user_id(authorization)
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT verification_status,bot_url,discovery_json FROM bot_collection_results "
                "WHERE job_id=$1 FOR UPDATE", job_id,
            )
            if row is None:
                raise HTTPException(status_code=404, detail="Bot采集记录不存在")
            discovery = row["discovery_json"]
            if isinstance(discovery, str):
                discovery = json.loads(discovery)
            url = row["bot_url"] or ""
            if body.status == "verified":
                if body.candidate_url:
                    if body.candidate_url not in (discovery or {}).get("candidate_urls", []):
                        raise HTTPException(status_code=409, detail="只能确认本次发现的 Bot 候选")
                    url = body.candidate_url
                if not url.strip():
                    raise HTTPException(status_code=409, detail="请选择可验证的 Bot 候选")
            await conn.execute(
                "UPDATE bot_collection_results SET verification_status=$2,verification_reason=$3, "
                "verified_by=$4,verified_at=NOW(),bot_url=$5 WHERE job_id=$1",
                job_id, body.status, body.reason.strip(), reviewer_id, url,
            )
            await conn.execute(
                "INSERT INTO bot_collection_audit(job_id,reviewer_id,old_status,new_status,reason,chosen_bot_url) "
                "VALUES($1,$2,$3,$4,$5,$6)",
                job_id, reviewer_id, row["verification_status"], body.status, body.reason.strip(),
                url if body.status == "verified" else None,
            )
    return {"ok": True, "status": body.status}
