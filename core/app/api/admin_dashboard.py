"""Data-backed administrator summary; labels match the underlying counters."""
import json
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request

from app import db
from app.api.auth import require_admin
from app.api.review import SQL_DUPLICATE_CANDIDATES
from app.config import get_settings

router = APIRouter(
    prefix="/api/admin/dashboard", tags=["admin-dashboard"],
    dependencies=[Depends(require_admin)],
)
_DUPLICATE_CTE = SQL_DUPLICATE_CANDIDATES.rsplit("SELECT * FROM candidates", 1)[0]
SQL_DUPLICATE_COUNT = _DUPLICATE_CTE + "SELECT count(*) FROM candidates WHERE group_pair_rank=1"


def _decode_json(value, fallback):
    if value is None:
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return fallback
    return value


async def _recognition_overview(pool, request: Request, now: datetime) -> dict:
    start = now - timedelta(days=30)
    stats = await pool.fetchrow(
        "SELECT COUNT(*) AS ocr_records, "
        "COUNT(*) FILTER (WHERE status='done') AS ocr_done, "
        "COUNT(*) FILTER (WHERE status='done' AND "
        "(NULLIF(BTRIM(raw_text),'') IS NOT NULL OR NULLIF(BTRIM(normalized_text),'') IS NOT NULL)) AS text_records, "
        "COUNT(*) FILTER (WHERE fields_json IS NOT NULL AND fields_json <> '{}'::jsonb "
        "AND fields_json <> '[]'::jsonb) AS field_records, "
        "SUM(CASE WHEN jsonb_typeof(fields_json)='object' THEN "
        "(SELECT COUNT(*) FROM jsonb_object_keys(listing_media_ocr.fields_json)) ELSE 0 END) AS field_value_count, "
        "COUNT(*) FILTER (WHERE predicted_model_version IS NOT NULL) AS vision4_calls, "
        "COUNT(*) FILTER (WHERE classifier_version_id=2) AS classifier_v2_calls, "
        "COUNT(*) FILTER (WHERE human_classification IS NOT NULL AND predicted_classification IS NOT NULL) AS labeled_records, "
        "COUNT(*) FILTER (WHERE human_classification IS NOT NULL AND predicted_classification IS NOT NULL "
        "AND human_classification <> predicted_classification) AS corrected_records, "
        "COUNT(*) FILTER (WHERE listing_id IS NOT NULL OR EXISTS ("
        "SELECT 1 FROM listings l WHERE l.source_msg_id=listing_media_ocr.raw_id)) AS listing_linked, "
        "COUNT(*) FILTER (WHERE review_status IN ('pending','confirmed','not_card')) AS review_involved, "
        "COUNT(*) FILTER (WHERE listing_id IS NOT NULL OR EXISTS ("
        "SELECT 1 FROM listings l WHERE l.source_msg_id=listing_media_ocr.raw_id) "
        "OR review_status IN ('pending','confirmed','not_card')) AS business_involved "
        ",COUNT(*) FILTER (WHERE listing_id IS NULL AND NOT EXISTS ("
        "SELECT 1 FROM listings l WHERE l.source_msg_id=listing_media_ocr.raw_id) "
        "AND review_status='not_required') AS no_flow_not_required "
        "FROM listing_media_ocr WHERE created_at >= $1",
        start,
    )
    active_runs = await pool.fetchrow(
        "SELECT COUNT(*) AS runs, COUNT(*) FILTER (WHERE "
        "jsonb_array_length(COALESCE(result_json->'selected_rule_ids','[]'::jsonb)) > 0) AS rule_hits "
        "FROM rule_parse_runs WHERE run_reason='active' AND created_at >= $1", start,
    )
    shadow_stats = await pool.fetchrow(
        "SELECT COUNT(*) AS comparisons,COUNT(*) FILTER (WHERE changed IS TRUE) AS differences "
        "FROM rule_shadow_comparisons WHERE observed_at >= $1", start,
    )
    # Legacy does not persist a per-message parser run. Attribute ingress by the
    # mode audit effective at Telegram posted_at, and disclose that distinction.
    mode_messages = await pool.fetch(
        "WITH routed AS (SELECT r.id,r.extract_state,COALESCE(("
        "SELECT e.new_mode FROM rule_router_mode_events e WHERE e.source_id=r.source_id "
        "AND e.changed_at <= r.posted_at ORDER BY e.changed_at DESC LIMIT 1), ("
        "SELECT e.previous_mode FROM rule_router_mode_events e WHERE e.source_id=r.source_id "
        "ORDER BY e.changed_at LIMIT 1),s.router_mode,'legacy') AS mode_at_post "
        "FROM raw_messages r JOIN ingest_sources s ON s.id=r.source_id WHERE r.posted_at >= $1) "
        "SELECT mode_at_post,COUNT(*) AS messages,COUNT(*) FILTER (WHERE extract_state IN ('done','dropped')) AS processed "
        "FROM routed GROUP BY mode_at_post ORDER BY mode_at_post", start,
    )
    mode_counts = {
        row["mode_at_post"]: {"messages": row["messages"], "processed": row["processed"]}
        for row in mode_messages
    }
    engine_row = await pool.fetchrow("SELECT value FROM app_settings WHERE key='ocr_classifier_engine'")
    source_rows = await pool.fetch(
        "SELECT id,title,is_active,COALESCE(router_mode,'legacy') AS router_mode "
        "FROM ingest_sources ORDER BY id"
    )
    source_mode_counts = {}
    for source in source_rows:
        if source["is_active"]:
            mode = source["router_mode"]
            source_mode_counts[mode] = source_mode_counts.get(mode, 0) + 1
    classifier = await pool.fetchrow(
        "SELECT id,status FROM ocr_classifier_versions WHERE status='active' ORDER BY id DESC LIMIT 1"
    )
    source2 = await pool.fetchrow(
        "SELECT s.is_active,s.router_mode,g.active_started_at,g.heartbeat_at,g.health_status,"
        "g.last_error,g.last_rollback_reason,g.last_metrics_json,"
        "(SELECT max(r.posted_at) FROM raw_messages r WHERE r.source_id=2) AS latest_message_posted_at,"
        "(SELECT max(rv.observed_at) FROM raw_messages r JOIN raw_message_revisions rv ON rv.raw_id=r.id "
        " WHERE r.source_id=2) AS latest_revision_observed_at,"
        "(SELECT count(*) FROM raw_messages r JOIN raw_message_revisions rv ON rv.raw_id=r.id "
        " WHERE r.source_id=2 AND rv.observed_at>=g.active_started_at) AS new_revisions,"
        "(SELECT count(DISTINCT r.id) FROM raw_messages r JOIN raw_message_revisions rv ON rv.raw_id=r.id "
        " WHERE r.source_id=2 AND rv.observed_at>=g.active_started_at) AS new_raws,"
        "(SELECT count(DISTINCT r.id) FROM raw_messages r JOIN raw_message_revisions rv ON rv.raw_id=r.id "
        " WHERE r.source_id=2 AND rv.observed_at>=g.active_started_at "
        " AND r.extract_state IN ('done','dropped')) AS processed_raws,"
        "(SELECT count(*) FROM rule_parse_runs p JOIN raw_message_revisions rv ON rv.id=p.raw_revision_id "
        " JOIN raw_messages r ON r.id=rv.raw_id WHERE r.source_id=2 AND p.run_reason='active' "
        " AND p.created_at>=g.active_started_at) AS active_runs,"
        "(SELECT count(*) FROM rule_parse_runs p JOIN raw_message_revisions rv ON rv.id=p.raw_revision_id "
        " JOIN raw_messages r ON r.id=rv.raw_id WHERE r.source_id=2 AND p.run_reason='active' "
        " AND p.created_at>=g.active_started_at AND "
        " jsonb_array_length(COALESCE(p.result_json->'selected_rule_ids','[]'::jsonb))>0) AS rule_hits,"
        "(SELECT count(*) FROM listing_media_ocr o JOIN raw_messages r ON r.id=o.raw_id "
        " WHERE r.source_id=2 AND o.created_at>=g.active_started_at) AS ocr_records,"
        "(SELECT count(*) FROM listing_media_ocr o JOIN raw_messages r ON r.id=o.raw_id "
        " WHERE r.source_id=2 AND o.created_at>=g.active_started_at AND o.fields_json IS NOT NULL "
        " AND o.fields_json<>'{}'::jsonb AND o.fields_json<>'[]'::jsonb) AS ocr_field_records,"
        "(SELECT COALESCE(sum((SELECT count(*) FROM jsonb_object_keys(o.fields_json))),0) "
        " FROM listing_media_ocr o "
        " JOIN raw_messages r ON r.id=o.raw_id WHERE r.source_id=2 AND o.created_at>=g.active_started_at "
        " AND jsonb_typeof(o.fields_json)='object') AS ocr_field_values,"
        "(SELECT count(*) FROM rule_active_review_queue q WHERE q.source_id=2 "
        " AND q.created_at>=g.active_started_at) AS review_candidates,"
        "(SELECT count(*) FROM rule_active_review_queue q WHERE q.source_id=2 "
        " AND q.created_at>=g.active_started_at AND "
        " jsonb_array_length(COALESCE(q.candidate_json->'ocr_assist','[]'::jsonb))>0) AS ocr_assisted_candidates,"
        "(SELECT count(*) FROM rule_active_review_queue q CROSS JOIN LATERAL "
        " jsonb_each(COALESCE(q.candidate_json->'evidence','{}'::jsonb)) field "
        " WHERE q.source_id=2 AND q.created_at>=g.active_started_at AND field.value->>'src'='ocr') "
        " AS ocr_adopted_field_values,"
        "(SELECT count(*) FROM rule_active_review_queue q WHERE q.source_id=2 "
        " AND q.created_at>=g.active_started_at AND "
        " jsonb_array_length(COALESCE(q.diagnostics_json->'conflict_kinds','[]'::jsonb))>0) AS conflicted_candidates,"
        "(SELECT count(*) FROM rule_active_review_queue q WHERE q.source_id=2 "
        " AND q.created_at>=g.active_started_at AND q.status='open') AS new_open_reviews,"
        "(SELECT count(*) FROM rule_active_review_queue q WHERE q.source_id=2 AND q.status='open') AS open_reviews,"
        "(SELECT count(*) FROM rule_active_ocr_rechecks x WHERE x.source_id=2 "
        " AND x.created_at>=g.active_started_at) AS ocr_rechecks "
        "FROM ingest_sources s LEFT JOIN active_router_guard_state g ON g.source_id=s.id WHERE s.id=2"
    )
    guard_heartbeat = source2["heartbeat_at"]
    guard_healthy = bool(
        source2["health_status"] == "healthy" and guard_heartbeat
        and (now - guard_heartbeat).total_seconds() <= 90
    )
    if not source2["active_started_at"]:
        source2_diagnosis = "没有找到本次 Active 启用时间，无法计算本次处理量。"
    elif not source2["is_active"] or source2["router_mode"] != "active":
        source2_diagnosis = "来源 2 当前未处于 Active；以下处理量仍按最近一次启用时间统计。"
    elif not guard_healthy:
        source2_diagnosis = "来源 2 当前为 Active，但 Guard 心跳异常或过期；请先检查守护状态。"
    elif source2["new_revisions"] == 0:
        source2_diagnosis = "启用后没有新的来源 2 raw 修订进入数据库，因此尚无消息可供 Active 解析、OCR 或分流。"
    elif source2["active_runs"] == 0:
        source2_diagnosis = "已有新 raw 修订，但没有 Active 解析审计；请检查 Bridge 到 Core 的入站与解析日志。"
    else:
        source2_diagnosis = "已收到并运行 Active；解析、字段和审核数量见下方。"
    active_model = await pool.fetchrow(
        "SELECT version_key,model_sha256 FROM vision4_model_versions WHERE status='active' "
        "ORDER BY activated_at DESC NULLS LAST,id DESC LIMIT 1"
    )
    categories = await pool.fetch(
        "SELECT COALESCE(predicted_classification,classification,'unknown') AS category,COUNT(*) AS count "
        "FROM listing_media_ocr WHERE created_at >= $1 AND predicted_model_version IS NOT NULL "
        "GROUP BY 1 ORDER BY count DESC,category", start,
    )
    processor = getattr(request.app.state, "media_ocr_processor", None)
    vision4 = getattr(processor, "_vision4", None)
    settings = get_settings()
    recent = await pool.fetch(
        "SELECT o.id,o.raw_id,o.listing_id,o.media_path,o.status AS ocr_status,o.review_status,"
        "o.classification,o.human_classification,o.predicted_classification,o.predicted_confidence,"
        "o.classifier_version_id,o.predicted_model_version,o.predicted_model_sha,o.engine_version,"
        "o.raw_text,o.normalized_text,o.fields_json,o.error_text,rm.raw_text AS message_text,"
        "rm.extract_state,rm.rule_version_id AS legacy_rule_version_id,rm.posted_at,"
        "s.id AS source_id,s.title AS source_title,s.router_mode,"
        "CASE WHEN rm.id IS NULL THEN NULL ELSE COALESCE(parse.run_reason,COALESCE((SELECT e.new_mode FROM rule_router_mode_events e "
        "WHERE e.source_id=rm.source_id AND e.changed_at<=rm.posted_at ORDER BY e.changed_at DESC LIMIT 1),"
        "(SELECT e.previous_mode FROM rule_router_mode_events e WHERE e.source_id=rm.source_id "
        "ORDER BY e.changed_at LIMIT 1),s.router_mode,'legacy')) END AS processed_mode,"
        "parse.rule_version_id AS active_rule_version_id,parse.selected_rule_ids,"
        "parse.parser_key,parse.run_reason,li.id AS matched_listing_id,li.status AS listing_status "
        "FROM listing_media_ocr o LEFT JOIN raw_messages rm ON rm.id=o.raw_id "
        "LEFT JOIN ingest_sources s ON s.id=rm.source_id "
        "LEFT JOIN LATERAL (SELECT pr.rule_version_id,pr.parser_key,pr.run_reason,"
        "pr.result_json->'selected_rule_ids' AS selected_rule_ids FROM raw_message_revisions rv "
        "JOIN rule_parse_runs pr ON pr.raw_revision_id=rv.id WHERE rv.raw_id=o.raw_id "
        "AND pr.run_reason='active' ORDER BY pr.created_at DESC,pr.id DESC LIMIT 1) parse ON TRUE "
        "LEFT JOIN LATERAL (SELECT l.id,l.status FROM listings l WHERE l.id=o.listing_id "
        "OR l.source_msg_id=o.raw_id ORDER BY (l.id=o.listing_id) DESC,l.id DESC LIMIT 1) li ON TRUE "
        "WHERE o.created_at >= $1 AND (o.predicted_model_version IS NOT NULL OR o.classifier_version_id IS NOT NULL) "
        "ORDER BY o.updated_at DESC,o.id DESC LIMIT 12", start,
    )
    images = []
    for row in recent:
        confidence = row["predicted_confidence"]
        fields = _decode_json(row["fields_json"], {})
        listing_id = row["matched_listing_id"] or row["listing_id"]
        if row["ocr_status"] == "failed":
            outcome_reason = row["error_text"] or "OCR 处理失败"
        elif listing_id:
            outcome_reason = f"已关联 Listing #{listing_id}"
        elif row["review_status"] == "pending":
            outcome_reason = "已进入人工核对队列，尚未产生 Listing"
        elif row["review_status"] == "confirmed":
            outcome_reason = "图片类型已人工确认，但没有关联 Listing"
        elif row["review_status"] == "not_card":
            outcome_reason = "人工判定为非资料卡"
        elif row["review_status"] == "not_required":
            outcome_reason = "分类策略未要求人工审核，且未关联 Listing"
        else:
            outcome_reason = "没有持久化的 Listing 或审核结果"
        images.append({
            "id": row["id"], "raw_id": row["raw_id"], "media_path": row["media_path"],
            "source_id": row["source_id"], "source_title": row["source_title"],
            "posted_at": row["posted_at"].isoformat() if row["posted_at"] else None,
            "message_text": row["message_text"], "processed_mode": row["processed_mode"],
            "vision4_version": row["predicted_model_version"], "vision4_sha": row["predicted_model_sha"],
            "classification": row["predicted_classification"] or row["classification"],
            "human_classification": row["human_classification"],
            "confidence": float(confidence) if confidence is not None else None,
            "classifier_version_id": row["classifier_version_id"],
            "ocr_status": row["ocr_status"], "ocr_engine": row["engine_version"],
            "ocr_text": row["raw_text"], "normalized_text": row["normalized_text"],
            "fields": fields, "error": row["error_text"], "review_status": row["review_status"],
            "rule_version_id": row["active_rule_version_id"] or row["legacy_rule_version_id"],
            "rule_version_source": "active_parse" if row["active_rule_version_id"] else
                                   "legacy_raw" if row["legacy_rule_version_id"] else None,
            "selected_rule_ids": _decode_json(row["selected_rule_ids"], []),
            "parser_key": row["parser_key"], "run_reason": row["run_reason"],
            "legacy_extract_state": row["extract_state"],
            "listing_id": listing_id,
            "listing_status": row["listing_status"],
            "outcome_reason": outcome_reason,
        })
    vision_loaded = bool(vision4 and vision4._session is not None and not vision4._load_error)
    return {
        "window_days": 30, "window_start": start.isoformat(),
        "configuration": {
            "vision4_mode": settings.vision4_mode,
            "ocr_classifier_engine": engine_row["value"] if engine_row else None,
            "vision4_loaded": vision_loaded,
            "vision4_runtime_version": vision4._manifest.get("model_version") if vision4 and vision4._manifest else None,
            "vision4_active_version": active_model["version_key"] if active_model else None,
            "vision4_active_sha": active_model["model_sha256"] if active_model else None,
            "classifier_active_id": classifier["id"] if classifier else None,
            "classifier_active_status": classifier["status"] if classifier else None,
            "active_router_enabled": settings.active_router_enabled,
            "active_router_source_ids": settings.active_router_source_ids,
            "active_router_kill_switch": settings.active_router_kill_switch,
            "ocr_review_assist_enabled": settings.ocr_review_assist_enabled,
            "source_modes": source_mode_counts,
            "sources": [{"id": row["id"], "title": row["title"], "is_active": row["is_active"],
                         "router_mode": row["router_mode"]} for row in source_rows],
        },
        "source2_activity": {
            "source_id": 2, "is_active": source2["is_active"], "router_mode": source2["router_mode"],
            "active_started_at": source2["active_started_at"].isoformat() if source2["active_started_at"] else None,
            "guard_health_status": source2["health_status"], "guard_healthy": guard_healthy,
            "guard_heartbeat_at": guard_heartbeat.isoformat() if guard_heartbeat else None,
            "guard_last_error": source2["last_error"],
            "guard_last_rollback_reason": source2["last_rollback_reason"],
            "last_metrics": _decode_json(source2["last_metrics_json"], {}),
            "latest_message_posted_at": source2["latest_message_posted_at"].isoformat()
            if source2["latest_message_posted_at"] else None,
            "latest_revision_observed_at": source2["latest_revision_observed_at"].isoformat()
            if source2["latest_revision_observed_at"] else None,
            "metrics": {
                "new_raws": source2["new_raws"], "new_revisions": source2["new_revisions"],
                "processed_raws": source2["processed_raws"], "active_runs": source2["active_runs"],
                "rule_hits": source2["rule_hits"], "ocr_records": source2["ocr_records"],
                "ocr_field_records": source2["ocr_field_records"],
                "ocr_field_values": source2["ocr_field_values"],
                "review_candidates": source2["review_candidates"],
                "ocr_assisted_candidates": source2["ocr_assisted_candidates"],
                "ocr_adopted_field_values": source2["ocr_adopted_field_values"],
                "conflicted_candidates": source2["conflicted_candidates"],
                "new_open_reviews": source2["new_open_reviews"],
                "open_reviews": source2["open_reviews"], "ocr_rechecks": source2["ocr_rechecks"],
            },
            "diagnosis": source2_diagnosis,
        },
        "metrics": {
            "vision4_calls": stats["vision4_calls"], "classifier_v2_calls": stats["classifier_v2_calls"],
            "ocr_text_records": stats["text_records"], "ocr_done": stats["ocr_done"],
            "field_records": stats["field_records"],
            "field_value_count": stats["field_value_count"] or 0,
            "field_rate": round(stats["field_records"] * 100 / stats["ocr_done"], 1) if stats["ocr_done"] else 0,
            "ocr_adopted": stats["business_involved"],
            "ocr_adoption_rate": round(stats["business_involved"] * 100 / stats["ocr_done"], 1)
            if stats["ocr_done"] else 0,
            "business_involved": stats["business_involved"], "listing_linked": stats["listing_linked"],
            "not_business_involved": stats["ocr_records"] - stats["business_involved"],
            "no_flow_not_required": stats["no_flow_not_required"],
            "review_involved": stats["review_involved"], "labeled_records": stats["labeled_records"],
            "corrected_records": stats["corrected_records"],
            "correction_rate": round(stats["corrected_records"] * 100 / stats["labeled_records"], 1)
            if stats["labeled_records"] else 0,
            "active_runs": active_runs["runs"], "active_rule_hits": active_runs["rule_hits"],
            "shadow_comparisons": shadow_stats["comparisons"],
            "shadow_differences": shadow_stats["differences"],
            "legacy_messages": mode_counts.get("legacy", {}).get("messages", 0),
            "legacy_processed_messages": mode_counts.get("legacy", {}).get("processed", 0),
            "active_messages": mode_counts.get("active", {}).get("messages", 0),
            "active_processed_messages": mode_counts.get("active", {}).get("processed", 0),
            "shadow_messages": mode_counts.get("shadow", {}).get("messages", 0),
            "shadow_processed_messages": mode_counts.get("shadow", {}).get("processed", 0),
        },
        "metric_scope": {
            "window": "最近 30 天，按记录创建时间；当前时间以服务器 UTC 为准",
            "vision4_calls": "listing_media_ocr.predicted_model_version 非空的持久化分类结果，不等同于模型加载次数",
            "classifier_v2_calls": "classifier_version_id=2 的持久化预测结果",
            "field_rate": "OCR done 记录中 fields_json 非空占比，不代表字段准确率",
            "ocr_adoption_rate": "OCR done 记录中关联 Listing 或进入图片人工分类审核的比例，不代表字段准确率",
            "field_value_count": "OCR done 记录 fields_json 对象的键数量总和，不代表字段准确性",
            "shadow_differences": "近 30 天 Shadow 对比 changed=true 的次数；仅作新旧差异，不代表新流程更准确",
            "correction_rate": "存在人工分类标签的记录中，人工分类与模型预测不同的比例，不是字段纠错率",
            "business_involved": "有直接/消息关联 Listing，或 review_status 为 pending/confirmed/not_card 的去重记录数",
            "mode_messages": "按来源模式审计事件映射到 Telegram posted_at；Legacy 没有逐消息 parser-run 审计，Active 实际执行数另列 rule_parse_runs",
        },
        "routing": {"mode_messages": mode_counts,
                    "active_parser_runs": active_runs["runs"], "active_rule_hits": active_runs["rule_hits"]},
        "category_distribution": [{"category": row["category"], "count": row["count"]}
                                  for row in categories],
        "recent_images": images,
        "assist_status": "active_sources" if any(
            row["is_active"] and row["router_mode"] == "active" for row in source_rows
        ) else "all_sources_legacy",
    }


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
    recognition = await _recognition_overview(pool, request, now)
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
        "recognition": recognition,
        "actions": [
            {"to": "/admin/review", "label": "打开 Listing 审核", "alert": rows["pending_listings"] > 0},
            {"to": "/admin/rule-review", "label": "处理规则待审", "alert": rows["open_rule_reviews"] > 0},
            {"to": "/admin/lifecycle", "label": "处理 Lifecycle", "alert": rows["open_lifecycle_reviews"] > 0},
            {"to": "/admin/ocr", "label": "核对 OCR 记录", "alert": False},
        ],
    }
