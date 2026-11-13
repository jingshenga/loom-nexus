"""Admin review of Bot links discovered from Telegram profiles."""
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


@router.get("")
async def list_bot_results(
    status: str = Query(default="all"), limit: int = 100, offset: int = 0,
) -> dict:
    if status not in {"all", "unverified", "verified", "unrelated", "unavailable", "no_bot"}:
        raise HTTPException(status_code=400, detail="invalid status")
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    rows = await pool.fetch(
        "SELECT b.job_id,b.contact_username,b.profile_url,b.bot_url,b.collected_at, "
        "b.verification_status,b.verification_reason,b.verified_at, "
        "j.status AS job_status,j.stage,j.error AS job_error, "
        "(SELECT l.id FROM listings l WHERE lower(l.contact_username)=lower(b.contact_username) "
        " ORDER BY (l.status='published') DESC,l.id DESC LIMIT 1) AS listing_id, "
        "(SELECT l.status FROM listings l WHERE lower(l.contact_username)=lower(b.contact_username) "
        " ORDER BY (l.status='published') DESC,l.id DESC LIMIT 1) AS listing_status "
        "FROM bot_collection_results b JOIN profile_collection_jobs j ON j.id=b.job_id "
        "WHERE ($1='all' OR ($1='no_bot' AND NULLIF(b.bot_url,'') IS NULL) "
        "OR ($1<>'no_bot' AND b.verification_status=$1)) "
        "ORDER BY CASE WHEN b.verification_status='unverified' AND NULLIF(b.bot_url,'') IS NOT NULL THEN 0 ELSE 1 END, "
        "b.collected_at DESC,b.job_id DESC LIMIT $2 OFFSET $3",
        status, max(1, min(limit, 200)), max(0, offset),
    )
    return {"items": [dict(row) for row in rows]}


@router.get("/{job_id}/audit")
async def bot_audit(job_id: int) -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    rows = await pool.fetch(
        "SELECT a.id,a.old_status,a.new_status,a.reason,a.created_at,u.username "
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
                "SELECT verification_status,bot_url FROM bot_collection_results "
                "WHERE job_id=$1 FOR UPDATE", job_id,
            )
            if row is None:
                raise HTTPException(status_code=404, detail="Bot采集记录不存在")
            if body.status == "verified" and not (row["bot_url"] or "").strip():
                raise HTTPException(status_code=409, detail="没有可验证的 Bot 候选")
            await conn.execute(
                "UPDATE bot_collection_results SET verification_status=$2,verification_reason=$3, "
                "verified_by=$4,verified_at=NOW() WHERE job_id=$1",
                job_id, body.status, body.reason.strip(), reviewer_id,
            )
            await conn.execute(
                "INSERT INTO bot_collection_audit(job_id,reviewer_id,old_status,new_status,reason) "
                "VALUES($1,$2,$3,$4,$5)",
                job_id, reviewer_id, row["verification_status"], body.status, body.reason.strip(),
            )
    return {"ok": True, "status": body.status}
