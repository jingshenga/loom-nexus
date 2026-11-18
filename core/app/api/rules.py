"""AI rule workshop APIs: CRUD, sandbox dry-run, LLM rule generation.

Dry-run reuses the exact pure functions the live extractor runs
(app.extractor.ast_rules). LLM access goes through app.llm (DB-first
config, validated endpoints).
"""
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import db, llm, sqlres
from app.api.auth import current_user_id, require_admin
from app.config import get_settings
from app.extractor import ast_rules
from app.extractor.ocr_fields import equivalent_field
from app.extractor.rule_config import PARSER_BY_TYPE, validate_rule_config, validate_match_mode
from app.extractor.rule_router import business_candidate_key, config_hash, shadow_compare
from app.extractor.rule_router import active_source_allowed

router = APIRouter(
    prefix="/api/admin/rules", tags=["admin-rules"],
    dependencies=[Depends(require_admin)],
)

SQL_UPSERT_VERSIONED = sqlres.load("rule_upsert_versioned.sql")
SQL_DELETE = sqlres.load("rule_delete.sql")
SQL_LIST = sqlres.load("rule_list.sql")
SQL_UNBIND = sqlres.load("rule_unbind.sql")
SQL_SAMPLES = sqlres.load("dryrun_samples.sql")
SQL_REQUEUE = sqlres.load("requeue_source.sql")
SQL_COPY = sqlres.load("rule_copy.sql")
SQL_DUTY_STATUS = sqlres.load("duty_rule_status.sql")
SQL_DUTY_COLLECTION_SOURCES = sqlres.load("duty_collection_sources.sql")
SQL_DUTY_COLLECTION_SOURCE_VALIDATE = sqlres.load("duty_collection_source_validate.sql")
SQL_PROFILE_COLLECTION_RESULTS = sqlres.load("profile_collection_results.sql")
SQL_PROFILE_COLLECTION_SUMMARY = sqlres.load("profile_collection_summary.sql")
SQL_PROFILE_COLLECTION_ENQUEUE = sqlres.load("profile_collection_enqueue_manual.sql")
SQL_PROFILE_COLLECTION_REQUEUE = sqlres.load("profile_collection_requeue_ineffective.sql")
SQL_BOT_COLLECTION_RESULTS = sqlres.load("bot_collection_results.sql")
SQL_CHECKIN_HISTORY_STATUS = sqlres.load("checkin_history_status.sql")
SQL_BIND_VERSIONED = sqlres.load("rule_bind_versioned.sql")
SQL_SHADOW_BINDINGS = sqlres.load("rule_shadow_bindings.sql")
SQL_ACTIVE_REVIEW_CLASSIFIED_CTE = (
    "WITH classified AS ("
    " SELECT q.id,q.source_id,s.title AS source_title,q.raw_id,r.platform_msg_id,"
    " r.posted_at,LEFT(COALESCE(r.raw_text,''),1000) AS message_preview,"
    " COALESCE(r.media_json,'[]'::jsonb) AS media,q.reason,q.candidate_rule_ids,"
    " q.diagnostics_json,q.candidate_json,q.status,q.listing_id,q.created_at,q.reviewed_at,q.last_action,"
    " CASE WHEN q.reason <> 'no_rule_match' "
    "   OR COALESCE(q.candidate_json->'fields','{}'::jsonb) <> '{}'::jsonb "
    "   OR COALESCE(q.candidate_json->>'identity','') <> '' "
    "   OR COALESCE(ocr.structured,false) "
    "   OR COALESCE(r.raw_text,'') ~* '(@[a-z][a-z0-9_]{4,31}|t\\.me/[a-z][a-z0-9_]{4,31}|telegram\\.me/[a-z][a-z0-9_]{4,31})' "
    "   OR (COALESCE(r.raw_text,'') ~* '(价格|售价|月租|日租|租金|[￥¥][[:space:]]*[0-9]|[0-9]{3,}[[:space:]]*(元|块))' "
    "       AND COALESCE(r.raw_text,'') ~* '(昵称|区域|地址|位置|房型|面积|介绍)') "
    "   OR COALESCE(ocr.identity_evidence,false) "
    "   OR EXISTS(SELECT 1 FROM listings l WHERE l.source_msg_id=r.id) "
    " THEN 'review' ELSE 'unmatched' END AS category "
    " FROM rule_active_review_queue q JOIN ingest_sources s ON s.id=q.source_id "
    " JOIN raw_messages r ON r.id=q.raw_id "
    " LEFT JOIN LATERAL (SELECT "
    "   bool_or(COALESCE(fields_json->>'username','') <> '' OR COALESCE(fields_json->>'nickname','') <> '' "
    "     OR COALESCE(fields_json->>'price_amount','') <> '' OR COALESCE(fields_json->>'district','') <> '' "
    "     OR COALESCE(raw_text,'') ~* '(价格|售价|月租|日租|[￥¥][[:space:]]*[0-9]|[0-9]{3,}[[:space:]]*(元|块))') AS structured,"
    "   bool_or(COALESCE(fields_json->>'username','') <> '' "
    "     OR COALESCE(raw_text,'') ~* '(t\\.me/[a-z][a-z0-9_]{4,31}|telegram\\.me/[a-z][a-z0-9_]{4,31}|@[a-z][a-z0-9_]{4,31})') AS identity_evidence "
    "   FROM listing_media_ocr WHERE raw_id=r.id AND status='done') ocr ON TRUE"
    ") "
)

LLM_SYSTEM_PROMPT = (
    "你是消息抽取规则生成器。根据用户给出的样本消息，产出一个 JSON 规则对象，"
    "结构固定为：{\"gate\": {\"require_any_keywords\": [], \"blocked_sender_peer_ids\": []}, "
    "\"extract\": {\"nickname\": [正则...], \"username\": [正则...], \"price\": [正则...], "
    "\"district\": [正则...], \"currency_map\": {\"模式\": \"CNY\"}}, "
    "\"ocr\": {\"enabled\": true, \"trigger_when_missing\": [\"price\"]}, "
    "\"lifecycle_terms\": [\"已售\"]}。"
    "正则必须带一个捕获组捕获字段值，用 Python re 语法，中文可直接出现。"
    "只输出 JSON 本身，不要任何解释或代码围栏。"
)


class RuleBody(BaseModel):
    rule_name: str
    rule_type: Literal["listing", "on_duty", "profile_collection", "bot_collection", "checkin_reverse"] = "listing"
    config: dict
    is_active: bool = True
    expected_version: int | None = None
    template_version_id: int | None = None
    parser_key: str | None = None
    change_summary: str = ""


class BindBody(BaseModel):
    source_id: int
    priority: int = Field(default=10, ge=-100000, le=100000)
    purpose: str = Field(default="", max_length=64)
    match_group: str = Field(default="default", min_length=1, max_length=64)
    match_mode: str = "legacy_single_selected"
    fallback_order: int = Field(default=0, ge=0, le=100000)
    rule_version_id: int | None = None


class ReextractBody(BaseModel):
    source_id: int


class DryRunBody(BaseModel):
    config: dict
    sample_limit: int = 50


class GenerateBody(BaseModel):
    samples: list


class CopyBody(BaseModel):
    rule_name: str


class TemplateBody(BaseModel):
    template_name: str
    rule_type: Literal["listing", "on_duty", "profile_collection", "bot_collection", "checkin_reverse"]
    config: dict
    description: str = ""
    parser_key: str | None = None
    change_summary: str = ""


class TemplateVersionBody(BaseModel):
    config: dict
    parser_key: str | None = None
    change_summary: str = ""


class TemplateInstanceBody(BaseModel):
    template_version_id: int
    rule_name: str
    change_summary: str = ""


class RollbackBody(BaseModel):
    target_version: int
    change_summary: str = ""


class RouterModeBody(BaseModel):
    mode: Literal["legacy", "shadow", "active"]


class RouterDryRunBody(BaseModel):
    source_id: int
    purpose: str = "listing"
    raw_message_ids: list[int] = Field(default_factory=list)
    sample_limit: int = 50
    rule_version_id: int | None = None


class ActiveReviewResolutionBody(BaseModel):
    action: Literal["dismiss", "no_action", "create_pending_listing"]
    reason: str = Field(default="", max_length=500)
    confirmed_fields: dict | None = None


class LifecycleResolutionBody(BaseModel):
    action: Literal["approve", "reject"]
    listing_id: int | None = None
    reason: str = Field(default="", max_length=500)


class ShadowPruneBody(BaseModel):
    older_than_days: int = Field(default=30, ge=7, le=365)
    batch_size: int = Field(default=250, ge=1, le=1000)
    max_batches: int = Field(default=4, ge=1, le=8)


class ProfileCollectionEnqueueBody(BaseModel):
    telegram_username: str = ""
    telegram_usernames: list[str] = Field(default_factory=list)
    source_id: int | None = None


def _pool():
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    return pool


def _decode_jsonb(value, fallback):
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


@router.get("")
async def list_rules() -> dict:
    pool = _pool()
    rows = await pool.fetch(SQL_LIST)
    collection_sources = await pool.fetch(SQL_DUTY_COLLECTION_SOURCES)
    statuses = {
        r["rule_id"]: r
        for r in await pool.fetch(SQL_DUTY_STATUS)
    }
    return {
        "items": [
            {
                "id": r["rule_id"],
                "rule_name": r["rule_name"],
                "rule_type": r["rule_type"],
                "version": r["version"],
                "config": _decode_jsonb(r["config_json"], {}),
                "is_active": r["is_active"],
                "bindings": _decode_jsonb(r["bindings"], []),
                "duty_status": ({
                    "observed_at": statuses[r["rule_id"]]["observed_at"].isoformat()
                    if statuses.get(r["rule_id"]) and statuses[r["rule_id"]]["observed_at"]
                    else None,
                    "matched": statuses[r["rule_id"]]["matched"],
                    "green_blue": statuses[r["rule_id"]]["green_blue"],
                    "green_only": statuses[r["rule_id"]]["green_only"],
                    "with_card": statuses[r["rule_id"]]["with_card"],
                    "queued": statuses[r["rule_id"]]["queued"],
                    "fetched": statuses[r["rule_id"]]["fetched"],
                    "failed": statuses[r["rule_id"]]["failed"],
                    "unqueued": statuses[r["rule_id"]]["unqueued"],
                    "unresolved": statuses[r["rule_id"]]["unresolved"],
                } if r["rule_type"] == "on_duty" else None),
            }
            for r in rows
        ],
        "collection_sources": [
            {"id": r["source_id"], "title": r["title"], "username": r["username"]}
            for r in collection_sources
        ],
    }


@router.put("")
async def upsert_rule(body: RuleBody, authorization: str = Header(default="")) -> dict:
    pool = _pool()
    parser_key = body.parser_key or body.config.get("parser_key") or PARSER_BY_TYPE[body.rule_type]
    body.config["parser_key"] = parser_key
    if body.rule_type == "profile_collection":
        body.config.pop("stale_channel_days", None)
        body.config.setdefault("pinned_followup_messages", 5)
        limits = {
            "recent_window": (1, 10),
            "max_scan_messages": (10, 100),
            "max_photos": (1, 6),
            "max_videos": (0, 2),
            "intro_min_chars": (1, 500),
            "intro_min_lines": (1, 30),
            "pinned_followup_messages": (1, 5),
        }
        for key, (minimum, maximum) in limits.items():
            value = body.config.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
                raise HTTPException(
                    status_code=422,
                    detail=f"资料深采配置 {key} 必须在 {minimum}–{maximum} 之间",
                )
        for key in ("channel_any_sender_matches", "skip_video_when_photo_found", "exclude_avatar"):
            if body.config.get(key) is not True:
                raise HTTPException(status_code=422, detail=f"资料深采配置 {key} 必须启用")
    if body.rule_type == "checkin_reverse":
        body.config.pop("max_age_days", None)
        source_username = str(body.config.get("source_username") or "").strip().lstrip("@").lower()
        if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", source_username):
            raise HTTPException(status_code=422, detail="请填写公开签到来源频道/群组用户名")
        body.config["source_username"] = source_username
        body.config.setdefault("collection_mode", "checkin_source_reverse")
        if body.config["collection_mode"] != "checkin_source_reverse":
            raise HTTPException(status_code=422, detail="签到反查规则模式无效")
    if body.rule_type == "on_duty":
        required = ("sender_peer_id", "match_prefix", "update_mode", "identity_mode")
        missing = [key for key in required if not body.config.get(key)]
        if body.config.get("identity_mode") == "linked_listing_messages" \
                and not body.config.get("target_channel_username"):
            missing.append("target_channel_username")
        if missing:
            raise HTTPException(
                status_code=422, detail=f"签到规则缺少配置：{', '.join(missing)}",
            )
        if body.config.get("auto_collect_enabled", True):
            try:
                collection_source_id = int(body.config.get("collection_source_id"))
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail="请选择自动补采的名片来源")
            if not await pool.fetch(SQL_DUTY_COLLECTION_SOURCE_VALIDATE, collection_source_id):
                raise HTTPException(status_code=422, detail="自动补采来源需启用且绑定名片抓取规则")
    errors = validate_rule_config(body.rule_type, body.config, strict=True)
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    try:
        rows = await pool.fetch(
            SQL_UPSERT_VERSIONED,
            body.rule_name.strip(), body.rule_type,
            json.dumps(body.config, ensure_ascii=False), body.is_active,
            config_hash(body.config), parser_key, body.template_version_id,
            body.expected_version, str(current_user_id(authorization)), body.change_summary,
        )
    except Exception as exc:
        message = str(exc).lower()
        if "unique" in message:
            raise HTTPException(status_code=409, detail="规则名已存在") from exc
        if "stale rule version" in message:
            raise HTTPException(status_code=409, detail="规则已被其他操作更新，请刷新后再保存") from exc
        if "template version" in message:
            raise HTTPException(status_code=422, detail="模板版本不存在或类型不匹配") from exc
        raise
    return {
        "rule_id": rows[0]["rule_id"], "version": rows[0]["version"],
        "rule_version_id": rows[0]["rule_version_id"],
        "config_hash": config_hash(body.config),
    }


@router.get("/templates")
async def list_rule_templates() -> dict:
    rows = await _pool().fetch(
        "SELECT t.id,t.template_name,t.rule_type,t.description,t.created_at, "
        "COALESCE(jsonb_agg(jsonb_build_object('version',v.version,'id',v.id,"
        "'config_hash',v.config_hash,'parser_key',v.parser_key,'created_at',v.created_at) "
        "ORDER BY v.version) FILTER (WHERE v.id IS NOT NULL),'[]'::jsonb) AS versions "
        "FROM rule_templates t LEFT JOIN rule_template_versions v ON v.template_id=t.id "
        "GROUP BY t.id ORDER BY t.id"
    )
    return {"items": [{**dict(row), "versions": _decode_jsonb(row["versions"], [])} for row in rows]}


@router.post("/templates")
async def create_rule_template(body: TemplateBody, authorization: str = Header(default="")) -> dict:
    config = dict(body.config)
    parser_key = body.parser_key or config.get("parser_key") or PARSER_BY_TYPE[body.rule_type]
    config["parser_key"] = parser_key
    errors = validate_rule_config(body.rule_type, config, strict=True)
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    try:
        rows = await _pool().fetch(
            "SELECT * FROM rule_template_create($1,$2,$3,$4::jsonb,$5,$6,$7,$8)",
            body.template_name.strip(), body.rule_type, body.description,
            json.dumps(config, ensure_ascii=False), config_hash(config), parser_key,
            str(current_user_id(authorization)), body.change_summary,
        )
    except Exception as exc:
        if "unique" in str(exc).lower():
            raise HTTPException(status_code=409, detail="模板名已存在") from exc
        raise
    return {"template_id": rows[0]["template_id"], "version": rows[0]["version"],
            "template_version_id": rows[0]["template_version_id"]}


@router.post("/templates/{template_id}/versions")
async def create_rule_template_version(
    template_id: int, body: TemplateVersionBody, authorization: str = Header(default=""),
) -> dict:
    row = await _pool().fetchrow("SELECT rule_type FROM rule_templates WHERE id=$1", template_id)
    if row is None:
        raise HTTPException(status_code=404, detail="template not found")
    config = dict(body.config)
    parser_key = body.parser_key or config.get("parser_key") or PARSER_BY_TYPE[row["rule_type"]]
    config["parser_key"] = parser_key
    errors = validate_rule_config(row["rule_type"], config, strict=True)
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    rows = await _pool().fetch(
        "SELECT * FROM rule_template_version_create($1,$2::jsonb,$3,$4,$5,$6)",
        template_id, json.dumps(config, ensure_ascii=False), config_hash(config), parser_key,
        str(current_user_id(authorization)), body.change_summary,
    )
    return {"template_id": rows[0]["template_id"], "version": rows[0]["version"],
            "template_version_id": rows[0]["template_version_id"]}


@router.post("/templates/instances")
async def instantiate_rule_template(
    body: TemplateInstanceBody, authorization: str = Header(default=""),
) -> dict:
    try:
        rows = await _pool().fetch(
            "SELECT * FROM rule_instance_from_template($1,$2,$3,$4)",
            body.template_version_id, body.rule_name.strip(),
            str(current_user_id(authorization)), body.change_summary,
        )
    except Exception as exc:
        message = str(exc).lower()
        if "unique" in message:
            raise HTTPException(status_code=409, detail="规则名已存在") from exc
        if "template version not found" in message:
            raise HTTPException(status_code=404, detail="template version not found") from exc
        raise
    return {"rule_id": rows[0]["rule_id"], "version": rows[0]["version"],
            "rule_version_id": rows[0]["rule_version_id"]}


@router.get("/{rule_id}/versions")
async def list_rule_versions(rule_id: int) -> dict:
    rows = await _pool().fetch(
        "SELECT id,version,rule_type,config_json,config_hash,hash_algorithm,schema_version,"
        "parser_key,template_version_id,snapshot_origin,history_complete,created_by,"
        "change_summary,created_at FROM rule_instance_versions WHERE rule_id=$1 ORDER BY version DESC",
        rule_id,
    )
    return {"items": [{**dict(row), "config_json": _decode_jsonb(row["config_json"], {})}
                       for row in rows]}


@router.post("/{rule_id}/rollback")
async def rollback_rule(
    rule_id: int, body: RollbackBody, authorization: str = Header(default=""),
) -> dict:
    try:
        rows = await _pool().fetch(
            "SELECT * FROM rule_instance_rollback($1,$2,$3,$4)",
            rule_id, body.target_version, str(current_user_id(authorization)), body.change_summary,
        )
    except Exception as exc:
        if "rule version not found" in str(exc).lower():
            raise HTTPException(status_code=404, detail="rule version not found") from exc
        raise
    return {"rule_id": rows[0]["rule_id"], "version": rows[0]["version"],
            "rule_version_id": rows[0]["rule_version_id"]}


@router.get("/router-management")
async def router_management() -> dict:
    rows = await _pool().fetch(
        "SELECT s.id AS source_id,s.peer_id,s.title,s.username,s.router_mode,"
        "COALESCE(jsonb_agg(jsonb_build_object('rule_id',r.id,'rule_name',r.rule_name,"
        "'rule_type',r.rule_type,'priority',b.priority,'rule_version_id',v.id,"
        "'version',COALESCE(v.version,r.version),'config',COALESCE(v.config_json,r.config_json),"
        "'template_version_id',tv.id,'template_id',t.id,"
        "'template_name',t.template_name,'independent_instance',v.template_version_id IS NULL) "
        "ORDER BY b.priority DESC,r.id) FILTER (WHERE r.id IS NOT NULL),'[]'::jsonb) AS bindings,"
        "(SELECT jsonb_build_object('observed_at',c.observed_at,'changed',c.changed,'raw_id',c.raw_id) "
        " FROM rule_shadow_comparisons c WHERE c.source_id=s.id ORDER BY c.observed_at DESC,c.id DESC LIMIT 1) AS last_shadow,"
        "(SELECT jsonb_build_object('previous_mode',e.previous_mode,'new_mode',e.new_mode,"
        "'changed_at',e.changed_at,'actor_user_id',e.actor_user_id) "
        " FROM rule_router_mode_events e WHERE e.source_id=s.id ORDER BY e.changed_at DESC,e.id DESC LIMIT 1) AS last_mode_event "
        "FROM ingest_sources s LEFT JOIN source_rule_bindings b ON b.source_id=s.id "
        "LEFT JOIN ingest_rules r ON r.id=b.rule_id "
        "LEFT JOIN rule_instance_versions v ON v.id=b.rule_version_id "
        "LEFT JOIN rule_template_versions tv ON tv.id=v.template_version_id "
        "LEFT JOIN rule_templates t ON t.id=tv.template_id "
        "GROUP BY s.id ORDER BY s.title,s.id"
    )
    error_rows = await _pool().fetch(
        "SELECT source_id,error_class,occurrences,last_occurred_at,last_raw_id "
        "FROM rule_shadow_error_counts WHERE last_occurred_at >= NOW()-INTERVAL '24 hours' "
        "ORDER BY last_occurred_at DESC LIMIT 200"
    )
    errors = {}
    for row in error_rows:
        errors.setdefault(row["source_id"], []).append(dict(row))
    settings = get_settings()
    allowed_ids = sorted({int(value.strip()) for value in settings.active_router_source_ids.split(",")
                          if value.strip().isdigit()})
    active_available = (
        settings.active_router_enabled and not settings.active_router_kill_switch
        and len(allowed_ids) == 1
    )
    guard_rows = await _pool().fetch(
        "SELECT source_id,heartbeat_at,health_status,active_started_at,last_metrics_json,last_error,"
        "last_rollback_reason FROM active_router_guard_state"
    )
    guards = {int(row["source_id"]): dict(row) for row in guard_rows}
    metric_rows = await _pool().fetch(
        "SELECT s.id AS source_id,"
        "COUNT(r.id) FILTER (WHERE r.posted_at >= NOW()-INTERVAL '30 days') AS messages_30d,"
        "COUNT(r.id) FILTER (WHERE r.extract_state IN ('done','dropped') AND r.posted_at >= NOW()-INTERVAL '30 days') AS processed_30d,"
        "(SELECT COUNT(*) FROM rule_parse_runs p JOIN raw_message_revisions rv ON rv.id=p.raw_revision_id "
        " JOIN raw_messages rm ON rm.id=rv.raw_id WHERE rm.source_id=s.id AND p.run_reason='active' "
        " AND p.created_at >= NOW()-INTERVAL '30 days') AS active_runs_30d,"
        "(SELECT COUNT(*) FROM rule_parse_runs p JOIN raw_message_revisions rv ON rv.id=p.raw_revision_id "
        " JOIN raw_messages rm ON rm.id=rv.raw_id WHERE rm.source_id=s.id AND p.run_reason='active' "
        " AND p.created_at >= NOW()-INTERVAL '30 days' AND p.result_json->>'reason'='active_match') AS active_hits_30d,"
        "(SELECT COUNT(*) FROM rule_shadow_comparisons c WHERE c.source_id=s.id "
        " AND c.observed_at >= NOW()-INTERVAL '30 days') AS shadow_comparisons_30d,"
        "(SELECT COUNT(*) FROM rule_shadow_comparisons c WHERE c.source_id=s.id AND c.changed IS TRUE "
        " AND c.observed_at >= NOW()-INTERVAL '30 days') AS shadow_differences_30d,"
        "(SELECT COUNT(*) FROM rule_active_review_queue q WHERE q.source_id=s.id AND q.status='open') AS open_active_reviews "
        "FROM ingest_sources s LEFT JOIN raw_messages r ON r.source_id=s.id GROUP BY s.id"
    )
    metrics = {int(row["source_id"]): dict(row) for row in metric_rows}
    items = []
    for row in rows:
        source_id = int(row["source_id"])
        bindings = _decode_jsonb(row["bindings"], [])
        guard = guards.get(source_id)
        guard_healthy = bool(
            guard and guard.get("health_status") == "healthy" and guard.get("heartbeat_at")
            and (datetime.now(timezone.utc) - guard["heartbeat_at"]).total_seconds() <= 90
        )
        block_reason = None
        if not active_available:
            block_reason = "Active 全局门禁未开启或急停已启用"
        elif source_id not in allowed_ids:
            block_reason = "来源未加入 Active 允许名单"
        elif source_id != 2:
            block_reason = "当前 OCR Review Assist 已验证支持来源 2；此来源的整合链路未验证"
        elif not any(item.get("rule_type") == "listing" for item in bindings):
            block_reason = "当前绑定没有 listing 规则，Active 无法处理该业务类型"
        items.append({
            **dict(row), "bindings": bindings,
            "last_shadow": _decode_jsonb(row["last_shadow"], None),
            "last_mode_event": _decode_jsonb(row["last_mode_event"], None),
            "recent_errors": errors.get(source_id, []), "metrics": metrics.get(source_id, {}),
            "guard": guard, "guard_healthy": guard_healthy,
            "active_compatible": block_reason is None, "active_block_reason": block_reason,
        })
    return {"items": [{**dict(row),
                       **items[index]} for index, row in enumerate(rows)],
            "active_mode_available": active_available,
            "active_source_ids": allowed_ids if active_available else []}


@router.get("/active-review-queue")
async def active_review_queue(
    limit: int = 100, status: Literal["open", "resolved", "dismissed"] = "open",
    source_id: int | None = None, rule_id: int | None = None,
    reason: str | None = None, since: datetime | None = None,
    until: datetime | None = None,
    category: Literal["review", "unmatched", "all"] = "review",
) -> dict:
    if not 1 <= limit <= 500:
        raise HTTPException(status_code=422, detail="limit must be 1-500")
    if since and until and since >= until:
        raise HTTPException(status_code=422, detail="since must be earlier than until")
    rows = await _pool().fetch(
        SQL_ACTIVE_REVIEW_CLASSIFIED_CTE +
        "SELECT *,count(*) OVER() AS total_count FROM classified WHERE status=$1 "
        "AND ($2::bigint IS NULL OR source_id=$2) "
        "AND ($3::bigint IS NULL OR $3=ANY(candidate_rule_ids)) "
        "AND ($4::text IS NULL OR reason=$4) AND ($5::timestamptz IS NULL OR created_at >= $5) "
        "AND ($6::timestamptz IS NULL OR created_at < $6) "
        "AND ($7::text='all' OR category=$7) "
        "ORDER BY created_at DESC,id DESC LIMIT $8",
        status, source_id, rule_id, reason, since, until, category, limit,
    )
    counts = await _pool().fetchrow(
        SQL_ACTIVE_REVIEW_CLASSIFIED_CTE +
        "SELECT count(*) FILTER (WHERE category='review') AS review,"
        " count(*) FILTER (WHERE category='unmatched') AS unmatched "
        "FROM classified WHERE status=$1 AND ($2::bigint IS NULL OR source_id=$2) "
        "AND ($3::bigint IS NULL OR $3=ANY(candidate_rule_ids)) "
        "AND ($4::text IS NULL OR reason=$4) AND ($5::timestamptz IS NULL OR created_at >= $5) "
        "AND ($6::timestamptz IS NULL OR created_at < $6)",
        status, source_id, rule_id, reason, since, until,
    )
    return {"items": [{**dict(row), "candidate_rule_ids": list(row["candidate_rule_ids"] or []),
                        "diagnostics_json": _decode_jsonb(row["diagnostics_json"], {}),
                        "candidate_json": _decode_jsonb(row["candidate_json"], {}),
                        "media": _decode_jsonb(row["media"], [])}
                      for row in rows], "total_count": rows[0]["total_count"] if rows else 0,
            "counts": {"review": counts["review"], "unmatched": counts["unmatched"]}}


@router.get("/active-review-queue/{queue_id}")
async def active_review_detail(queue_id: int) -> dict:
    row = await _pool().fetchrow(
        "SELECT q.*,s.title AS source_title,r.platform_msg_id,r.posted_at,r.raw_text,r.media_json "
        "FROM rule_active_review_queue q JOIN ingest_sources s ON s.id=q.source_id "
        "JOIN raw_messages r ON r.id=q.raw_id WHERE q.id=$1", queue_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="review candidate not found")
    events = await _pool().fetch(
        "SELECT e.id,e.actor_user_id,e.action,e.listing_id,e.reason,e.created_at "
        "FROM rule_active_review_events e WHERE e.queue_id=$1 ORDER BY e.created_at,e.id", queue_id,
    )
    diagnostics = _decode_jsonb(row["diagnostics_json"], {})
    trace = diagnostics.get("trace") or []
    version_ids = sorted({int(item["rule_version_id"]) for item in trace
                          if str(item.get("rule_version_id", "")).isdigit()})
    rule_versions = await _pool().fetch(
        "SELECT v.id AS rule_version_id,v.rule_id,v.version,r.rule_name,v.rule_type "
        "FROM rule_instance_versions v JOIN ingest_rules r ON r.id=v.rule_id "
        "WHERE v.id=ANY($1::bigint[]) ORDER BY v.rule_id,v.version", version_ids,
    ) if version_ids else []
    ocr_rows = await _pool().fetch(
        "SELECT o.media_path,o.status,o.classification,o.review_status,o.raw_text,o.normalized_text,"
        "o.fields_json,o.evidence_json,o.confidence,o.engine_version,o.created_at,"
        "to_jsonb(o)->>'predicted_classification' AS predicted_classification,"
        "to_jsonb(o)->>'predicted_confidence' AS predicted_confidence,"
        "to_jsonb(o)->>'human_classification' AS human_classification,"
        "to_jsonb(o)->>'classifier_version_id' AS classifier_version_id,"
        "to_jsonb(o)->'classifier_signals_json' AS classifier_signals_json "
        "FROM listing_media_ocr o WHERE o.raw_id=$1 ORDER BY o.id", row["raw_id"],
    )
    media = _decode_jsonb(row["media_json"], [])
    ocr_by_path = {}
    for item in ocr_rows:
        record = dict(item)
        record["fields_json"] = _decode_jsonb(record["fields_json"], {})
        record["evidence_json"] = _decode_jsonb(record["evidence_json"], {})
        record["classifier_signals_json"] = _decode_jsonb(record["classifier_signals_json"], {})
        ocr_by_path[item["media_path"]] = record
    for item in media:
        if item.get("path") in ocr_by_path:
            item["ocr_result"] = ocr_by_path[item["path"]]
    return {**dict(row),
            "candidate_rule_ids": list(row["candidate_rule_ids"] or []),
            "diagnostics_json": diagnostics,
            "candidate_json": _decode_jsonb(row["candidate_json"], {}),
            "media": media,
            "rule_versions": [dict(item) for item in rule_versions],
            "events": [dict(item) for item in events]}


@router.post("/active-review-queue/{queue_id}/requeue")
async def requeue_unmatched_active_review(
    queue_id: int, authorization: str = Header(default=""),
) -> dict:
    actor_id = current_user_id(authorization)
    pool = _pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM rule_active_review_queue WHERE id=$1 FOR UPDATE", queue_id,
            )
            if row is None:
                raise HTTPException(status_code=404, detail="review candidate not found")
            if row["status"] != "open" or row["reason"] != "no_rule_match":
                raise HTTPException(status_code=409, detail="only an open no-match record can be sent to review")
            await conn.execute(
                "UPDATE rule_active_review_queue SET reason='manual_review_requested',"
                "diagnostics_json=diagnostics_json || jsonb_build_object('manual_review_requested',true),"
                "updated_at=NOW() WHERE id=$1", queue_id,
            )
            await conn.execute(
                "INSERT INTO rule_active_review_events(queue_id,actor_user_id,action,reason,decision_json) "
                "VALUES($1,$2,'manual_requeue','管理员从未命中视图送入人工审核',"
                "jsonb_build_object('original_reason','no_rule_match'))", queue_id, actor_id,
            )
    return {"queue_id": queue_id, "status": "open", "reason": "manual_review_requested",
            "actor_user_id": actor_id}


@router.post("/active-review-queue/{queue_id}/resolve")
async def resolve_active_review(
    queue_id: int, body: ActiveReviewResolutionBody,
    authorization: str = Header(default=""),
) -> dict:
    pool = _pool()
    actor_id = current_user_id(authorization)
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT q.*,r.media_json,r.posted_at FROM rule_active_review_queue q "
                "JOIN raw_messages r ON r.id=q.raw_id WHERE q.id=$1 FOR UPDATE OF r,q", queue_id,
            )
            if row is None:
                raise HTTPException(status_code=404, detail="review candidate not found")
            if row["status"] != "open":
                raise HTTPException(status_code=409, detail="review candidate was already handled")

            listing_id = None
            if body.action == "create_pending_listing":
                candidate = _decode_jsonb(row["candidate_json"], {})
                fields = body.confirmed_fields or candidate.get("fields") or {}
                username_value = fields.get("username")
                if username_value is None and body.confirmed_fields is None:
                    username_value = candidate.get("identity")
                username = str(username_value or "").strip().lstrip("@")
                if not username:
                    raise HTTPException(status_code=409, detail="candidate has no confirmed Telegram identity")
                existing = await conn.fetchval(
                    "SELECT id FROM listings WHERE source_msg_id=$1 LIMIT 1", row["raw_id"],
                )
                if existing is not None:
                    raise HTTPException(status_code=409, detail="this source message already has a Listing")
                duplicate = await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM listings WHERE lower(contact_username)=lower($1) "
                    "AND status IN ('published','pending','duplicate_pending','archived'))", username,
                )
                price = fields.get("price_amount")
                if price not in (None, ""):
                    try:
                        price = str(float(price))
                    except (TypeError, ValueError):
                        raise HTTPException(status_code=422, detail="price_amount must be numeric")
                else:
                    price = None
                listing_id = await conn.fetchval(
                    "INSERT INTO listings(source_msg_id,contact_username,nickname,district,price_amount,"
                    "price_currency,description,media_json,manual_fields,evidence_json,custom_fields,"
                    "confidence,status,published_at) VALUES($1,$2,$3,$4,$5::numeric,$6,$7,$8::jsonb,"
                    "'{}'::jsonb,$9::jsonb,$10::jsonb,0.5,$11,NULL) RETURNING id",
                    row["raw_id"], username,
                    str(fields.get("nickname") or "") or None,
                    str(fields.get("district") or "") or None,
                    price, str(fields.get("price_currency") or "CNY"),
                    str(fields.get("description") or ""),
                    json.dumps(_decode_jsonb(row["media_json"], []), ensure_ascii=False),
                    json.dumps(candidate.get("evidence") or {}, ensure_ascii=False),
                    json.dumps(fields.get("custom_fields") or {}, ensure_ascii=False),
                    "duplicate_pending" if duplicate else "pending",
                )

            new_status = "dismissed" if body.action == "dismiss" else "resolved"
            decision = {"confirmed_fields": body.confirmed_fields} if body.confirmed_fields is not None else {}
            await conn.execute(
                    "INSERT INTO rule_active_review_events(queue_id,actor_user_id,action,listing_id,reason,decision_json) "
                    "VALUES($1,$2,$3,$4,$5,$6::jsonb)", queue_id, actor_id, body.action, listing_id,
                    body.reason, json.dumps(decision, ensure_ascii=False),
            )
            await conn.execute(
                "UPDATE rule_active_review_queue SET status=$2,listing_id=$3,reviewed_by=$4,"
                "reviewed_at=NOW(),last_action=$5,updated_at=NOW() WHERE id=$1",
                queue_id, new_status, listing_id, actor_id, body.action,
            )
    return {"queue_id": queue_id, "status": new_status, "action": body.action,
            "listing_id": listing_id, "actor_user_id": actor_id}


@router.get("/sources/{source_id}/raw-samples")
async def router_raw_samples(source_id: int, limit: int = 50) -> dict:
    if not 1 <= limit <= 100:
        raise HTTPException(status_code=422, detail="limit must be 1-100")
    rows = await _pool().fetch(
        "SELECT id AS raw_id,platform_msg_id,posted_at,LEFT(COALESCE(raw_text,''),500) AS text_preview,"
        "jsonb_array_length(COALESCE(media_json,'[]'::jsonb)) AS media_count "
        "FROM raw_messages WHERE source_id=$1 ORDER BY posted_at DESC NULLS LAST,id DESC LIMIT $2",
        source_id, limit,
    )
    return {"source_id": source_id, "items": [dict(row) for row in rows]}


@router.put("/sources/{source_id}/router-mode")
async def set_source_router_mode(source_id: int, body: RouterModeBody,
                                 authorization: str = Header(default="")) -> dict:
    pool = _pool()
    actor_id = current_user_id(authorization)
    settings = get_settings()
    allowed_ids = {int(value.strip()) for value in settings.active_router_source_ids.split(",")
                   if value.strip().isdigit()}
    if body.mode == "active" and (
        not settings.active_router_enabled or settings.active_router_kill_switch
        or allowed_ids != {source_id}
        or not active_source_allowed(
            source_id, enabled=settings.active_router_enabled,
            allowed_source_ids=settings.active_router_source_ids,
            kill_switch=settings.active_router_kill_switch,
        )
    ):
        raise HTTPException(status_code=409, detail="Active 未获准：需开启全局开关、关闭急停并仅白名单此来源")
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.fetchval("SELECT pg_advisory_xact_lock($1)", 814271002)
            if body.mode == "active":
                supported_rules = await conn.fetch(
                    "SELECT DISTINCT r.rule_type FROM source_rule_bindings b "
                    "JOIN ingest_rules r ON r.id=b.rule_id WHERE b.source_id=$1 AND r.is_active",
                    source_id,
                )
                if source_id != 2 or not any(row["rule_type"] == "listing" for row in supported_rules):
                    raise HTTPException(status_code=409, detail="该来源尚未通过 Active OCR 兼容检查")
                guard = await conn.fetchrow(
                    "SELECT heartbeat_at,health_status FROM active_router_guard_state "
                    "WHERE source_id=$1 FOR UPDATE", source_id,
                )
                if (not guard or guard["health_status"] != "healthy" or not guard["heartbeat_at"]
                        or (datetime.now(timezone.utc) - guard["heartbeat_at"]).total_seconds() > 90):
                    raise HTTPException(status_code=409, detail="Active Guard 未在线，拒绝切换")
            row = await conn.fetchrow(
                "SELECT router_mode FROM ingest_sources WHERE id=$1 FOR UPDATE", source_id,
            )
            if row is None:
                raise HTTPException(status_code=404, detail="source not found")
            previous = row["router_mode"]
            if previous != body.mode:
                if body.mode == "active":
                    active_rows = await conn.fetch(
                        "SELECT id FROM ingest_sources WHERE router_mode='active' AND id<>$1 FOR UPDATE",
                        source_id,
                    )
                    if active_rows:
                        raise HTTPException(status_code=409, detail="已有其他来源处于 Active；当前 Guard 仅允许来源 2")
                    baseline = await conn.fetchrow(
                        "SELECT (SELECT md5(COALESCE(string_agg(concat_ws(':',rule_id,rule_type,purpose,match_group,mode,"
                        "priority,rule_version_id,config_hash),'|' ORDER BY purpose,priority DESC,rule_id),'')) "
                        "FROM rule_shadow_bindings_for_source($1)) AS binding_signature,"
                        "(SELECT md5(COALESCE(string_agg(concat_ws(':',l.id,l.status,"
                        "md5(COALESCE(l.manual_fields,'{}'::jsonb)::text)),'|' ORDER BY l.id),'')) "
                        "FROM listings l JOIN raw_messages r ON r.id=l.source_msg_id "
                        "WHERE r.source_id=$1 AND l.status='published') AS source2_published_signature,"
                        "(SELECT count(*) FROM listings l JOIN raw_messages r ON r.id=l.source_msg_id "
                        "WHERE r.source_id=$1 AND l.status='published') AS source2_published_count",
                        source_id,
                    )
                    await conn.execute(
                        "INSERT INTO active_router_guard_state(source_id,active_started_at,active_baseline_json,"
                        "last_rollback_reason,updated_at) VALUES($1,NOW(),$2::jsonb,NULL,NOW()) "
                        "ON CONFLICT(source_id) DO UPDATE SET active_started_at=NOW(),"
                        "active_baseline_json=EXCLUDED.active_baseline_json,last_rollback_reason=NULL,updated_at=NOW()",
                        source_id, json.dumps(dict(baseline)),
                    )
                await conn.execute("UPDATE ingest_sources SET router_mode=$2 WHERE id=$1", source_id, body.mode)
                await conn.execute(
                    "INSERT INTO rule_router_mode_events(source_id,previous_mode,new_mode,actor_user_id) "
                    "VALUES($1,$2,$3,$4)", source_id, previous, body.mode, actor_id,
                )
                if body.mode == "legacy":
                    await conn.execute(
                        "UPDATE active_router_guard_state SET active_started_at=NULL,"
                        "active_baseline_json='{}'::jsonb,updated_at=NOW() WHERE source_id=$1", source_id,
                    )
    return {"source_id": source_id, "previous_mode": previous,
            "mode": body.mode, "actor_user_id": actor_id}


class CollectionPreviewBody(BaseModel):
    source_id: int = Field(gt=0)
    username: str = Field(min_length=4, max_length=33)
    message_id: int | None = Field(default=None, gt=0)


@router.post("/collection-preview")
async def collection_preview(body: CollectionPreviewBody) -> dict:
    username = body.username.strip().lstrip("@").lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]{3,31}", username):
        raise HTTPException(status_code=422, detail="请输入有效的 Telegram 用户名")
    pool = _pool()
    value = await pool.fetchval("SELECT nexus_collection_plan($1,$2,NULL,$3)",
                               body.source_id, body.message_id, username)
    plan = _decode_jsonb(value, {})
    original = plan.pop("local_post", {})
    existing = plan.pop("listing_fields", {})
    changes = []
    version = None
    if original and plan.get("decision") in {"supplement", "update"}:
        rule = await pool.fetchrow(
            "SELECT b.rule_version_id,COALESCE(v.config_json,r.config_json) AS config "
            "FROM source_rule_bindings b JOIN ingest_rules r ON r.id=b.rule_id "
            "LEFT JOIN rule_instance_versions v ON v.id=b.rule_version_id "
            "WHERE b.source_id=$1 AND r.rule_type='listing' AND r.is_active "
            "ORDER BY b.priority DESC,r.id LIMIT 1", body.source_id,
        )
        if rule:
            version = rule["rule_version_id"]
            fields, _ = ast_rules.extract_fields(_decode_jsonb(rule["config"], {}), original.get("raw_text", ""))
            for field in ("nickname", "district", "price_amount", "price_currency", "contact_username"):
                parsed_key = "username" if field == "contact_username" else field
                before, after = existing.get(field), fields.get(parsed_key)
                if after not in (None, "") and not equivalent_field(parsed_key, before, after):
                    changes.append({"field": field, "current": before, "proposed": after})
    return {**plan, "original": {key: original[key] for key in ("id", "platform_msg_id", "revision_id")
                                  if key in original},
            "rule_version_id": version, "field_differences": changes}


@router.post("/router-dryrun")
async def router_dry_run(body: RouterDryRunBody) -> dict:
    pool = _pool()
    if body.sample_limit < 1 or body.sample_limit > 200:
        raise HTTPException(status_code=422, detail="sample_limit must be 1-200")
    if len(body.raw_message_ids) > 200:
        raise HTTPException(status_code=422, detail="最多指定 200 条消息")
    bindings = [dict(row) for row in await pool.fetch(SQL_SHADOW_BINDINGS, body.source_id)]
    bindings = [b for b in bindings if b.get("purpose") == body.purpose]
    if not bindings:
        raise HTTPException(status_code=404, detail="source has no active rules for this purpose")
    if body.rule_version_id is not None:
        version = await pool.fetchrow(
            "SELECT id,rule_id,config_json,config_hash FROM rule_instance_versions WHERE id=$1",
            body.rule_version_id,
        )
        target = next((b for b in bindings if b["rule_id"] == version["rule_id"]), None) if version else None
        if target is None:
            raise HTTPException(status_code=422, detail="指定版本不属于该来源当前绑定的规则")
        target["rule_version_id"] = version["id"]
        target["config_json"] = version["config_json"]
        target["config_hash"] = version["config_hash"]
    if body.raw_message_ids:
        samples = await pool.fetch(
            "SELECT id AS raw_id,platform_msg_id,sender_peer_id,raw_text,media_json FROM raw_messages "
            "WHERE source_id=$1 AND id=ANY($2::bigint[]) ORDER BY id LIMIT $3",
            body.source_id, body.raw_message_ids, body.sample_limit,
        )
    else:
        samples = await pool.fetch(
            "SELECT id AS raw_id,platform_msg_id,sender_peer_id,raw_text,media_json FROM raw_messages "
            "WHERE source_id=$1 ORDER BY posted_at DESC NULLS LAST,id DESC LIMIT $2",
            body.source_id, body.sample_limit,
        )
    results = []
    for sample in samples:
        comparison = shadow_compare(
            bindings, sample["raw_text"] or "", sample["sender_peer_id"],
        )
        selected = next((item.get("selected") for item in comparison["shadow_predictions"]
                         if item.get("purpose") == body.purpose and item.get("selected")), None)
        identity = (selected or {}).get("identity", "")
        fields = (selected or {}).get("fields", {})
        legacy = comparison.get("actual_legacy") or {}
        legacy_fields = legacy.get("fields", {})
        results.append({
            "raw_id": sample["raw_id"], "platform_msg_id": sample["platform_msg_id"],
            "media_count": len(_decode_jsonb(sample["media_json"], [])),
            "description_present": bool((sample["raw_text"] or "").strip()),
            "legacy": legacy, "router": comparison["shadow_predictions"],
            "field_diff": {key: {"legacy": legacy_fields.get(key), "router": fields.get(key)}
                           for key in sorted(set(legacy_fields) | set(fields))
                           if legacy_fields.get(key) != fields.get(key)},
            "identity_diff": {"legacy": legacy.get("identity", ""), "router": identity},
            "conflicts": [c for item in comparison["shadow_predictions"] for c in item.get("conflicts", [])],
            "candidate_key": business_candidate_key(
                body.source_id, sample["platform_msg_id"], body.purpose, identity,
            ),
            "ocr_would_be_considered": any(
                trace.get("ocr_would_be_considered")
                for item in comparison["shadow_predictions"] for trace in item.get("trace", [])
            ),
            "publication_blockers": sorted(set((selected or {}).get("missing_fields", [])) | {
                "media/custom-field eligibility requires full processing context",
                "publish authorization and explicit state transition are not evaluated by dry-run",
            }),
            "estimated_action": "review" if not fields or (selected or {}).get("missing_fields") else "needs_publish_gate_check",
            "changed": comparison["changed"],
        })
    return {"source_id": body.source_id, "purpose": body.purpose, "count": len(results),
            "read_only": True, "telegram_requests": 0, "ocr_runs": 0, "results": results}


@router.get("/lifecycle-review")
async def list_lifecycle_review_events(
    status: Literal["pending", "approved", "rejected", "all"] = "pending",
    source_id: int | None = None,
    rule_id: int | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    if not 1 <= limit <= 100 or offset < 0:
        raise HTTPException(status_code=422, detail="limit must be 1-100 and offset >= 0")
    rows = await _pool().fetch(
        "SELECT e.id,e.raw_id,e.rule_version_id,e.sender_peer_id,e.lifecycle_term,"
        "e.candidate_username,e.review_reason,e.status,e.resolved_listing_id,e.review_action,"
        "e.review_reason_text,e.identity_evidence,e.reviewed_by,e.reviewed_at,e.created_at,"
        "r.source_id,r.platform_msg_id,r.raw_text,r.media_json,r.posted_at,"
        "s.title AS source_title,s.username AS source_username,rv.rule_id,ir.rule_name,rv.version AS rule_version,"
        "u.username AS reviewer_username,u.display_name AS reviewer_display_name,"
        "COALESCE((SELECT jsonb_agg(jsonb_build_object('id',l.id,'nickname',l.nickname,"
        "'status',l.status,'contact_username',l.contact_username,'price_amount',l.price_amount) "
        "ORDER BY l.updated_at DESC,l.id DESC) "
        "FROM listings l WHERE e.candidate_username<>'' AND lower(l.contact_username)=e.candidate_username),'[]'::jsonb) AS candidates "
        "FROM lifecycle_review_events e JOIN raw_messages r ON r.id=e.raw_id "
        "JOIN ingest_sources s ON s.id=r.source_id "
        "LEFT JOIN rule_instance_versions rv ON rv.id=e.rule_version_id "
        "LEFT JOIN ingest_rules ir ON ir.id=rv.rule_id "
        "LEFT JOIN site_users u ON u.id::text=e.reviewed_by "
        "WHERE ($1='all' OR e.status=$1) AND ($2::bigint IS NULL OR r.source_id=$2) "
        "AND ($3::bigint IS NULL OR rv.rule_id=$3) "
        "AND ($4::timestamptz IS NULL OR e.created_at >= $4) "
        "AND ($5::timestamptz IS NULL OR e.created_at < $5) "
        "ORDER BY e.created_at DESC,e.id DESC LIMIT $6 OFFSET $7",
        status, source_id, rule_id, created_after, created_before, limit + 1, offset,
    )
    has_more = len(rows) > limit
    return {"items": [{**dict(row),
                       "candidates": _decode_jsonb(row["candidates"], []),
                       "identity_evidence": _decode_jsonb(row["identity_evidence"], {}),
                       "media_json": _decode_jsonb(row["media_json"], [])}
                       for row in rows[:limit]],
            "has_more": has_more, "next_offset": offset + limit if has_more else None}


@router.post("/lifecycle-review/{event_id}/resolve")
async def resolve_lifecycle_review_event(
    event_id: int, body: LifecycleResolutionBody, authorization: str = Header(default=""),
) -> dict:
    if body.action == "approve" and body.listing_id is None:
        raise HTTPException(status_code=422, detail="approve 需要明确选择一张卡片")
    if body.action == "reject" and body.listing_id is not None:
        raise HTTPException(status_code=422, detail="reject 不应包含 listing_id")
    try:
        resolved = await _pool().fetchval(
            "SELECT lifecycle_review_resolve($1,$2,$3,$4,$5)",
            event_id, body.action, body.listing_id,
            str(current_user_id(authorization)), body.reason,
        )
    except Exception as exc:
        message = str(exc).lower()
        if "does not match" in message or "only a published listing" in message:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if "listing not found" in message:
            raise HTTPException(status_code=404, detail="卡片不存在") from exc
        raise
    if not resolved:
        raise HTTPException(status_code=409, detail="事件不存在或已处理")
    return {"event_id": event_id, "action": body.action, "listing_id": body.listing_id}


@router.get("/shadow-diagnostics")
async def shadow_diagnostics() -> dict:
    pool = _pool()
    counts = await pool.fetchrow(
        "SELECT (SELECT count(*) FROM rule_shadow_comparisons) AS comparisons,"
        "(SELECT count(*) FROM rule_parse_runs WHERE run_reason='shadow') AS shadow_runs,"
        "(SELECT count(*) FROM rule_shadow_error_counts WHERE last_occurred_at>=NOW()-INTERVAL '24 hours') AS error_buckets,"
        "(SELECT COALESCE(sum(occurrences),0) FROM rule_shadow_error_counts WHERE last_occurred_at>=NOW()-INTERVAL '24 hours') AS recent_errors,"
        "(SELECT count(*) FROM rule_shadow_comparisons WHERE observed_at<NOW()-INTERVAL '30 days') AS expired_comparisons,"
        "(SELECT count(*) FROM rule_parse_runs WHERE run_reason='shadow' AND created_at<NOW()-INTERVAL '30 days') AS expired_runs,"
        "(SELECT count(*) FROM rule_shadow_error_counts WHERE last_occurred_at<NOW()-INTERVAL '30 days') AS expired_error_buckets,"
        "pg_total_relation_size('rule_shadow_comparisons') AS comparison_bytes,"
        "pg_total_relation_size('rule_parse_runs') AS parse_run_bytes,"
        "pg_total_relation_size('rule_shadow_error_counts') AS error_count_bytes"
    )
    errors = await pool.fetch(
        "SELECT source_id,error_class,occurrences,last_occurred_at,last_raw_id "
        "FROM rule_shadow_error_counts WHERE last_occurred_at>=NOW()-INTERVAL '24 hours' "
        "ORDER BY last_occurred_at DESC LIMIT 100"
    )
    last_run = await pool.fetchrow(
        "SELECT c.id,c.status,c.cutoff_at,c.preview_json,c.deleted_json,c.started_at,c.finished_at,"
        "u.username AS actor_username FROM rule_shadow_cleanup_runs c "
        "LEFT JOIN site_users u ON u.id=c.actor_user_id ORDER BY c.id DESC LIMIT 1"
    )
    settings = get_settings()
    sizes = sum(int(counts[key] or 0) for key in
                ("comparison_bytes", "parse_run_bytes", "error_count_bytes"))
    warnings = []
    if sizes >= settings.shadow_capacity_warn_bytes:
        warnings.append("shadow_storage_capacity")
    if counts["recent_errors"] >= settings.shadow_recent_error_warn_count:
        warnings.append("shadow_error_rate")
    return {"counts": dict(counts), "errors": [dict(row) for row in errors],
            "last_cleanup": dict(last_run) if last_run else None,
            "thresholds": {"storage_bytes": settings.shadow_capacity_warn_bytes,
                           "recent_errors": settings.shadow_recent_error_warn_count},
            "warnings": warnings}


@router.post("/shadow-diagnostics/prune")
async def prune_shadow_diagnostics(body: ShadowPruneBody,
                                   authorization: str = Header(default="")) -> dict:
    pool = _pool()
    actor_id = current_user_id(authorization)
    cutoff = datetime.now(timezone.utc) - timedelta(days=body.older_than_days)
    preview = await pool.fetchrow(
        "SELECT (SELECT count(*) FROM rule_parse_runs WHERE run_reason='shadow' AND created_at<$1) AS shadow_runs,"
        "(SELECT count(*) FROM rule_shadow_comparisons WHERE observed_at<$1) AS comparisons,"
        "(SELECT count(*) FROM rule_shadow_error_counts WHERE last_occurred_at<$1) AS error_buckets",
        cutoff,
    )
    run_id = await pool.fetchval(
        "INSERT INTO rule_shadow_cleanup_runs(actor_user_id,cutoff_at,requested_batch_size,preview_json,status) "
        "VALUES($1,$2,$3,$4::jsonb,'running') RETURNING id",
        actor_id, cutoff, body.batch_size, json.dumps(dict(preview)),
    )
    deleted = {"shadow_runs": 0, "comparisons": 0, "error_buckets": 0}
    try:
        for _ in range(body.max_batches):
            statuses = [
                await pool.execute(
                    "WITH victims AS (SELECT id FROM rule_parse_runs WHERE run_reason='shadow' AND created_at<$1 "
                    "ORDER BY created_at,id LIMIT $2 FOR UPDATE SKIP LOCKED) "
                    "DELETE FROM rule_parse_runs r USING victims v WHERE r.id=v.id",
                    cutoff, body.batch_size,
                ),
                await pool.execute(
                    "WITH victims AS (SELECT id FROM rule_shadow_comparisons WHERE observed_at<$1 "
                    "ORDER BY observed_at,id LIMIT $2 FOR UPDATE SKIP LOCKED) "
                    "DELETE FROM rule_shadow_comparisons c USING victims v WHERE c.id=v.id",
                    cutoff, body.batch_size,
                ),
                await pool.execute(
                    "WITH victims AS (SELECT source_id,error_class,window_started_at "
                    "FROM rule_shadow_error_counts WHERE last_occurred_at<$1 "
                    "ORDER BY last_occurred_at LIMIT $2 FOR UPDATE SKIP LOCKED) "
                    "DELETE FROM rule_shadow_error_counts e USING victims v "
                    "WHERE (e.source_id,e.error_class,e.window_started_at)="
                    "(v.source_id,v.error_class,v.window_started_at)",
                    cutoff, body.batch_size,
                ),
            ]
            batch_counts = [int(value.split()[-1]) for value in statuses]
            deleted["shadow_runs"] += batch_counts[0]
            deleted["comparisons"] += batch_counts[1]
            deleted["error_buckets"] += batch_counts[2]
            if not any(batch_counts):
                break
        await pool.execute(
            "UPDATE rule_shadow_cleanup_runs SET deleted_json=$2::jsonb,status='completed',finished_at=NOW() WHERE id=$1",
            run_id, json.dumps(deleted),
        )
    except Exception as exc:
        await pool.execute(
            "UPDATE rule_shadow_cleanup_runs SET deleted_json=$2::jsonb,status='failed',error_class=$3,finished_at=NOW() WHERE id=$1",
            run_id, json.dumps(deleted), type(exc).__name__,
        )
        raise
    return {"run_id": run_id, "cutoff_at": cutoff.isoformat(), "preview": dict(preview),
            "deleted": deleted, "batch_size": body.batch_size, "max_batches": body.max_batches}


@router.post("/{rule_id}/copy")
async def copy_rule(
    rule_id: int, body: CopyBody, authorization: str = Header(default=""),
) -> dict:
    pool = _pool()
    try:
        rows = await pool.fetch(
            SQL_COPY, rule_id, body.rule_name.strip(), str(current_user_id(authorization)),
        )
    except Exception as exc:
        message = str(exc).lower()
        if "unique" in message:
            raise HTTPException(status_code=409, detail="规则名已存在") from exc
        if "private credentials" in message:
            raise HTTPException(status_code=409, detail="源规则含账号密钥，移除后才能复制") from exc
        raise
    if not rows:
        raise HTTPException(status_code=404, detail="not found or protected")
    return {
        "rule_id": rows[0]["rule_id"], "version": rows[0]["version"],
        "rule_version_id": rows[0]["rule_version_id"],
    }


@router.delete("/{rule_id}")
async def delete_rule(rule_id: int) -> dict:
    pool = _pool()
    rows = await pool.fetch(SQL_DELETE, rule_id)
    if not rows[0]["ok"]:
        raise HTTPException(status_code=404, detail="not found or protected")
    return {"ok": True}


@router.post("/{rule_id}/bind")
async def bind_rule(rule_id: int, body: BindBody) -> dict:
    pool = _pool()
    if not validate_match_mode(body.match_mode):
        raise HTTPException(status_code=422, detail="unsupported match_mode")
    try:
        row = await pool.fetchval(
            SQL_BIND_VERSIONED, rule_id, body.source_id, body.priority,
            body.purpose, body.match_group, body.match_mode, body.fallback_order,
            body.rule_version_id,
        )
    except Exception as exc:
        message = str(exc).lower()
        if "not found" in message or "does not belong" in message:
            raise HTTPException(status_code=422, detail="规则、来源或规则版本无效") from exc
        raise
    return {"ok": bool(row)}


@router.get("/{rule_id}/profile-collection")
async def profile_collection_results(rule_id: int, limit: int = 50) -> dict:
    pool = _pool()
    rule = await pool.fetchrow("SELECT rule_type FROM ingest_rules WHERE id=$1", rule_id)
    if rule is None or rule["rule_type"] not in {"profile_collection", "bot_collection", "checkin_reverse"}:
        raise HTTPException(status_code=404, detail="collection rule not found")
    query = SQL_BOT_COLLECTION_RESULTS if rule["rule_type"] == "bot_collection" else SQL_PROFILE_COLLECTION_RESULTS
    rows = await pool.fetch(query, rule_id, max(1, min(limit, 100)))
    summary_row = await pool.fetchrow(SQL_PROFILE_COLLECTION_SUMMARY, rule_id)
    summary = {
        key: int(summary_row[key])
        for key in (
            "total", "pending", "processing", "deferred", "fetched",
            "skipped", "not_found", "failed", "completed",
        )
    }
    summary["cooldown_until"] = (
        summary_row["cooldown_until"].isoformat()
        if summary_row["cooldown_until"] else None
    )
    return {"summary": summary, "items": [
        {
            "job_id": row["job_id"], "duty_date": str(row["duty_date"]),
            "contact_username": row["contact_username"], "status": row["status"],
            "error": row["error"], "next_attempt_at": row["next_attempt_at"].isoformat()
                if row["next_attempt_at"] else None,
            "retry_count": row["retry_count"], "queued_at": row["queued_at"].isoformat(),
            "result_id": row["result_id"], "contact_peer_id": row["contact_peer_id"],
            "profile_url": row["profile_url"], "channel_url": row["channel_url"],
            "bot_url": row["bot_url"], "description": row["description"],
            "media": _decode_jsonb(row["media_json"], []),
            "evidence": _decode_jsonb(row["evidence_json"], {}),
            "collected_at": row["collected_at"].isoformat() if row["collected_at"] else None,
            "ocr_pending": row["ocr_pending"], "ocr_review": row["ocr_review"],
        } for row in rows
    ]}


@router.get("/{rule_id}/history-scan")
async def checkin_history_status(rule_id: int) -> dict:
    row = await _pool().fetchrow(SQL_CHECKIN_HISTORY_STATUS, rule_id)
    if row is None:
        return {"scan": None}
    return {"scan": {
        "source_id": row["source_id"], "source_username": row["source_username"],
        "status": row["status"], "cursor_msg_id": row["cursor_msg_id"],
        "scanned_count": row["scanned_count"], "stored_count": row["stored_count"],
        "candidate_count": row["candidate_count"],
        "last_scanned_msg_id": row["last_scanned_msg_id"],
        "last_scanned_at": row["last_scanned_at"].isoformat() if row["last_scanned_at"] else None,
        "next_attempt_at": row["next_attempt_at"].isoformat() if row["next_attempt_at"] else None,
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
        "error": row["error"], "progress_note": row["progress_note"],
        "published_count": row["published_count"], "review_count": row["review_count"],
    }}


@router.post("/{rule_id}/profile-collection/enqueue")
async def enqueue_profile_collection(rule_id: int, body: ProfileCollectionEnqueueBody) -> dict:
    raw_values = list(body.telegram_usernames or [])
    if body.telegram_username.strip():
        raw_values.append(body.telegram_username)
    usernames = []
    for raw_value in raw_values:
        for raw in re.split(r"[,\s;，；]+", str(raw_value).strip()):
            if not raw:
                continue
            match = re.fullmatch(
                r"(?:https?://)?(?:www\.)?t\.me/([A-Za-z0-9_]{5,32})/?|@?([A-Za-z0-9_]{5,32})",
                raw, re.IGNORECASE,
            )
            if not match:
                raise HTTPException(status_code=422, detail=f"TG 号格式无效：{raw}")
            username = (match.group(1) or match.group(2)).lower()
            if username not in usernames:
                usernames.append(username)
    if not usernames:
        raise HTTPException(status_code=422, detail="请输入至少一个 Telegram 用户名")
    if len(usernames) > 100:
        raise HTTPException(status_code=422, detail="单次最多提交 100 个 TG 号")
    jobs = []
    skipped = []
    for username in usernames:
        rows = await _pool().fetch(SQL_PROFILE_COLLECTION_ENQUEUE, rule_id, username, body.source_id)
        if rows and rows[0]["skip_reason"]:
            skipped.append({"username": username, "reason": rows[0]["skip_reason"]})
        elif rows:
            jobs.append({"job_id": rows[0]["job_id"], "username": username, "status": rows[0]["status"]})
    if not jobs and not skipped:
        raise HTTPException(status_code=409, detail="该规则没有已启用并绑定的采集来源/账号")
    return {"jobs": jobs, "queued": len(jobs), "skipped": len(usernames) - len(jobs), "skip_reasons": skipped}


@router.post("/{rule_id}/bot-collection/preview")
async def preview_bot_collection(rule_id: int, body: CollectionPreviewBody) -> dict:
    pool = _pool()
    eligible_binding = await pool.fetchval(
        "SELECT EXISTS(SELECT 1 FROM source_rule_bindings b JOIN ingest_rules r ON r.id=b.rule_id "
        "JOIN ingest_sources s ON s.id=b.source_id JOIN bridge_accounts a ON a.id=s.assigned_account_id "
        "WHERE b.rule_id=$1 AND b.source_id=$2 AND r.rule_type='bot_collection' "
        "AND r.is_active AND s.is_active AND a.status='active')", rule_id, body.source_id,
    )
    if not eligible_binding:
        raise HTTPException(status_code=409, detail="该 Bot 规则没有已启用的来源和采集账号")
    username = body.username.strip().lstrip("@").lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]{3,31}", username):
        raise HTTPException(status_code=422, detail="请输入有效的 Telegram 用户名")
    reason = await pool.fetchval("SELECT nexus_bot_collection_precheck($1,$2,$3)", rule_id, body.source_id, username)
    return {"eligible": reason is None, "reason": reason}


@router.post("/{rule_id}/profile-collection/requeue-ineffective")
async def requeue_ineffective_profile_collection(rule_id: int) -> dict:
    rows = await _pool().fetch(SQL_PROFILE_COLLECTION_REQUEUE, rule_id)
    return {"requeued": rows[0]["requeued"] if rows else 0}


@router.post("/{rule_id}/unbind")
async def unbind_rule(rule_id: int, body: BindBody) -> dict:
    pool = _pool()
    await pool.fetch(SQL_UNBIND, rule_id, body.source_id)
    return {"ok": True}


@router.post("/reextract")
async def reextract_source(body: ReextractBody) -> dict:
    """Requeue every processed message of a source so the (just
    improved) bound rule re-runs against existing data; cards refresh
    in place when the new pass scores higher."""
    pool = _pool()
    rows = await pool.fetch(SQL_REQUEUE, body.source_id)
    return {"requeued": rows[0]["requeued"]}


@router.post("/dryrun")
async def dry_run(body: DryRunBody) -> dict:
    pool = _pool()
    rows = await pool.fetch(SQL_SAMPLES, body.sample_limit)
    results = []
    hits = 0
    for r in rows:
        text = r["raw_text"] or ""
        ok, reason = ast_rules.gate_pass(body.config, text, None)
        term = ast_rules.hit_lifecycle(body.config, text) if ok else None
        fields, _ = ast_rules.extract_fields(body.config, text) if ok else ({}, {})
        matched = bool(fields) or term is not None
        hits += 1 if matched else 0
        results.append({
            "raw_id": r["raw_id"],
            "text": text[:120],
            "gate": "pass" if ok else f"blocked ({reason})",
            "lifecycle": term,
            "fields": {k: v for k, v in fields.items()},
            "matched": matched,
        })
    return {
        "total": len(results),
        "hits": hits,
        "hit_rate": round(hits / len(results), 3) if results else 0.0,
        "results": results,
    }


@router.get("/llm-status")
async def llm_status() -> dict:
    cfg = await llm.load_config(_pool())
    return {"available": bool(cfg["base_url"] and cfg["api_key"]),
            "model": cfg["model"] or None}


@router.get("/samples")
async def recent_samples(limit: int = 20, source_id: int = None) -> dict:
    """Recent raw messages for the sample drawer; optionally restricted
    to one bound source (group/channel)."""
    pool = _pool()
    limit = max(1, min(limit, 200))
    if source_id:
        rows = await pool.fetch(
            sqlres.load("samples_for_source.sql"), source_id, limit,
        )
    else:
        rows = await pool.fetch(sqlres.load("dryrun_samples.sql"), limit)
    return {
        "samples": [
            {"text": r["raw_text"], "source": r["source_title"] or "未知来源"}
            for r in rows
            if r["raw_text"]
        ]
    }


@router.post("/generate")
async def generate(body: GenerateBody) -> dict:
    pool = _pool()
    cfg = await llm.load_config(pool)
    if not (cfg["base_url"] and cfg["api_key"]):
        raise HTTPException(status_code=503, detail="LLM 未配置（规则工坊右上『AI 设置』）")
    cleaned = [x.strip() for x in body.samples if x and x.strip()][:20]
    if not cleaned:
        raise HTTPException(status_code=400, detail="no usable samples")
    content = llm.chat(cfg, [
        {"role": "system", "content": LLM_SYSTEM_PROMPT},
        {"role": "user", "content": "\n---\n".join(cleaned)},
    ])
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
    try:
        config = json.loads(content)
    except json.JSONDecodeError:
        raise HTTPException(status_code=502, detail="llm returned non-JSON payload")
    if not isinstance(config, dict) or "extract" not in config:
        raise HTTPException(status_code=502, detail="llm payload missing 'extract' section")
    # sanity: every declared regex must compile before we hand it back
    for group in (config.get("extract") or {}).values():
        if isinstance(group, list):
            for pattern in group:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise HTTPException(
                        status_code=502,
                        detail=f"llm produced invalid regex '{pattern}': {exc}",
                    )
    return {"config": config}
