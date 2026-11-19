"""Extraction pipeline loop: claim → gate → regex → OCR cross-fill →
lifecycle.

Runs as a background task inside nexus-core. Batches of raw messages
are claimed row-locked from the database, then processed purely
in-process against the bound rule config; every write goes back
through database functions with bound parameters.

When regex extraction misses core fields (price, district, nickname,
username, body data), the pipeline cross-fills from OCR: first from
the structured card fields the media OCR worker already stored in
listing_media_ocr, then — if the rule enables it and nothing is stored
yet — by OCR-ing the photos inline. Filled values keep their OCR
confidence and line-level provenance in evidence_json, and OCR-sourced
listings stay capped below the auto-publish threshold for review.
"""
import asyncio
import json
import logging
import os
import re
import time

import asyncpg

from app import llm, sqlres
from app.extractor import ast_rules, media_ocr, ocr_fields, profile_extract
from app.extractor.ocr_worker import OcrWorker

logger = logging.getLogger("nexus-core.extractor")

SQL_CLAIM = sqlres.load("claim_batch.sql")
SQL_MARK = sqlres.load("extract_mark.sql")
SQL_CREATE_LISTING = sqlres.load("upsert_newest.sql")
SQL_ARCHIVE = sqlres.load("archive_for_contact.sql")
SQL_POLICY = sqlres.load("settings_get_num.sql")
SQL_POLICY_ENABLED = sqlres.load("settings_get.sql")
SQL_MEDIA_FLAGS = sqlres.load("media_ocr_set_flags.sql")
SQL_LISTING_SYNC_MEDIA = sqlres.load("listing_sync_media.sql")
SQL_HISTORY_SOURCE = sqlres.load("history_scan_source.sql")
SQL_CREATE_HISTORY_LISTING = sqlres.load("upsert_history_scan.sql")

# structured card fields the media OCR worker already stored for a
# message's images, best-confidence first
SQL_OCR_DONE = """
SELECT media_path, classification, confidence, fields_json, evidence_json
FROM listing_media_ocr
WHERE raw_id = $1 AND status = 'done'
  AND COALESCE(human_classification, classification) = 'info_card'
ORDER BY confidence DESC NULLS LAST, id
"""

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


# OCR cross-fill triggers: rule configs may list any of these keys in
# extract.ocr.trigger_when_missing; a config without the key defaults
# to all core fields, so messages missing data are rescued from OCR
# cards instead of becoming pending-review junk.
TRIGGER_KEYS = {
    "price": ("price_amount",),
    "district": ("district",),
    "nickname": ("nickname",),
    "username": ("username",),
    "body": ("height_cm", "weight_kg", "bust_size", "age_years"),
}
DEFAULT_TRIGGERS = ("price", "district", "nickname", "body")


def missing_triggers(fields: dict, triggers) -> list:
    """Trigger names whose mapped fields are all absent from `fields`."""
    missing = []
    for name in triggers:
        keys = TRIGGER_KEYS.get(name, (name,))
        if not any(fields.get(key) for key in keys):
            missing.append(name)
    return missing


async def _load_policy(pool) -> tuple:
    """(auto_publish_enabled, threshold) from app_settings."""
    enabled_row = await pool.fetch(SQL_POLICY_ENABLED, "autopublish_enabled")
    enabled = not enabled_row or (enabled_row[0]["value"] != "0")
    thresh_rows = await pool.fetch(SQL_POLICY, "autopublish_threshold", 0.8)
    return enabled, float(thresh_rows[0]["value"])


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
    def __init__(self, pool: asyncpg.Pool, media_dir: str, ocr_enabled: bool, ocr_worker=None, media_ocr_processor=None):
        self._pool = pool
        self._media_dir = media_dir
        self._ocr_enabled = ocr_enabled
        self._ocr = ocr_worker or OcrWorker()
        self._media_ocr_processor = media_ocr_processor
        # LLM endpoint config cached briefly — profile extraction only
        # runs when a model endpoint is actually configured
        self._llm_cfg: dict | None = None
        self._llm_cfg_at = 0.0

    async def _llm_config(self) -> dict:
        if self._llm_cfg is None or time.monotonic() - self._llm_cfg_at > 60:
            try:
                self._llm_cfg = await llm.load_config(self._pool)
            except Exception:
                logger.exception("llm config load failed; profile pass disabled")
                self._llm_cfg = {}
            self._llm_cfg_at = time.monotonic()
        return self._llm_cfg

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

            term = ast_rules.hit_lifecycle(rule, text)
            if term is not None:
                rows = await self._pool.fetch(
                    SQL_ARCHIVE, row["sender_peer_id"], None,
                )
                archived = rows[0]["archived"]
                await self._mark(raw_id, "dropped")
                logger.info(
                    "raw %s lifecycle '%s' -> archived %d published card(s)",
                    raw_id, term, archived,
                )
                return

            fields, evidence = ast_rules.extract_fields(rule, text)
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
            triggers = ocr_cfg.get("trigger_when_missing") or DEFAULT_TRIGGERS
            ocr_definitions = [row for row in field_definitions if row["extraction_source"] == "ocr"]
            if missing_triggers(fields, triggers):
                for card_fields, card_evidence, confidence in await self._existing_ocr_cards(raw_id):
                    filled, filled_ev = ocr_fields.card_to_listing_fields(card_fields, card_evidence, confidence)
                    fields, evidence = ast_rules.merge_extraction(fields, evidence, filled, filled_ev)
            missing = missing_triggers(fields, triggers)
            missing.extend(row["field_key"] for row in ocr_definitions if not custom_fields.get(row["field_key"]))
            if not text.strip():
                missing.append("description")
            if (
                missing and self._ocr_enabled and ocr_cfg.get("enabled")
                and any(m.get("path") for m in media)
            ):
                # (b) nothing stored yet: OCR the photos inline, run the
                # rule patterns over the raw OCR text, then merge the
                # structured card fields on top
                paths = [
                    os.path.join(self._media_dir, m["path"])
                    for m in media
                    if m.get("path") and str(m.get("kind")) == "photo"
                ]
                ocr_texts = []
                cards = []
                if paths and self._media_ocr_processor is not None:
                    ocr_texts, pending = await self._media_ocr_processor.read_raw_texts(raw_id, [
                        item["path"] for item in media if item.get("kind") == "photo" and item.get("path")
                    ])
                    if pending:
                        await self._mark(raw_id, "new")
                        return
                    cards = await self._existing_ocr_cards(raw_id)
                    refreshed_media = await self._pool.fetchval(
                        "SELECT media_json FROM raw_messages WHERE id=$1", raw_id,
                    )
                    media = _decode_jsonb(refreshed_media, media)
                else:
                    for path in paths:
                        document = await self._ocr.read_document(path)
                        if not document.get("ok"):
                            continue
                        ocr_texts.extend(document.get("texts") or [])
                        if document.get("lines"):
                            card_fields, card_evidence = media_ocr.extract_card_fields(document["lines"])
                            cards.append((card_fields, card_evidence, None))

                if ocr_texts:
                    custom_fields.update(extract_custom_fields("\n".join(ocr_texts), ocr_definitions))
                    rule_fields, rule_evidence = ast_rules.extract_fields(rule, " ".join(ocr_texts), src="ocr")
                    fields, evidence = ast_rules.merge_extraction(fields, evidence, rule_fields, rule_evidence)
                for card_fields, card_evidence, confidence in cards:
                    filled, filled_ev = ocr_fields.card_to_listing_fields(card_fields, card_evidence, confidence)
                    fields, evidence = ast_rules.merge_extraction(fields, evidence, filled, filled_ev)

            # (c) channel profile: a learned per-source extraction
            # profile + configured model endpoint lets the LLM parse
            # the message straight into the standard schema — it fills
            # whatever regex/OCR still missed and contributes the
            # structured tariffs/services/restrictions extras; any
            # model failure degrades silently to the deterministic result
            profile = _decode_jsonb(row.get("extraction_profile"), None)
            if profile_extract.profile_ready(profile, await self._llm_config()):
                needs_fill = bool(missing_triggers(fields, triggers))
                has_extras = any(
                    key in evidence for key in (
                        "service_text", "restriction_text",
                        "price_1p_text", "price_text",
                    )
                )
                if needs_fill or not has_extras:
                    cfg = await self._llm_config()
                    parsed = await profile_extract.extract_with_profile(
                        lambda messages, timeout=60: llm.chat(cfg, messages, timeout),
                        text, profile,
                    )
                    if parsed:
                        fields, evidence, filled = profile_extract.merge_profile_result(
                            fields, evidence, parsed,
                        )
                        if filled:
                            logger.info(
                                "raw %s profile-llm filled %s",
                                raw_id, ",".join(sorted(set(filled))),
                            )

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

    async def _existing_ocr_cards(self, raw_id) -> list:
        """Structured card fields already produced by the media OCR
        worker for this message's images, best-confidence first.
        Returns (card_fields, card_evidence, card_confidence) tuples."""
        try:
            rows = await self._pool.fetch(SQL_OCR_DONE, raw_id)
        except Exception:
            # a missing/older schema must not stall the whole loop;
            # cross-fill just falls back to the inline OCR path
            logger.exception("raw %s failed to load stored OCR cards", raw_id)
            return []
        cards = []
        for row in rows:
            card_fields = _decode_jsonb(row["fields_json"], {}) or {}
            card_evidence = _decode_jsonb(row["evidence_json"], {}) or {}
            if not card_fields:
                continue
            cards.append((card_fields, card_evidence, float(row["confidence"] or 0.0)))
        return cards

    async def _mark(self, raw_id, state: str) -> None:
        await self._pool.fetch(SQL_MARK, raw_id, state)


async def _sleep(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass
