"""Resumable, low-rate history backfill for the two check-in sources."""
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone

from runner import backfill, hub, sqlres
from runner.duty import listing_contact_username, profile_usernames
from runner.duty_collection import (
    _fetch_album, _is_flood_wait, _flood_wait_seconds, _is_obvious_promo,
    _profile_media_kind,
)

logger = logging.getLogger("nexus-bridge.checkin-history")

SQL_CLAIM = sqlres.load("checkin_history_claim.sql")
SQL_EXISTING = sqlres.load("checkin_history_existing.sql")
SQL_PROGRESS = sqlres.load("checkin_history_progress.sql")
SQL_DEFER = sqlres.load("checkin_history_defer.sql")
SQL_FAIL = sqlres.load("checkin_history_fail.sql")
SQL_RETRY = sqlres.load("checkin_history_retry.sql")
SQL_RECONCILE = sqlres.load("checkin_history_reconcile.sql")
SQL_APPEND = sqlres.load("append_sample.sql")
PAGE_SIZE = max(20, min(100, int(os.getenv("NEXUS_CHECKIN_HISTORY_PAGE_SIZE", "100"))))
PAGE_INTERVAL_SECONDS = max(5, int(os.getenv("NEXUS_CHECKIN_HISTORY_PAGE_INTERVAL_SECONDS", "15")))


def _decode(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}


def _rule_candidate(message, rule):
    text = (message.raw_text or "").strip()
    if not text:
        return bool(
            (getattr(message, "photo", None) or getattr(message, "video", None))
            and (profile_usernames(message) or listing_contact_username(message))
        )
    if _is_obvious_promo(text):
        return False
    config = _decode(rule)
    gate = config.get("gate") or {}
    sender = getattr(message, "sender_id", None)
    if sender is not None and int(sender) in {int(value) for value in gate.get("blocked_sender_peer_ids", [])}:
        return False
    keywords = [str(value) for value in gate.get("require_any_keywords") or [] if value]
    keyword_match = not keywords or any(keyword in text for keyword in keywords)
    if not keyword_match and not profile_usernames(message) and not listing_contact_username(message):
        return False
    if any(term and term in text for term in config.get("lifecycle_terms") or []):
        return False
    return True


async def _collect_page(pool, client, scan):
    entity = await client.get_entity(scan["peer_id"])
    messages = [message async for message in client.iter_messages(
        entity, limit=PAGE_SIZE, offset_id=int(scan["cursor_msg_id"] or 0), wait_time=1,
    )]
    if not messages:
        return 0, 0, 0, int(scan["cursor_msg_id"] or 0), None, None, False

    known_ids = {int(row["platform_msg_id"]) for row in await pool.fetch(
        SQL_EXISTING, scan["source_id"], [int(message.id) for message in messages],
    )}
    stored = candidates = 0
    groups = []
    albums = {}
    for message in messages:
        grouped_id = getattr(message, "grouped_id", None)
        if grouped_id is None:
            groups.append([message])
        else:
            albums.setdefault(grouped_id, []).append(message)
    groups.extend(albums.values())
    for group in groups:
        group.sort(key=lambda message: int(message.id))
        message = next((part for part in group if (part.raw_text or "").strip()), group[0])
        if not _rule_candidate(message, scan["listing_config"]):
            continue

        parts = group if len(group) > 1 else (
            await _fetch_album(client, entity, message) if message.grouped_id else [message]
        )
        anchor = next((part for part in parts if (part.raw_text or "").strip()), message)
        if int(anchor.id) in known_ids:
            continue
        candidates += 1
        media = []
        photos = 0
        videos = []
        for part in parts:
            kind = _profile_media_kind(part)
            if kind == "photo" and photos < 6:
                saved = await backfill.save_media(
                    part, scan["source_id"],
                    stable_prefix=f"history_{scan['source_id']}_{part.id}",
                    force_kind="photo",
                )
                media.extend(saved)
                photos += sum(item.get("kind") == "photo" for item in saved)
            elif kind == "video":
                videos.append(part)
        if photos == 0:
            for part in videos[:2]:
                media.extend(await backfill.save_media(
                    part, scan["source_id"],
                    stable_prefix=f"history_{scan['source_id']}_{part.id}",
                    force_kind="video",
                ))
        text = (anchor.raw_text or "").strip()
        usernames = profile_usernames(anchor)
        username = listing_contact_username(anchor) or (usernames[0] if len(usernames) == 1 else None)
        if username and username.lower() not in text.lower():
            text = f"{text}\nTG：@{username}".strip()
        rows = await pool.fetch(
            SQL_APPEND, scan["source_id"], int(anchor.id), anchor.sender_id,
            anchor.grouped_id, text, anchor.date,
            json.dumps(backfill.order_media(media), ensure_ascii=False),
        )
        if rows and rows[0]["raw_id"] is not None:
            stored += 1
            known_ids.add(int(anchor.id))

    cursor = min(int(message.id) for message in messages)
    has_more = len(messages) == PAGE_SIZE
    return len(messages), stored, candidates, cursor, cursor, datetime.now(timezone.utc), has_more


async def run_history_scan(pool, stop):
    logger.info("check-in source history scanner started (page=%s interval=%ss)", PAGE_SIZE, PAGE_INTERVAL_SECONDS)
    while not stop.is_set():
        try:
            scan = await pool.fetchrow(SQL_CLAIM)
            if scan is None:
                await asyncio.wait_for(stop.wait(), timeout=3)
                continue
            session = str(scan["session_file"]).removesuffix(".session")
            client = await hub.get_client(session)
            if client is None:
                raise RuntimeError("assigned Telegram account is unauthorized")
            try:
                scanned, stored, candidates, cursor, last_id, last_at, has_more = await _collect_page(
                    pool, client, scan,
                )
            except Exception as exc:
                if _is_flood_wait(exc):
                    wait_seconds = _flood_wait_seconds(exc)
                    await pool.execute(SQL_DEFER, scan["source_id"], wait_seconds)
                    logger.warning(
                        "history scan deferred by Telegram cooldown (source=%s wait=%ss)",
                        scan["source_id"], wait_seconds,
                    )
                    continue
                terminal = {
                    "ChannelPrivateError", "ChannelInvalidError", "ChannelPublicGroupNaError",
                    "ChatAdminRequiredError", "UsernameInvalidError", "UsernameNotOccupiedError",
                }
                if type(exc).__name__ in terminal or "unauthorized" in str(exc).lower():
                    await pool.execute(SQL_FAIL, scan["source_id"], str(exc)[:1000])
                    logger.exception("history scan terminal account failure (source=%s)", scan["source_id"])
                else:
                    await pool.execute(SQL_RETRY, scan["source_id"], 300, str(exc)[:1000])
                    logger.exception("history scan will retry from checkpoint (source=%s)", scan["source_id"])
                continue

            if scanned:
                await pool.execute(
                    SQL_PROGRESS, scan["source_id"], cursor, scanned, stored,
                    candidates, last_id, last_at, has_more, PAGE_INTERVAL_SECONDS,
                )
                await pool.execute(SQL_RECONCILE, scan["source_id"])
            else:
                await pool.execute(
                    SQL_PROGRESS, scan["source_id"], cursor, 0, 0, 0,
                    None, None, False, PAGE_INTERVAL_SECONDS,
                )
            logger.info(
                "history scan page complete: source=%s scanned=%s candidates=%s stored=%s cursor=%s more=%s",
                scan["source_id"], scanned, candidates, stored, cursor, has_more,
            )
        except TimeoutError:
            pass
        except Exception:
            logger.exception("history scan polling failed")
