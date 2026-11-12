"""Data-backed administrator summary; labels match the underlying counters."""
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request

from app import db
from app.api.auth import require_admin
from app.api.review import SQL_DUPLICATE_CANDIDATES

router = APIRouter(
    prefix="/api/admin/dashboard", tags=["admin-dashboard"],
    dependencies=[Depends(require_admin)],
)
_DUPLICATE_CTE = SQL_DUPLICATE_CANDIDATES.rsplit("SELECT * FROM candidates", 1)[0]
SQL_DUPLICATE_COUNT = _DUPLICATE_CTE + "SELECT count(*) FROM candidates WHERE group_pair_rank=1"


@router.get("")
async def dashboard(request: Request) -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    now = datetime.now(timezone.utc)
    today = datetime.combine(now.astimezone(ZoneInfo("Asia/Shanghai")).date(), time.min,
                             tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(timezone.utc)
    rows = await pool.fetchrow(
        "SELECT "
        "(SELECT count(*) FROM raw_messages WHERE posted_at >= $1) AS raw_today, "
        "(SELECT count(*) FROM rule_parse_runs WHERE run_reason='active' AND created_at >= $1) AS active_runs_today, "
        "(SELECT count(*) FROM rule_parse_runs WHERE run_reason='active' AND created_at >= $1 "
        " AND jsonb_array_length(COALESCE(result_json->'selected_rule_ids','[]'::jsonb)) > 0) AS rule_hits_today, "
        "(SELECT count(*) FROM listing_media_ocr WHERE updated_at >= $1) AS ocr_today, "
        "(SELECT count(*) FROM listings WHERE status='pending') AS pending_listings, "
        "(SELECT count(*) FROM listings WHERE status='published') AS published_listings, "
        "(SELECT count(*) FROM rule_active_review_queue WHERE status='open') AS open_rule_reviews, "
        "(SELECT count(*) FROM lifecycle_review_events WHERE status='pending') AS open_lifecycle_reviews, "
        "(SELECT count(*) FROM listings WHERE auto_published IS TRUE AND created_at >= $1) AS auto_published_today",
        today,
    )
    sources = await pool.fetch(
        "SELECT s.id,s.title,s.router_mode,max(r.posted_at) AS last_message_at "
        "FROM ingest_sources s LEFT JOIN raw_messages r ON r.source_id=s.id "
        "GROUP BY s.id,s.title,s.router_mode ORDER BY max(r.posted_at) DESC NULLS LAST,s.id LIMIT 6"
    )
    bridge = await pool.fetchrow(
        "SELECT count(*) AS accounts,max(last_active_at) AS last_active_at FROM bridge_accounts"
    )
    try:
        duplicate_candidates = await pool.fetchval(SQL_DUPLICATE_COUNT)
    except Exception:
        duplicate_candidates = None
    try:
        await pool.fetchval("SELECT 1")
        db_ok = True
    except Exception:
        db_ok = False
    processor = getattr(request.app.state, "media_ocr_processor", None)
    vision4 = getattr(processor, "_vision4", None)
    vision_ok = bool(vision4 and vision4._session is not None and not vision4._load_error)
    active_mode = await pool.fetchval(
        "SELECT COALESCE(bool_or(router_mode='active'),FALSE) FROM ingest_sources"
    )
    return {
        "as_of": now.isoformat(),
        "metrics": {
            "raw_today": rows["raw_today"],
            "active_runs_today": rows["active_runs_today"],
            "rule_hits_today": rows["rule_hits_today"],
            "ocr_today": rows["ocr_today"],
            "review_open": rows["pending_listings"] + rows["open_rule_reviews"] + rows["open_lifecycle_reviews"],
            "published_listings": rows["published_listings"],
            "duplicate_candidates": duplicate_candidates,
            "auto_published_today": rows["auto_published_today"],
        },
        "metric_scope": {
            "raw_today": "Telegram posted_at since midnight Asia/Shanghai",
            "rule_hits_today": "Active parse runs with selected_rule_ids; Legacy does not persist equivalent parse runs",
            "ocr_today": "listing_media_ocr rows updated since midnight Asia/Shanghai",
            "review_open": "pending listings + open Active review queue + pending Lifecycle events",
            "duplicate_candidates": "current rows from the existing duplicate-candidate query",
        },
        "health": [
            {"name": "Core / API", "ok": True, "status": "在线", "detail": "本页请求成功"},
            {"name": "PostgreSQL", "ok": db_ok, "status": "正常" if db_ok else "异常", "detail": "连接探测 SELECT 1"},
            {"name": "Bridge", "ok": False, "status": "状态未知", "detail": f"无直接健康探测；账号 {bridge['accounts']} 个，最近活跃 {bridge['last_active_at'].isoformat() if bridge['last_active_at'] else '无记录'}"},
            {"name": "Vision4", "ok": vision_ok, "status": "已加载" if vision_ok else "未加载/异常", "detail": vision4._manifest.get("model_version") if vision4 and vision4._manifest else None},
            {"name": "Router", "ok": not active_mode, "status": "有来源 Active" if active_mode else "来源 Legacy", "detail": "来源模式以数据库当前值为准"},
        ],
        "recent_sources": [
            {"id": row["id"], "title": row["title"], "router_mode": row["router_mode"],
             "last_message_at": row["last_message_at"].isoformat() if row["last_message_at"] else None}
            for row in sources
        ],
        "actions": [
            {"to": "/admin/review", "label": "打开 Listing 审核", "alert": rows["pending_listings"] > 0},
            {"to": "/admin/rule-review", "label": "处理规则待审", "alert": rows["open_rule_reviews"] > 0},
            {"to": "/admin/lifecycle", "label": "处理 Lifecycle", "alert": rows["open_lifecycle_reviews"] > 0},
            {"to": "/admin/ocr", "label": "核对 OCR 记录", "alert": False},
        ],
    }
