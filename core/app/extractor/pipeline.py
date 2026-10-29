"""Extraction pipeline loop: claim → gate → regex → OCR → lifecycle.

Runs as a background task inside nexus-core. Batches of raw messages
are claimed row-locked from the database, then processed purely
in-process against the bound rule config; every write goes back
through database functions with bound parameters.
"""
import asyncio
import json
import logging
import os

import asyncpg

from app import sqlres
from app.extractor import ast_rules
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


class Extractor:
    def __init__(self, pool: asyncpg.Pool, media_dir: str, ocr_enabled: bool, ocr_worker=None):
        self._pool = pool
        self._media_dir = media_dir
        self._ocr_enabled = ocr_enabled
        self._ocr = ocr_worker or OcrWorker()

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
            ocr_cfg = (rule.get("ocr") or {})
            missing = [
                key for key in (ocr_cfg.get("trigger_when_missing") or [])
                if key == "price" and "price_amount" not in fields
            ]
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
                    ocr_texts = await self._ocr.read_texts(paths)
                    if ocr_texts:
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

            has_structure = any(
                k in fields
                for k in ("nickname", "price_amount", "district", "username")
            )
            if not has_structure:
                await self._mark(raw_id, "dropped")
                logger.info("raw %s dropped (no fields extracted)", raw_id)
                return

            confidence = score_confidence(fields, evidence, len(media))
            ocr_used = any(v.get("src") == "ocr" for v in evidence.values())
            auto_enabled, threshold = await _load_policy(self._pool)
            # below-threshold or OCR-sourced data needs human review;
            # above-threshold regex-clean data publishes directly
            should_publish = (
                auto_enabled and not ocr_used and confidence >= threshold
            )
            if ocr_used:
                confidence = min(confidence, 0.79)  # cap OCR confidence
            rows = await self._pool.fetch(
                SQL_CREATE_LISTING,
                raw_id,
                None,  # sender is the channel bot, NOT the contact; the
                       # contact identity is the extracted username
                fields.get("username"),
                fields.get("nickname"),
                fields.get("district"),
                fields.get("price_amount"),
                fields.get("price_currency") or "CNY",
                text,
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

    async def _mark(self, raw_id, state: str) -> None:
        await self._pool.fetch(SQL_MARK, raw_id, state)


async def _sleep(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass
