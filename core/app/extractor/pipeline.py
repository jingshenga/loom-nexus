"""Extraction pipeline loop: claim → gate → regex → OCR → lifecycle.

Runs as a background task inside nexus-core. Batches of raw messages
are claimed row-locked from the database, then processed purely
in-process against the bound rule config; every write goes back
through database functions with bound parameters.
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import time

import asyncpg

from app import sqlres
from app.config import get_settings
from app.extractor import ast_rules
from app.extractor.lifecycle_identity import explicit_lifecycle_username
from app.extractor.ocr_fields import equivalent_field, structured_ocr_candidate
from app.extractor.ocr_worker import OcrWorker
from app.extractor.rule_router import (
    active_source_allowed, normalize_telegram_identity, resolve_active_listing, shadow_compare,
)

logger = logging.getLogger("nexus-core.extractor")

SQL_CLAIM = sqlres.load("claim_batch.sql")
SQL_MARK = sqlres.load("extract_mark.sql")
SQL_CREATE_LISTING = sqlres.load("upsert_newest.sql")
SQL_LIFECYCLE_REVIEW = sqlres.load("lifecycle_review_enqueue.sql")
SQL_POLICY = sqlres.load("settings_get_num.sql")
SQL_POLICY_ENABLED = sqlres.load("settings_get.sql")
SQL_MEDIA_FLAGS = sqlres.load("media_ocr_set_flags.sql")
SQL_LISTING_SYNC_MEDIA = sqlres.load("listing_sync_media.sql")
SQL_HISTORY_SOURCE = sqlres.load("history_scan_source.sql")
SQL_CREATE_HISTORY_LISTING = sqlres.load("upsert_history_scan.sql")
SQL_OCR_ENQUEUE_RAW = sqlres.load("media_ocr_enqueue_raw.sql")
SQL_SHADOW_BINDINGS = sqlres.load("rule_shadow_bindings.sql")
SQL_ACTIVE_REVIEW_UPSERT = sqlres.load("rule_active_review_upsert.sql")
SQL_OCR_RECHECK_ENQUEUE = sqlres.load("active_ocr_recheck_enqueue.sql")
SQL_OCR_RECHECK_CLAIM = sqlres.load("active_ocr_recheck_claim.sql")
SQL_OCR_RECHECK_FINISH = sqlres.load("active_ocr_recheck_finish.sql")
SQL_OCR_RECHECK_CONTEXT = sqlres.load("active_ocr_recheck_context.sql")
SQL_ACTIVE_PARSE_RUN = sqlres.load("rule_parse_run_active_insert.sql")
SQL_SHADOW_RECORD = sqlres.load("rule_shadow_record.sql")
SQL_SHADOW_PARSE_RUN = sqlres.load("rule_parse_run_shadow_insert.sql")
_SHADOW_LOGGED_AT = {}

FALLBACK_RULE = {"gate": {}, "extract": {}, "lifecycle_terms": []}

# field weights for the confidence score (sum = 1.0)
FIELD_WEIGHTS = {
    "nickname": 0.30,
    "price_amount": 0.30,
    "district": 0.15,
    "username": 0.15,
    "price_currency": 0.10,
}


def score_confidence(fields: dict, evidence: dict, media_count: int) -> float:
    """Deterministic weighted completeness score in [0, 1]."""
    score = sum(
        weight for name, weight in FIELD_WEIGHTS.items() if fields.get(name)
    )
    # small bonus when the message carries media (photo albums are
    # usually complete cards), capped so media alone cannot publish
    if media_count:
        score = min(1.0, score + 0.05)
    return round(score, 3)


async def _load_policy(pool) -> tuple:
    """(auto_publish_enabled, threshold) from app_settings."""
    enabled_row = await pool.fetch(SQL_POLICY_ENABLED, "autopublish_enabled")
    enabled = not enabled_row or (enabled_row[0]["value"] != "0")
    thresh_rows = await pool.fetch(SQL_POLICY, "autopublish_threshold", 0.8)
    return enabled, float(thresh_rows[0]["value"])


def _shadow_sampled(rate: float, source_id: int, raw_id: int, content_hash: str | None) -> bool:
    if rate >= 1:
        return True
    if rate <= 0:
        return False
    seed = f"{source_id}:{raw_id}:{content_hash or ''}".encode()
    value = int.from_bytes(hashlib.sha256(seed).digest()[:8], "big") / 2**64
    return value < rate


def _shadow_safe_candidate(candidate):
    if not candidate:
        return None
    fields = candidate.get("fields") or {}
    return {
        "rule_id": candidate.get("rule_id"),
        "rule_version_id": candidate.get("rule_version_id"),
        "rule_ids": candidate.get("rule_ids"),
        "matched": bool(candidate.get("matched")),
        "reason": candidate.get("reason"),
        "identity_present": bool(candidate.get("identity")),
        "field_keys": sorted(fields),
        "missing_fields": sorted(candidate.get("missing_fields") or []),
    }


def _shadow_safe_trace(trace):
    allowed = ("rule_id", "rule_version_id", "rule_version_known", "rule_type", "priority",
               "supported", "matched", "reason", "missing_fields", "ocr_would_be_considered")
    return [{key: item[key] for key in allowed if key in item} for item in trace]


def _shadow_safe_prediction(prediction, actual=None):
    selected = prediction.get("selected") or {}
    actual_fields = set((actual or {}).get("fields") or {})
    predicted_fields = set(selected.get("fields") or {})
    changed_fields = sorted(
        key for key in actual_fields | predicted_fields
        if (actual or {}).get("fields", {}).get(key) != selected.get("fields", {}).get(key)
    )
    return {
        "purpose": prediction.get("purpose"),
        "match_group": prediction.get("match_group"),
        "mode": prediction.get("mode"),
        "reason": prediction.get("reason"),
        "selected": _shadow_safe_candidate(selected),
        "conflicts": [{"kind": item.get("kind"), "field": item.get("field"),
                       "candidate_count": len(item.get("values") or [])}
                      for item in prediction.get("conflicts", [])],
        "field_diff_keys": changed_fields,
        "identity_diff": normalize_telegram_identity((actual or {}).get("identity"))
        != normalize_telegram_identity(selected.get("identity")),
        "trace": _shadow_safe_trace(prediction.get("trace", [])),
    }


def _shadow_safe_comparison(result):
    actual = result.get("actual_legacy")
    return _shadow_safe_candidate(actual), {
        "changed": bool(result.get("changed")),
        "predictions": [_shadow_safe_prediction(item, actual)
                        for item in result.get("shadow_predictions", [])],
    }


def _bounded_shadow_json(value, max_bytes: int) -> str:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(payload.encode("utf-8")) <= max_bytes:
        return payload
    if isinstance(value, dict) and isinstance(value.get("predictions"), list):
        compact = {"changed": bool(value.get("changed")), "truncated": True,
                   "predictions": []}
        for item in value["predictions"][:20]:
            selected = item.get("selected") or {}
            compact["predictions"].append({
                "purpose": item.get("purpose"),
                "reason": item.get("reason"),
                "selected": {key: selected.get(key) for key in
                             ("rule_id", "rule_version_id", "identity_present", "field_keys")
                             if key in selected},
                "conflicts": item.get("conflicts", [])[:20],
                "field_diff_keys": item.get("field_diff_keys", [])[:20],
                "identity_diff": bool(item.get("identity_diff")),
            })
        payload = json.dumps(compact, ensure_ascii=False, separators=(",", ":"), default=str)
        if len(payload.encode("utf-8")) <= max_bytes:
            return payload
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    compact = {"truncated": True, "sha256": digest, "serialized_bytes": len(payload.encode("utf-8"))}
    return json.dumps(compact, separators=(",", ":"))


def _decode_jsonb(value, fallback):
    """asyncpg returns jsonb columns as raw JSON text unless a codec is
    registered; accept both text and already-decoded forms."""
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def extract_custom_fields(text: str, definitions: list) -> dict:
    """Extract configured fields from text lines using each field's aliases."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    found = {}
    for definition in definitions:
        aliases_value = definition["aliases"] or []
        if isinstance(aliases_value, str):
            aliases_value = _decode_jsonb(aliases_value, [])
        aliases = [definition["field_key"], *aliases_value]
        for line in lines:
            for alias in aliases:
                match = re.search(
                    rf"{re.escape(str(alias))}\s*[:：=\-]?\s*(.+)$",
                    line, re.IGNORECASE,
                )
                if match:
                    value = match.group(1).strip()
                    if value:
                        found[definition["field_key"]] = value
                        break
            if definition["field_key"] in found:
                break
    return found


