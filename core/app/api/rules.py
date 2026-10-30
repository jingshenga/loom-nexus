"""AI rule workshop APIs: CRUD, sandbox dry-run, LLM rule generation.

Dry-run reuses the exact pure functions the live extractor runs
(app.extractor.ast_rules). LLM access goes through app.llm (DB-first
config, validated endpoints).
"""
import json
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db, llm, sqlres
from app.api.auth import require_admin
from app.extractor import ast_rules

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
    rule_type: Literal["listing", "on_duty"] = "listing"
    config: dict
    is_active: bool = True


class BindBody(BaseModel):
    source_id: int
    priority: int = 10


class ReextractBody(BaseModel):
    source_id: int


class DryRunBody(BaseModel):
    config: dict
    sample_limit: int = 50


class GenerateBody(BaseModel):
    samples: list


class CopyBody(BaseModel):
    rule_name: str


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
