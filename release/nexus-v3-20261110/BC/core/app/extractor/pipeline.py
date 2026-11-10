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
from app.extractor import ast_rules
from app.extractor.lifecycle_identity import explicit_lifecycle_username
from app.extractor.ocr_worker import OcrWorker
from app.extractor.rule_router import shadow_compare

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
SQL_SHADOW_BINDINGS = sqlres.load("rule_shadow_bindings.sql")
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
        "identity_diff": bool((actual or {}).get("identity") != selected.get("identity", "")),
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
            if not rows:
                await _sleep(stop, idle_s)
                continue
            for row in rows:
                await self._process_one(row)

    async def _process_one(self, row) -> None:
        raw_id = row["raw_id"]
        text = row["raw_text"] or ""
        rule = _decode_jsonb(row["rule_config"], None)
        if rule is None:
            rule = FALLBACK_RULE
        media = _decode_jsonb(row["media_json"], [])
        await self._record_shadow_if_enabled(row, rule)
        try:
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

            fields, evidence = ast_rules.extract_fields(rule, text)
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