class Extractor:
    def __init__(self, pool: asyncpg.Pool, media_dir: str, ocr_enabled: bool, ocr_worker=None, media_ocr_processor=None,
                 shadow_sample_rate: float = 1.0, shadow_trace_max_bytes: int = 8192,
                 shadow_error_log_interval_s: int = 60):
        self._pool = pool
        self._media_dir = media_dir
        self._ocr_enabled = ocr_enabled
        self._ocr = ocr_worker or OcrWorker()
        self._media_ocr_processor = media_ocr_processor
        self._shadow_sample_rate = min(1.0, max(0.0, float(shadow_sample_rate)))
        self._shadow_trace_max_bytes = max(1024, int(shadow_trace_max_bytes))
        self._shadow_error_log_interval_s = max(10, int(shadow_error_log_interval_s))

    async def run(self, stop: asyncio.Event, batch_size: int, idle_s: float) -> None:
        logger.info(
            "extractor loop started (batch=%d idle=%.1fs ocr=%s)",
            batch_size, idle_s, self._ocr_enabled,
        )
        while not stop.is_set():
            rows = await self._pool.fetch(SQL_CLAIM, batch_size)
            for row in rows:
                await self._process_one(row)
            rechecks = await self._pool.fetch(SQL_OCR_RECHECK_CLAIM, batch_size)
            for task in rechecks:
                await self._process_ocr_recheck(task)
            await self._rollback_sources_with_stale_guard()
            if not rows and not rechecks:
                await _sleep(stop, idle_s)

    async def _rollback_sources_with_stale_guard(self):
        """Revoke Active independently if the supervisor heartbeat disappears."""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.fetchval("SELECT pg_advisory_xact_lock($1)", 814271002)
                sources = await conn.fetch(
                    "SELECT s.id,s.router_mode,g.heartbeat_at,g.health_status "
                    "FROM ingest_sources s LEFT JOIN active_router_guard_state g ON g.source_id=s.id "
                    "WHERE s.router_mode='active' AND (g.source_id IS NULL "
                    "OR g.health_status<>'healthy' OR g.heartbeat_at<NOW()-INTERVAL '90 seconds') "
                    "FOR UPDATE OF s"
                )
                for source in sources:
                    await conn.execute(
                        "UPDATE ingest_sources SET router_mode='legacy' WHERE id=$1", source["id"],
                    )
                    await conn.execute(
                        "INSERT INTO rule_router_mode_events(source_id,previous_mode,new_mode,actor_user_id) "
                        "VALUES($1,$2,'legacy',NULL)", source["id"], source["router_mode"],
                    )
                    await conn.execute(
                        "UPDATE active_router_guard_state SET active_started_at=NULL,"
                        "active_baseline_json='{}'::jsonb,last_rollback_reason='guard_heartbeat_stale',"
                        "updated_at=NOW() WHERE source_id=$1", source["id"],
                    )
                    logger.error("Active source %s returned to Legacy: supervisor heartbeat stale", source["id"])

    async def _process_one(self, row) -> None:
        raw_id = row["raw_id"]
        text = row["raw_text"] or ""
        rule = _decode_jsonb(row["rule_config"], None)
        if rule is None:
            rule = FALLBACK_RULE
        media = _decode_jsonb(row["media_json"], [])
        await self._record_shadow_if_enabled(row, rule)
        try:
            active = await self._active_route(row)
            if active and active.get("review_reason"):
                if active["review_reason"] == "lifecycle_requires_human_review":
                    selected = (active.get("result") or {}).get("selected") or {}
                    fields = selected.get("fields") or {}
                    term = selected.get("lifecycle") or ""
                    target_username = explicit_lifecycle_username(text, fields.get("username"))
                    reason = (
                        "identity_unresolved" if target_username is None
                        else "source_or_sender_authority_unverified"
                    )
                    await self._pool.fetch(
                        SQL_LIFECYCLE_REVIEW, raw_id, row["sender_peer_id"], term,
                        target_username or "", reason,
                        json.dumps({
                            "identity_basis": "single_explicit_username_in_message" if target_username else "unresolved",
                            "candidate_username": target_username or "",
                            "sender_authority_verified": False,
                            "forward_metadata_available": False,
                            "automatic_action_blocked_reason": reason,
                        }, ensure_ascii=False),
                    )
                else:
                    await self._enqueue_active_review(row, active)
                await self._mark(raw_id, "dropped")
                return
            active_fields = active.get("fields") if active else None
            active_evidence = active.get("evidence") if active else None
            if active and active.get("rule_config"):
                rule = active["rule_config"]
            history_source = await self._pool.fetchval(SQL_HISTORY_SOURCE, row["source_id"])
            for item in media:
                if item.get("kind") != "photo" or item.get("ocr_status") in (
                    "processing", "done", "failed",
                ):
                    continue
                item["visibility"] = "pending"
                item["ocr_status"] = "pending"
                await self._pool.fetch(
                    SQL_MEDIA_FLAGS, raw_id, item.get("path", ""),
                    "pending", "pending", "uncertain",
                )

            ok, reason = ast_rules.gate_pass(rule, text, row["sender_peer_id"])
            if not ok:
                await self._mark(raw_id, "dropped")
                logger.info("raw %s dropped by gate (%s)", raw_id, reason)
                return

            if active_fields is None:
                fields, evidence = ast_rules.extract_fields(rule, text)
            else:
                fields, evidence = active_fields, active_evidence or {}
            term = ast_rules.hit_lifecycle(rule, text)
            if term is not None:
                target_username = explicit_lifecycle_username(
                    text, fields.get("username"),
                )
                reason = (
                    "identity_unresolved" if target_username is None
                    else "source_or_sender_authority_unverified"
                )
                await self._pool.fetch(
                    SQL_LIFECYCLE_REVIEW, raw_id, row["sender_peer_id"], term,
                    target_username or "", reason,
                    json.dumps({
                        "identity_basis": "single_explicit_username_in_message" if target_username else "unresolved",
                        "candidate_username": target_username or "",
                        "sender_authority_verified": False,
                        "forward_metadata_available": False,
                        "automatic_action_blocked_reason": reason,
                    }, ensure_ascii=False),
                )
                await self._mark(raw_id, "dropped")
                logger.info(
                    "raw %s lifecycle '%s' queued for review (target=%s reason=%s)",
                    raw_id, term, target_username or "unresolved", reason,
                )
                return

            field_definitions = await self._pool.fetch(
                "SELECT field_key,aliases,extraction_source,required_for_publish "
                "FROM listing_field_definitions WHERE is_active"
            )
            custom_fields = extract_custom_fields(
                text,
                [row for row in field_definitions if row["extraction_source"] == "telegram"],
            )
            if history_source:
                evidence["collection_method"] = "checkin_history_scan"
                evidence["review_bucket"] = "checkin_supplement"
                if not fields.get("username"):
                    match = re.search(r"(?:TG|电报)\s*[:：]?\s*@?([A-Za-z][A-Za-z0-9_]{3,31})", text, re.I)
                    if match:
                        fields["username"] = match.group(1).lower()
            ocr_cfg = (rule.get("ocr") or {})
            missing = [
                key for key in (ocr_cfg.get("trigger_when_missing") or [])
                if key == "price" and "price_amount" not in fields
            ]
            missing.extend(
                row["field_key"] for row in field_definitions
                if row["extraction_source"] == "ocr" and not custom_fields.get(row["field_key"])
            )
            if not text.strip():
                missing.append("description")
            if (
                self._ocr_enabled and ocr_cfg.get("enabled")
                and missing and any(m.get("path") for m in media)
            ):
                paths = [
                    os.path.join(self._media_dir, m["path"])
                    for m in media
                    if m.get("path") and str(m.get("kind")) == "photo"
                ]
                if paths:
                    if self._media_ocr_processor is not None:
                        ocr_texts, ocr_pending = await self._media_ocr_processor.read_raw_texts(raw_id, [
                            m["path"] for m in media
                            if m.get("path") and str(m.get("kind")) == "photo"
                        ])
                    else:
                        ocr_texts = await self._ocr.read_texts(paths)
                        ocr_pending = False
                    if ocr_pending:
                        await self._mark(raw_id, "new")
                        return
                    if ocr_texts:
                        custom_fields.update(extract_custom_fields(
                            "\n".join(ocr_texts),
                            [row for row in field_definitions if row["extraction_source"] == "ocr"],
                        ))
                        ocr_fields, ocr_evidence = ast_rules.extract_fields(
                            rule, " ".join(ocr_texts), src="ocr",
                        )
                        fields, evidence = ast_rules.merge_extraction(
                            fields, evidence, ocr_fields, ocr_evidence,
                        )
                        logger.info(
                            "raw %s ocr fallback read %d line(s) from %d image(s)",
                            raw_id, len(ocr_texts), len(paths),
                        )
                    refreshed_media = await self._pool.fetchval(
                        "SELECT media_json FROM raw_messages WHERE id=$1", raw_id,
                    )
                    media = _decode_jsonb(refreshed_media, media)

            has_structure = any(
                k in fields
                for k in ("nickname", "price_amount", "district", "username")
            )
            if not has_structure and not (history_source and (text.strip() or media)):
                await self._mark(raw_id, "dropped")
                logger.info("raw %s dropped (no fields extracted)", raw_id)
                return
            if not has_structure and history_source:
                confidence = 0.0
            else:
                confidence = score_confidence(fields, evidence, len(media))
            ocr_used = any(
                isinstance(value, dict) and value.get("src") == "ocr"
                for value in evidence.values()
            )
            auto_enabled, threshold = await _load_policy(self._pool)
            description = text.strip()
            ocr_intro = "\n".join(ocr_texts).strip() if "ocr_texts" in locals() else ""
            if ocr_intro:
                marker = "图片识别内容：" + ocr_intro
                if marker not in description:
                    description = "\n\n".join(part for part in (description, marker) if part)
            complete_for_publish = await self._pool.fetchval(
                "SELECT listing_meets_publish_requirements($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb)",
                fields.get("nickname") or "", fields.get("price_amount"), description,
                fields.get("username") or "", fields.get("bot_url") or "",
                json.dumps(media), json.dumps(custom_fields, ensure_ascii=False),
            )
            # below-threshold or OCR-sourced data needs human review;
            # above-threshold regex-clean data publishes directly
            should_publish = (
                auto_enabled and complete_for_publish and not ocr_used and confidence >= threshold
            )
            if ocr_used:
                confidence = min(confidence, 0.79)  # cap OCR confidence
            rows = await self._pool.fetch(
                SQL_CREATE_HISTORY_LISTING if history_source else SQL_CREATE_LISTING,
                raw_id,
                None,  # sender is the channel bot, NOT the contact; the
                       # contact identity is the extracted username
                fields.get("username"),
                fields.get("nickname"),
                fields.get("district"),
                fields.get("price_amount"),
                fields.get("price_currency") or "CNY",
                description,
                json.dumps(media),
                json.dumps(evidence, ensure_ascii=False),
                confidence,
                should_publish,
                row["posted_at"],  # the card's "publish time" is the
                                   # message's own time, not collect time
            )
            result = rows[0]
            await self._pool.execute(
                SQL_LISTING_SYNC_MEDIA, result["listing_id"], raw_id,
            )
            if custom_fields:
                await self._pool.execute(
                    "UPDATE listings SET custom_fields=COALESCE(custom_fields,'{}'::jsonb)||$2::jsonb, "
                    "updated_at=NOW() WHERE id=$1 AND NOT (manual_fields ? 'custom_fields')",
                    result["listing_id"], json.dumps(custom_fields, ensure_ascii=False),
                )
            await self._mark(raw_id, "done")
            extra = ""
            if result["conflicted"]:
                extra = (
                    f", newest-wins: archived {result['conflicted']} older card(s)"
                )
            logger.info(
                "raw %s -> listing %s (%s, conf=%.2f, %s)%s",
                raw_id, result["listing_id"],
                ",".join(sorted(evidence.keys())) or "no evidence",
                confidence,
                result["status"], extra,
            )
            if should_publish and result["conflicted"]:
                # data changed for a known contact: park it for review
                logger.info(
                    "listing %s conflicts with archived sibling; recheck advised",
                    result["listing_id"],
                )
        except Exception:
            logger.exception("raw %s extraction failed; returning to new", raw_id)
            await self._mark(raw_id, "new")

    async def _active_route(self, row):
        """Return a review-only Active result; never let a denied Active job fall through to Legacy."""
        try:
            mode = await self._pool.fetchval(
                "SELECT router_mode FROM ingest_sources WHERE id=$1", row["source_id"],
            )
            if mode != "active":
                return None
            settings = get_settings()
            guard_ok = await self._pool.fetchval(
                "SELECT EXISTS(SELECT 1 FROM active_router_guard_state "
                "WHERE source_id=$1 AND health_status='healthy' "
                "AND active_started_at IS NOT NULL "
                "AND heartbeat_at >= NOW()-INTERVAL '90 seconds')",
                row["source_id"],
            )
            if not guard_ok:
                await self._rollback_sources_with_stale_guard()
                return {"review_reason": "active_guard_unavailable", "result": {
                    "reason": "active_guard_unavailable", "trace": [], "conflicts": [],
                }}
            if not active_source_allowed(
                row["source_id"], enabled=settings.active_router_enabled,
                allowed_source_ids=settings.active_router_source_ids,
                kill_switch=settings.active_router_kill_switch,
            ):
                return {"review_reason": "active_router_disabled", "result": {
                    "reason": "active_router_disabled", "trace": [], "conflicts": [],
                }}
            bindings = [dict(item) for item in await self._pool.fetch(SQL_SHADOW_BINDINGS, row["source_id"])]
            result = resolve_active_listing(bindings, row["raw_text"] or "", row["sender_peer_id"])
            ocr_evidence = None
            ocr_pending = False
            if settings.ocr_review_assist_enabled and int(row["source_id"]) == 2:
                result, ocr_evidence, ocr_pending = await self._supplement_active_from_ocr(
                    row, bindings, result,
                )
                if ocr_pending:
                    await self._schedule_ocr_recheck(row, bindings)
            await self._record_active_parse_runs(row, bindings, result)
            selected = result.get("selected") or {}
            if result.get("conflicts"):
                return {"review_reason": "rule_conflict", "result": result,
                        "ocr_evidence": ocr_evidence, "ocr_pending": ocr_pending}
            if not selected:
                return {"review_reason": "no_rule_match", "result": result,
                        "ocr_evidence": ocr_evidence, "ocr_pending": ocr_pending}
            if selected.get("lifecycle"):
                return {"review_reason": "lifecycle_requires_human_review", "result": result,
                        "ocr_evidence": ocr_evidence, "ocr_pending": ocr_pending}
            if not selected.get("identity"):
                return {"review_reason": "identity_unresolved", "result": result,
                        "ocr_evidence": ocr_evidence, "ocr_pending": ocr_pending}
            selected_ids = set(selected.get("rule_ids") or [])
            chosen_binding = next(
                (binding for binding in bindings if binding.get("rule_id") in selected_ids), None,
            )
            if not chosen_binding:
                return {"review_reason": "selected_rule_missing", "result": result,
                        "ocr_evidence": ocr_evidence, "ocr_pending": ocr_pending}
            config = chosen_binding.get("config_json") or {}
            if isinstance(config, str):
                config = json.loads(config)
            return {"fields": selected.get("fields") or {},
                    "evidence": selected.get("evidence") or {}, "rule_config": config,
                    "review_reason": "human_review_required", "result": result,
                    "ocr_evidence": ocr_evidence, "ocr_pending": ocr_pending}
        except Exception as exc:
            logger.exception("active router failed (source=%s raw=%s); parking for review",
                             row["source_id"], row["raw_id"])
            return {"review_reason": "active_router_error", "result": {
                "reason": "active_router_error", "error_class": type(exc).__name__,
                "trace": [], "conflicts": [],
            }}

    async def _supplement_active_from_ocr(self, row, bindings, result):
        """Fuse persisted OCR fields into review-only Active candidates."""
        media = _decode_jsonb(row.get("media_json"), [])
        paths = list(dict.fromkeys(
            item.get("path") for item in media
            if item.get("kind") == "photo" and item.get("path")
        ))
        if not paths:
            return result, None, False
        records = await self._pool.fetch(
            "SELECT id,media_path,status,classification,review_status,raw_text,fields_json,"
            "evidence_json,confidence,engine_version,updated_at FROM listing_media_ocr "
            "WHERE raw_id=$1 AND media_path=ANY($2::text[]) ORDER BY media_path,id",
            row["raw_id"], paths,
        )
        completed = []
        for record in records:
            item = dict(record)
            ocr_fields = _decode_jsonb(item["fields_json"], {})
            business_keys = {
                "location_text", "service_text", "features_text", "restriction_text",
                "price_text", "price_1p_text", "price_2p_text", "contact_handles",
            }
            if (item["status"] == "done" and item["review_status"] != "not_card"
                    and item["classification"] not in {"profile_photo", "other"}
                    and (item["raw_text"] or ocr_fields)
                    and (item["classification"] != "uncertain"
                         or len(business_keys.intersection(ocr_fields)) >= 2)):
                completed.append(item)
        pending = any(item["status"] in {"pending", "processing"} for item in records)
        if not completed:
            return result, None, pending or not records

        metadata = [{
            "ocr_id": item["id"],
            "media_path": item["media_path"], "engine_version": item["engine_version"],
            "classification": item["classification"], "confidence": float(item["confidence"] or 0),
            "updated_at": item["updated_at"].isoformat() if item["updated_at"] else None,
            "fields": _decode_jsonb(item["fields_json"], {}),
            "evidence": _decode_jsonb(item["evidence_json"], {}),
        } for item in completed]
        result = dict(result)
        result["ocr_assist"] = metadata
        text_selected = result.get("selected") or {}
        ocr_matches = [resolve_active_listing(bindings, item["raw_text"] or "", row["sender_peer_id"])
                       for item in completed]
        result["trace"] = list(result.get("trace") or []) + [
            {**trace, "evidence_source": "ocr"}
            for match in ocr_matches for trace in match.get("trace", [])
        ]
        selected_ocr = []
        ocr_conflicts = []
        for item, match in zip(completed, ocr_matches):
            if match.get("conflicts"):
                ocr_conflicts.extend({"kind": conflict.get("kind", "ocr_rule_conflict"),
                                     "field": conflict.get("field"), "source": "ocr_rule"}
                                    for conflict in match["conflicts"])
            parsed = match.get("selected") or {}
            parsed_fields = dict(parsed.get("fields") or {})
            parsed_evidence = {
                key: {**(value if isinstance(value, dict) else {}), "src": "ocr",
                      "ocr_id": item["id"], "engine_version": item["engine_version"]}
                for key, value in (parsed.get("evidence") or {}).items()
            }
            structured = structured_ocr_candidate(
                _decode_jsonb(item["fields_json"], {}),
                _decode_jsonb(item["evidence_json"], {}),
                ocr_id=item["id"], engine_version=item["engine_version"],
                confidence=float(item["confidence"] or 0),
                text_identity=text_selected.get("identity") or parsed.get("identity") or "",
            )
            ocr_conflicts.extend(structured["conflicts"])
            for key, value in structured["fields"].items():
                if key in parsed_fields and not equivalent_field(key, parsed_fields[key], value):
                    ocr_conflicts.append({"kind": "field", "field": key,
                                          "source": "ocr_text_vs_fields_json",
                                          "values": [parsed_fields[key], value],
                                          "evidence": {"text": parsed_evidence.get(key),
                                                       "ocr": structured["evidence"].get(key)}})
                    parsed_fields.pop(key, None)
                    parsed_evidence.pop(key, None)
                elif key not in parsed_fields:
                    parsed_fields[key] = value
                    parsed_evidence[key] = structured["evidence"].get(key)
            identity = parsed.get("identity") or structured["fields"].get("username") or ""
            if identity:
                parsed_fields.setdefault("username", identity)
                parsed_evidence.setdefault("username", structured["evidence"].get("username") or parsed_evidence.get("username"))
            selected_ocr.append({
                "fields": parsed_fields, "evidence": parsed_evidence, "identity": identity,
                "rule_ids": parsed.get("rule_ids") or [], "clues": structured["clues"],
            })

        # Merge media in stable path/id order and do not choose between conflicting values.
        field_values = {}
        identities = []
        rule_ids = []
        clues = {}
        for candidate in selected_ocr:
            if candidate.get("identity"):
                identities.append((candidate["identity"], candidate["evidence"].get("username")))
            rule_ids.extend(candidate.get("rule_ids") or [])
            clues.update(candidate.get("clues") or {})
            for key, value in candidate["fields"].items():
                field_values.setdefault(key, []).append((value, candidate["evidence"].get(key)))
        ocr_selected = {"fields": {}, "evidence": {}, "rule_ids": list(dict.fromkeys(rule_ids)),
                        "identity": "", "clues": clues}
        for key, values in field_values.items():
            unique = []
            for value, evidence in values:
                if not any(equivalent_field(key, value, old_value) for old_value, _ in unique):
                    unique.append((value, evidence))
            if len(unique) == 1:
                ocr_selected["fields"][key] = unique[0][0]
                if unique[0][1]:
                    ocr_selected["evidence"][key] = unique[0][1]
            else:
                ocr_conflicts.append({"kind": "field", "field": key,
                                      "source": "multiple_ocr_media",
                                      "values": [value for value, _ in unique],
                                      "evidence": [evidence for _, evidence in unique]})
        unique_identities = {}
        for value, evidence in identities:
            unique_identities.setdefault(normalize_telegram_identity(value), (value, evidence))
        if len(unique_identities) == 1:
            value, evidence = next(iter(unique_identities.values()))
            ocr_selected["identity"] = value
            if evidence:
                ocr_selected["evidence"].setdefault("username", evidence)
        elif len(unique_identities) > 1:
            ocr_conflicts.append({"kind": "telegram_identity", "source": "multiple_ocr_media",
                                  "values": [item[0] for item in unique_identities.values()],
                                  "evidence": [item[1] for item in unique_identities.values()]})

        if ocr_conflicts:
            result["conflicts"] = list(result.get("conflicts") or []) + ocr_conflicts
        if not text_selected:
            structural_fields = {"nickname", "district", "price_amount"}
            if len(structural_fields.intersection(ocr_selected["fields"])) < 1 or not (
                    ocr_selected.get("identity") or len(structural_fields.intersection(ocr_selected["fields"])) >= 2):
                result["ocr_clues"] = ocr_selected["clues"]
                return result, metadata, False
            result["selected"] = ocr_selected
            result["reason"] = "ocr_structured_candidate"
            return result, metadata, False

        text_identity = normalize_telegram_identity(text_selected.get("identity"))
        ocr_identity = normalize_telegram_identity(ocr_selected.get("identity"))
        if text_identity and ocr_identity and text_identity != ocr_identity:
            result["conflicts"] = list(result.get("conflicts") or []) + [{
                "kind": "telegram_identity", "source": "telegram_text_vs_ocr",
                "values": [text_selected.get("identity"), ocr_selected.get("identity")],
                "evidence": {"text": text_selected.get("evidence", {}).get("username"),
                             "ocr": ocr_selected.get("evidence", {}).get("username")},
            }]
        merged = dict(text_selected)
        fields = dict(text_selected.get("fields") or {})
        evidence = dict(text_selected.get("evidence") or {})
        for key, value in (ocr_selected.get("fields") or {}).items():
            if key in fields and not equivalent_field(key, fields[key], value):
                result["conflicts"] = list(result.get("conflicts") or []) + [
                    {"kind": "field", "field": key, "source": "telegram_text_vs_ocr",
                     "values": [fields[key], value],
                     "evidence": {"text": evidence.get(key), "ocr": ocr_selected["evidence"].get(key)}},
                ]
                continue
            if key not in fields:
                fields[key] = value
                evidence[key] = (ocr_selected.get("evidence") or {}).get(key, {"src": "ocr"})
        if ocr_identity and not text_identity:
            fields.setdefault("username", ocr_selected.get("identity"))
            evidence.setdefault("username", ocr_selected.get("evidence", {}).get("username", {"src": "ocr"}))
        merged["fields"] = fields
        merged["evidence"] = evidence
        merged["identity"] = text_selected.get("identity") or ocr_selected.get("identity") or ""
        merged["rule_ids"] = list(dict.fromkeys(
            (text_selected.get("rule_ids") or []) + (ocr_selected.get("rule_ids") or [])
        ))
        result["selected"] = merged
        result["ocr_clues"] = ocr_selected["clues"]
        if result.get("conflicts"):
            result["reason"] = "ocr_text_conflict"
        else:
            result["reason"] = "ocr_supplement_merge"
        return result, metadata, False

    async def _schedule_ocr_recheck(self, row, bindings):
        media = _decode_jsonb(row.get("media_json"), [])
        paths = list(dict.fromkeys(
            item.get("path") for item in media
            if item.get("kind") == "photo" and item.get("path")
        ))
        if not paths:
            return
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                for path in paths:
                    await conn.execute(SQL_OCR_ENQUEUE_RAW, row["raw_id"], path)
                versions = sorted({int(binding["rule_version_id"]) for binding in bindings
                                   if binding.get("rule_version_id") is not None})
                await conn.execute(
                    SQL_OCR_RECHECK_ENQUEUE, row["raw_id"], row["source_id"], versions,
                )

    async def _process_ocr_recheck(self, task):
        """Refresh only the original open Active review item; never claim a raw message."""
        task_id = task["id"]
        try:
            row = await self._pool.fetchrow(SQL_OCR_RECHECK_CONTEXT, task["raw_id"])
            if (not row or row["extract_state"] != "dropped"
                    or int(row["revision_id"]) != int(task["raw_revision_id"])
                    or row["content_hash"] != task["content_hash"]):
                await self._finish_ocr_recheck(task_id, "stale")
                return
            queue_status = await self._pool.fetchval(
                "SELECT status FROM rule_active_review_queue "
                "WHERE source_id=$1 AND raw_id=$2 AND content_hash=$3",
                task["source_id"], task["raw_id"], task["content_hash"],
            )
            if queue_status != "open":
                await self._finish_ocr_recheck(task_id, "stale")
                return
            mode = await self._pool.fetchval(
                "SELECT router_mode FROM ingest_sources WHERE id=$1", task["source_id"],
            )
            guard_ok = await self._pool.fetchval(
                "SELECT EXISTS(SELECT 1 FROM active_router_guard_state "
                "WHERE source_id=$1 AND health_status='healthy' "
                "AND active_started_at IS NOT NULL "
                "AND heartbeat_at >= NOW()-INTERVAL '90 seconds')",
                task["source_id"],
            )
            settings = get_settings()
            if (mode != "active" or int(row["mode_event_id"]) != int(task["mode_event_id"])
                    or not guard_ok
                    or not settings.ocr_review_assist_enabled
                    or not active_source_allowed(
                        task["source_id"], enabled=settings.active_router_enabled,
                        allowed_source_ids=settings.active_router_source_ids,
                        kill_switch=settings.active_router_kill_switch,
                    )):
                await self._finish_ocr_recheck(task_id, "stale")
                return

            bindings = [dict(item) for item in await self._pool.fetch(
                SQL_SHADOW_BINDINGS, task["source_id"],
            )]
            versions = sorted({int(binding["rule_version_id"]) for binding in bindings
                               if binding.get("rule_version_id") is not None})
            if versions != sorted(int(value) for value in task["rule_version_ids"]):
                await self._finish_ocr_recheck(task_id, "stale")
                return

            result = resolve_active_listing(
                bindings, row["raw_text"] or "", row["sender_peer_id"],
            )
            result, ocr_evidence, pending = await self._supplement_active_from_ocr(
                row, bindings, result,
            )
            if pending:
                await self._finish_ocr_recheck(task_id, "retry", "OCR media still pending")
                return
            if not ocr_evidence:
                await self._finish_ocr_recheck(task_id, "failed", "No completed OCR evidence")
                return

            selected = result.get("selected") or {}
            if result.get("conflicts"):
                reason = "rule_conflict"
            elif selected.get("lifecycle"):
                term = selected.get("lifecycle") or ""
                fields = selected.get("fields") or {}
                target = explicit_lifecycle_username(row["raw_text"] or "", fields.get("username"))
                lifecycle_reason = "identity_unresolved" if target is None else "source_or_sender_authority_unverified"
                await self._pool.fetch(
                    SQL_LIFECYCLE_REVIEW, task["raw_id"], row["sender_peer_id"], term,
                    target or "", lifecycle_reason,
                    json.dumps({"identity_basis": "single_explicit_username_in_message" if target else "unresolved",
                                "candidate_username": target or "", "sender_authority_verified": False,
                                "forward_metadata_available": False,
                                "automatic_action_blocked_reason": lifecycle_reason,
                                "ocr_recheck_id": task_id}, ensure_ascii=False),
                )
                await self._finish_ocr_recheck(task_id, "done")
                return
            elif not selected:
                reason = "no_rule_match"
            elif not selected.get("identity"):
                reason = "identity_unresolved"
            else:
                reason = "human_review_required"
            active = {"review_reason": reason, "result": result,
                      "ocr_evidence": ocr_evidence, "ocr_pending": False}
            await self._enqueue_active_review(row, active)
            await self._finish_ocr_recheck(task_id, "done")
        except Exception as exc:
            logger.exception("OCR review recheck failed (task=%s raw=%s)", task_id, task.get("raw_id"))
            try:
                await self._finish_ocr_recheck(task_id, "retry", type(exc).__name__)
            except Exception:
                logger.exception("OCR review recheck lease remains for recovery (task=%s)", task_id)

    async def _finish_ocr_recheck(self, task_id, status, error=""):
        await self._pool.execute(SQL_OCR_RECHECK_FINISH, task_id, status, error)

    async def _enqueue_active_review(self, row, active):
        result = active.get("result") or {}
        trace = result.get("trace") or []
        rule_ids = sorted({int(item["rule_id"]) for item in trace
                           if item.get("matched") and item.get("rule_id") is not None})
        context = await self._pool.fetchrow(
            "SELECT id,content_hash FROM raw_message_revisions WHERE raw_id=$1 ORDER BY revision DESC LIMIT 1",
            row["raw_id"],
        )
        diagnostics = {
            "reason": active["review_reason"],
            "raw_revision_id": context["id"] if context else None,
            "trace": [{key: item.get(key) for key in (
            "rule_id", "rule_version_id", "rule_type", "priority", "supported", "matched",
            "reason", "evidence_source") if key in item} for item in trace],
            "conflict_kinds": sorted({item.get("kind", "unknown") for item in result.get("conflicts", [])}),
            "conflicts": [{key: item.get(key) for key in
                           ("kind", "field", "source", "values", "evidence") if key in item}
                          for item in result.get("conflicts", [])],
        }
        selected = result.get("selected") or {}
        candidate = {
            "fields": selected.get("fields") or {},
            "evidence": selected.get("evidence") or {},
            "identity": selected.get("identity") or "",
            "rule_ids": selected.get("rule_ids") or rule_ids,
            "ocr_assist": active.get("ocr_evidence") or result.get("ocr_assist") or [],
            "ocr_clues": result.get("ocr_clues") or {},
            "raw_revision_id": context["id"] if context else None,
            "content_hash": context["content_hash"] if context else None,
        }
        await self._pool.execute(
            SQL_ACTIVE_REVIEW_UPSERT, row["source_id"], row["raw_id"],
            context["content_hash"] if context else "unknown", active["review_reason"],
            rule_ids, json.dumps(diagnostics, ensure_ascii=False),
            json.dumps(candidate, ensure_ascii=False),
        )

    async def _record_active_parse_runs(self, row, bindings, result):
        revision = await self._pool.fetchrow(
            "SELECT id,content_hash FROM raw_message_revisions "
            "WHERE raw_id=$1 ORDER BY revision DESC LIMIT 1", row["raw_id"],
        )
        if revision is None:
            return
        trace = result.get("trace") or []
        matched = {int(item["rule_id"]): item for item in trace
                   if item.get("matched") and item.get("rule_id") is not None}
        if not matched:
            matched = {int(item["rule_id"]): item for item in trace if item.get("rule_id") is not None}
        safe_trace = [{key: item.get(key) for key in (
            "rule_id", "rule_version_id", "rule_type", "priority", "supported", "matched",
            "reason", "evidence_source"
        ) if key in item} for item in trace]
        safe_result = {
            "reason": result.get("reason"),
            "selected_rule_ids": (result.get("selected") or {}).get("rule_ids") or [],
            "identity_present": bool((result.get("selected") or {}).get("identity")),
            "field_keys": sorted(((result.get("selected") or {}).get("fields") or {}).keys()),
            "conflicts": [{"kind": item.get("kind"), "field": item.get("field")}
                          for item in result.get("conflicts", [])],
        }
        binding_by_id = {int(item["rule_id"]): item for item in bindings if item.get("rule_id") is not None}
        for rule_id in sorted(matched):
            binding = binding_by_id.get(rule_id, {})
            version_id = binding.get("rule_version_id")
            digest = hashlib.sha256(
                f"active:{revision['id']}:{rule_id}:{version_id}:{revision['content_hash']}".encode()
            ).hexdigest()
            config = binding.get("config_json") or {}
            if isinstance(config, str):
                config = _decode_jsonb(config, {})
            parser_key = config.get("parser_key", "ast_rules_v1") if isinstance(config, dict) else "ast_rules_v1"
            await self._pool.execute(
                SQL_ACTIVE_PARSE_RUN, revision["id"], "listing", rule_id, version_id, parser_key,
                digest, json.dumps({"trace": safe_trace}, ensure_ascii=False),
                json.dumps(safe_result, ensure_ascii=False),
            )

    async def _record_shadow_if_enabled(self, row, legacy_config: dict) -> None:
        """Persist a read-only prediction; never let shadow block legacy extraction."""
        try:
            context = await self._pool.fetchrow(
                "SELECT s.router_mode, rr.id AS revision_id, rr.content_hash FROM ingest_sources s "
                "LEFT JOIN LATERAL (SELECT id,content_hash FROM raw_message_revisions "
                "WHERE raw_id=$2 ORDER BY revision DESC LIMIT 1) rr ON TRUE "
                "WHERE s.id=$1",
                row["source_id"], row["raw_id"],
            )
            if not context or context["router_mode"] != "shadow":
                return
            binding_rows = await self._pool.fetch(SQL_SHADOW_BINDINGS, row["source_id"])
            bindings = [dict(item) for item in binding_rows]
            shadow_started = time.perf_counter()
            result = shadow_compare(
                bindings, row["raw_text"] or "", row["sender_peer_id"], legacy_config,
            )
            duration_ms = round((time.perf_counter() - shadow_started) * 1000, 3)
            conflicts = any(item.get("conflicts") for item in result["shadow_predictions"])
            if not result["changed"] and not conflicts and not _shadow_sampled(
                self._shadow_sample_rate, row["source_id"], row["raw_id"], context["content_hash"],
            ):
                return
            signature_input = json.dumps(
                [(b.get("rule_id"), b.get("rule_version_id"), b.get("purpose"),
                  b.get("match_group"), b.get("mode")) for b in bindings],
                sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
            signature = hashlib.sha256(signature_input).hexdigest()
            actual = result.get("actual_legacy")
            safe_actual, safe_prediction = _shadow_safe_comparison(result)
            safe_prediction["duration_ms"] = duration_ms
            actual_json = _bounded_shadow_json(safe_actual, self._shadow_trace_max_bytes)
            predicted_json = _bounded_shadow_json(safe_prediction, self._shadow_trace_max_bytes)
            await self._pool.fetch(
                SQL_SHADOW_RECORD,
                row["source_id"], row["raw_id"], context["content_hash"] or "unknown",
                signature,
                actual_json, predicted_json, result["changed"],
            )
            for prediction in result["shadow_predictions"]:
                if context["revision_id"] is None:
                    continue
                selected = prediction.get("selected") or {}
                rule_id = selected.get("rule_id")
                rule_version_id = selected.get("rule_version_id")
                if not rule_id and selected.get("rule_ids"):
                    rule_id = selected["rule_ids"][0]
                if not rule_version_id and rule_id:
                    matched = next((b for b in bindings if b.get("rule_id") == rule_id), None)
                    rule_version_id = matched.get("rule_version_id") if matched else None
                purpose_result = _shadow_safe_prediction(prediction, result.get("actual_legacy"))
                purpose_result["duration_ms"] = duration_ms
                idempotency = hashlib.sha256(
                    f"shadow:{context['revision_id']}:{prediction['purpose']}:{rule_version_id}:{signature}".encode()
                ).hexdigest()
                await self._pool.execute(
                    SQL_SHADOW_PARSE_RUN, context["revision_id"], prediction["purpose"],
                    rule_id, rule_version_id, "ast_rules_v1", idempotency,
                    _bounded_shadow_json({"trace": _shadow_safe_trace(prediction.get("trace", []))},
                                         self._shadow_trace_max_bytes),
                    _bounded_shadow_json(purpose_result, self._shadow_trace_max_bytes),
                )
        except Exception as exc:
            error_type = type(exc).__name__
            try:
                await self._pool.execute(
                    "INSERT INTO rule_shadow_error_counts(source_id,error_class,window_started_at,"
                    "occurrences,last_occurred_at,last_raw_id) VALUES($1,$2,date_trunc('hour',NOW()),1,NOW(),$3) "
                    "ON CONFLICT(source_id,error_class,window_started_at) DO UPDATE SET "
                    "occurrences=rule_shadow_error_counts.occurrences+1,last_occurred_at=NOW(),last_raw_id=EXCLUDED.last_raw_id",
                    row["source_id"], error_type, row["raw_id"],
                )
            except Exception:
                pass
            key = (row["source_id"], error_type)
            now = time.monotonic()
            if len(_SHADOW_LOGGED_AT) > 4096:
                cutoff = now - max(self._shadow_error_log_interval_s * 4, 300)
                for logged_key, timestamp in tuple(_SHADOW_LOGGED_AT.items()):
                    if timestamp < cutoff:
                        _SHADOW_LOGGED_AT.pop(logged_key, None)
            if now - _SHADOW_LOGGED_AT.get(key, 0) >= self._shadow_error_log_interval_s:
                _SHADOW_LOGGED_AT[key] = now
                logger.warning("shadow processing failed (source=%s raw=%s error=%s); continuing legacy path",
                               row["source_id"], row["raw_id"], error_type)

    async def _mark(self, raw_id, state: str) -> None:
        await self._pool.fetch(SQL_MARK, raw_id, state)


async def _sleep(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass
