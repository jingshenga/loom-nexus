"""AI rule workshop APIs: CRUD, sandbox dry-run, LLM rule generation.

Dry-run reuses the exact pure functions the live extractor runs
(app.extractor.ast_rules). LLM access goes through app.llm (DB-first
config, validated endpoints).
"""
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import db, llm, sqlres
from app.api.auth import require_admin
from app.extractor import ast_rules, ocr_fields
from app.extractor.pipeline import DEFAULT_TRIGGERS, extract_custom_fields, missing_triggers

router = APIRouter(
    prefix="/api/admin/rules", tags=["admin-rules"],
    dependencies=[Depends(require_admin)],
)

SQL_UPSERT = sqlres.load("rule_upsert.sql")
SQL_DELETE = sqlres.load("rule_delete.sql")
SQL_LIST = sqlres.load("rule_list.sql")
SQL_BIND = sqlres.load("rule_bind.sql")
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


class BindBody(BaseModel):
    source_id: int
    priority: int = 10


class ReextractBody(BaseModel):
    source_id: int


class DryRunBody(BaseModel):
    config: dict
    sample_limit: int = Field(50, ge=1, le=200)
    sample_set: Literal["recent", "golden"] = "recent"


class GenerateBody(BaseModel):
    samples: list


class CopyBody(BaseModel):
    rule_name: str


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
async def upsert_rule(body: RuleBody) -> dict:
    pool = _pool()
    if body.rule_type == "bot_collection":
        body.config = {}
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
    rows = await pool.fetch(
        SQL_UPSERT, body.rule_name, body.rule_type,
        json.dumps(body.config, ensure_ascii=False), body.is_active,
    )
    return {"rule_id": rows[0]["rule_id"], "version": rows[0]["version"]}


@router.post("/{rule_id}/copy")
async def copy_rule(rule_id: int, body: CopyBody) -> dict:
    pool = _pool()
    try:
        rows = await pool.fetch(SQL_COPY, rule_id, body.rule_name.strip())
    except Exception as exc:
        if "unique" in str(exc).lower():
            raise HTTPException(status_code=409, detail="规则名已存在") from exc
        raise
    if not rows:
        raise HTTPException(status_code=404, detail="not found or protected")
    return {"rule_id": rows[0]["rule_id"], "version": rows[0]["version"]}


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
    await pool.fetch(SQL_BIND, rule_id, body.source_id, body.priority)
    return {"ok": True}


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
    for username in usernames:
        rows = await _pool().fetch(SQL_PROFILE_COLLECTION_ENQUEUE, rule_id, username, body.source_id)
        if rows:
            jobs.append({"job_id": rows[0]["job_id"], "username": username, "status": rows[0]["status"]})
    if not jobs:
        raise HTTPException(status_code=409, detail="该规则没有已启用并绑定的采集来源/账号")
    return {"jobs": jobs, "queued": len(jobs), "skipped": len(usernames) - len(jobs)}


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


def _golden_values(config, fields, evidence, cards, custom_definitions, text):
    options = config.get("ocr") or {}
    triggers = options.get("trigger_when_missing") or DEFAULT_TRIGGERS
    card_values = {}
    for card in cards:
        for key, value in card["fields"].items():
            if isinstance(value, list):
                existing = card_values.setdefault(key, [])
                if isinstance(existing, list):
                    existing.extend(item for item in value if item not in existing)
            elif value is not None:
                card_values.setdefault(key, value)

    if missing_triggers(fields, triggers):
        for card in cards:
            recovered, proof = ocr_fields.card_to_listing_fields(card["fields"])
            fields, evidence = ast_rules.merge_extraction(fields, evidence, recovered, proof)
    ocr_text = "\n".join(card["raw_text"] for card in cards if card["raw_text"])
    if options.get("enabled") and ocr_text and missing_triggers(fields, triggers):
        recovered, proof = ast_rules.extract_fields(config, " ".join(ocr_text.splitlines()), src="ocr")
        fields, evidence = ast_rules.merge_extraction(fields, evidence, recovered, proof)

    custom = extract_custom_fields(text, [d for d in custom_definitions if d["extraction_source"] == "telegram"])
    if options.get("enabled"):
        custom.update(extract_custom_fields(ocr_text, [d for d in custom_definitions if d["extraction_source"] == "ocr"]))
    return card_values | custom | fields


def _golden_differences(expected, actual):
    comparable = dict(expected["fields"])
    comparable.pop("description", None)
    comparable["username"] = comparable.pop("contact_username", None)
    comparable.update(expected["attributes"])
    comparable.update(expected["custom_fields"])
    differences = {}
    for key, value in comparable.items():
        predicted = actual.get(key)
        if value in (None, "", []) and predicted in (None, "", []):
            continue
        if key in {"price_amount", "age_years", "height_cm", "weight_kg"} and value is not None and predicted is not None:
            try:
                if Decimal(str(value)) == Decimal(str(predicted)):
                    continue
            except InvalidOperation:
                pass
        if value != predicted:
            differences[key] = {"expected": value, "actual": predicted}
    return differences


@router.post("/dryrun")
async def dry_run(body: DryRunBody) -> dict:
    pool = _pool()
    golden = body.sample_set == "golden"
    definitions = []
    if golden:
        rows = await pool.fetch(
            """SELECT listing_id AS raw_id, raw_text, sender_peer_id, media_json, ocr_json, expected_json
               FROM golden_rule_samples ORDER BY saved_at DESC, listing_id DESC LIMIT $1""",
            body.sample_limit,
        )
        definitions = await pool.fetch(
            "SELECT field_key,aliases,extraction_source FROM listing_field_definitions WHERE is_active"
        )
    else:
        rows = await pool.fetch(SQL_SAMPLES, body.sample_limit)
    results = []
    hits = 0
    passed = 0
    for r in rows:
        text = r["raw_text"] or ""
        sender = r["sender_peer_id"] if golden else None
        ok, reason = ast_rules.gate_pass(body.config, text, sender)
        term = ast_rules.hit_lifecycle(body.config, text) if ok else None
        fields, evidence = ast_rules.extract_fields(body.config, text) if ok and (term is None or not golden) else ({}, {})
        result = {
            "raw_id": r["raw_id"],
            "text": text[:120],
            "gate": "pass" if ok else f"blocked ({reason})",
            "lifecycle": term,
            "fields": fields,
        }
        if golden:
            cards = [card for card in _decode_jsonb(r["ocr_json"], [])
                     if card["status"] == "done" and card["classification"] == "info_card"]
            if ok and term is None:
                fields = _golden_values(body.config, fields, evidence, cards, definitions, text)
            result["fields"] = fields
            result["media"] = _decode_jsonb(r["media_json"], [])
            result["differences"] = _golden_differences(_decode_jsonb(r["expected_json"], {}), fields)
            result["passed"] = ok and term is None and not result["differences"]
            passed += int(result["passed"])
        result["matched"] = bool(fields) or term is not None
        hits += int(result["matched"])
        results.append(result)
    return {
        "total": len(results),
        "hits": hits,
        "hit_rate": round(hits / len(results), 3) if results else 0.0,
        "passed": passed if golden else None,
        "sample_set": body.sample_set,
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
