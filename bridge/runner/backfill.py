"""History backfill: pull a bound source's recent messages (text AND
media) into the pipeline, or run a media-only pass that attaches
photos/videos to already-stored messages.

Cursor pagination: a history job carries offset_id — 0 pulls the
newest page (increment sync), -1 resolves to the source's local
min_msg_id at execution time so repeated deep jobs walk further back,
and any positive value is used as an explicit Telethon offset_id.
The per-source watermark (min/max msg id + local count) comes from
source_watermark.sql. ingest_append_sample's ON CONFLICT DO NOTHING
keeps the walk idempotent: locally-known messages are skipped in
microseconds, unknown ones land and enter the extraction pipeline.

Media policy: photos always; videos only up to
NEXUS_BRIDGE_MEDIA_MAX_MB (default 50) to protect NAS disk.
After a media pass, cards still missing a price are requeued so the
OCR pass can read it off the new images.
"""
import asyncio
import json
import logging
import os

import asyncpg

from runner import floodgate, hub, sqlres

logger = logging.getLogger("nexus-bridge.backfill")

SQL_CLAIM = sqlres.load("backfill_claim.sql")
SQL_FINISH = sqlres.load("backfill_finish.sql")
SQL_APPEND = sqlres.load("append_sample.sql")
SQL_SESSION_OF = sqlres.load("session_file_of.sql")
SQL_SET_MEDIA = sqlres.load("set_media.sql")
SQL_RESET_PRICE = sqlres.load("reset_missing_price.sql")
SQL_RAW_IDS = sqlres.load("raw_ids_for_source.sql")
SQL_WATERMARK = sqlres.load("source_watermark.sql")
SQL_LIMIT_SET = sqlres.load("gateway_limit_set.sql")

# unified proactive floodgate: history/resolve/media buckets + the
# shared account cooldown breaker (a FloodWait tripped by the sender
# also pauses backfill for that account, and vice versa)
GATE = floodgate.GATE


class BackfillCooling(Exception):
    """Raised when an official FloodWait tripped the account breaker;
    carries the effective cooldown seconds."""

    def __init__(self, wait_s: float):
        super().__init__(f"cooling {wait_s}s")
        self.wait_s = wait_s


async def _pace(account_id, category: str) -> None:
    """Await the floodgate for this account/category, then record the
    call timestamp so the next delay is computed against it."""
    if account_id is None:
        return
    while (delay := GATE.delay_for(category, account_id)) > 0:
        logger.debug(
            "floodgate %s: waiting %.2fs before next call (account=%s)",
            category, delay, account_id,
        )
        await asyncio.sleep(delay)
    GATE.record(category, account_id)


POLL_S = float(os.getenv("NEXUS_BRIDGE_BACKFILL_INTERVAL", "3"))
MEDIA_DIR = os.getenv("NEXUS_MEDIA_DIR", "/app/data/media")
MAX_MEDIA_BYTES = int(os.getenv("NEXUS_BRIDGE_MEDIA_MAX_MB", "50")) * 1024 * 1024


async def save_media(
    msg, source_id: int, stable_prefix: str | None = None,
    force_kind: str | None = None, account_id=None,
) -> list:
    """Download photo/video for one message; returns media_json items.
    Downloads pass the `media` floodgate bucket so a big album cannot
    saturate NAS disk/bandwidth in one burst."""
    try:
        is_photo = bool(msg.photo)
        is_video = bool(msg.video or msg.video_note)
        if force_kind == "photo":
            is_photo, is_video = True, False
        elif force_kind == "video":
            is_photo, is_video = False, True
        if not (is_photo or is_video):
            return []
        if not is_photo:
            size = getattr(msg.file, "size", 0) or 0
            if size > MAX_MEDIA_BYTES:
                logger.info(
                    "skip video msg %s (%.0f MB over cap)",
                    msg.id, size / 1048576,
                )
                return []
        target_dir = os.path.join(MEDIA_DIR, f"src_{source_id}")
        os.makedirs(target_dir, exist_ok=True)
        target = target_dir
        if stable_prefix:
            media_obj = (
                (msg.video or msg.video_note or msg.document)
                if force_kind == "video"
                else (msg.photo or msg.video or msg.video_note or msg.document)
            )
            media_id = getattr(media_obj, "id", None) or msg.id
            ext = getattr(msg.file, "ext", None) or (".mp4" if is_video else ".jpg")
            target = os.path.join(target_dir, f"{stable_prefix}_{media_id}{ext}")
        if account_id is not None:
            await _pace(account_id, "media")
        saved = await msg.download_media(file=target)
        if not saved:
            return []
        rel = os.path.relpath(str(saved), MEDIA_DIR)
        from runner import media_meta

        # face focus + score (photos), poster frame (videos) — inline,
        # best-effort; ordering happens inside enrich_entries
        return media_meta.enrich_entries(
            [{"kind": "photo" if is_photo else "video", "path": rel}]
        )
    except Exception as exc:
        if account_id is not None and floodgate.is_flood_wait(exc):
            raise
        logger.warning("media download failed (msg %s): %s", msg.id, exc)
        return []


MEDIA_ORDER = {"photo": 0, "video": 1, "file": 2}


def order_media(media: list) -> list:
    """Public face photos first, then private info cards, videos and files."""
    def sort_key(item):
        if isinstance(item.get("manual_order"), int):
            return (0, item["manual_order"], 0, 0)
        visibility = {"public": 0, "pending": 1, "admin": 2}.get(
            item.get("visibility"), 1,
        )
        try:
            score = float(item.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        return (
            1, MEDIA_ORDER.get(item.get("kind"), 3), visibility,
            -score if item.get("kind") == "photo" else 0,
        )

    return sorted(
        media or [], key=sort_key,
    )


async def _fetch_history(client, peer_id: int, limit: int, source_id: int,
                         offset_id: int = 0, account_id=None):
    """[(msg_id, sender, grouped, text, date, media_json)] oldest first,
    walking BACKWARD from offset_id (Telethon semantics: offset_id is
    the exclusive upper bound — messages older than it are returned).
    Both the entity resolve and every page fetch pass the floodgate
    (`resolve` / `history` buckets) so deep walks stay smooth. Album
    parts sharing a grouped_id are MERGED into one row anchored on the
    caption-bearing part, carrying all photos + videos."""
    await _pace(account_id, "resolve")
    entity = await client.get_entity(peer_id)
    await _pace(account_id, "history")
    messages = list(reversed(
        (await client.get_messages(entity, limit=limit, offset_id=offset_id)) or []
    ))
    rows = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        gid = getattr(msg, "grouped_id", None)
        if not gid:
            media = await save_media(msg, source_id, account_id=account_id)
            text = (msg.raw_text or "").strip()
            if text or media:
                rows.append((msg.id, msg.sender_id, None, text, msg.date, order_media(media)))
            i += 1
            continue
        group = []
        while i < len(messages) and getattr(messages[i], "grouped_id", None) == gid:
            group.append(messages[i])
            i += 1
        anchor = next((m for m in group if (m.raw_text or "").strip()), group[0])
        media = []
        for part in group:
            media.extend(await save_media(part, source_id, account_id=account_id))
        text = (anchor.raw_text or "").strip()
        if text or media:
            rows.append(
                (anchor.id, anchor.sender_id, gid, text, anchor.date, order_media(media)),
            )
    return rows


async def _resolve_offset(pool, job) -> int:
    """Cursor for a history job: explicit positive offset wins; -1
    means "walk backward from the source's local watermark" (deep
    mode); 0 pulls the newest page (increment sync)."""
    offset = int(job.get("offset_id") or 0)
    if offset > 0:
        return offset
    if offset == -1:
        mark = await pool.fetch(SQL_WATERMARK, job["source_id"])
        return int(mark[0]["min_msg_id"] or 0)
    return 0


async def _backfill_history(pool, job, client) -> int:
    offset = await _resolve_offset(pool, job)
    try:
        history = await _fetch_history(
            client, job["peer_id"], job["msg_limit"], job["source_id"], offset,
            account_id=job.get("account_id"),
        )
    except Exception as exc:
        if floodgate.is_flood_wait(exc):
            wait_s = await _cool_on_flood_wait(pool, job, exc)
            raise BackfillCooling(wait_s) from exc
        raise
    inserted = 0
    for msg_id, sender, grouped, text, date, media in history:
        rows = await pool.fetch(
            SQL_APPEND, job["source_id"], msg_id, sender, grouped,
            text, date, json.dumps(media),
        )
        if rows[0]["raw_id"] is not None:
            inserted += 1
    logger.info(
        "history walk source=%s cursor=%s limit=%s: %d new / %d seen",
        job["source_id"], offset or "newest", job["msg_limit"],
        inserted, len(history),
    )
    return inserted


async def _cool_on_flood_wait(pool, job, exc) -> float:
    """Official FloodWait during a backfill call: trip the shared
    account breaker (sender pauses too), persist the cooling flag via
    gateway_limit_set, and report back — the job itself ends as
    `failed` with a cooling note, so nothing retries blindly while the
    ban window is live."""
    account_id = job.get("account_id")
    wait_s = GATE.cooldown.cool(account_id, floodgate.flood_wait_seconds(exc))
    try:
        await pool.fetch(SQL_LIMIT_SET, account_id, "cooldown")
    except Exception:
        logger.exception("failed to persist cooldown flag (account=%s)", account_id)
    logger.warning(
        "backfill job %s -> cooling (account=%s waits %ss after FloodWait)",
        job["job_id"], account_id, wait_s,
    )
    return wait_s


async def _backfill_media(pool, job, client) -> int:
    """Attach media to already-stored messages of the source."""
    stored = await pool.fetch(SQL_RAW_IDS, job["source_id"], job["msg_limit"])
    if not stored:
        return 0
    try:
        await _pace(job.get("account_id"), "resolve")
        entity = await client.get_entity(job["peer_id"])
        await _pace(job.get("account_id"), "history")
        ids = [r["platform_msg_id"] for r in stored]
        messages = await client.get_messages(entity, ids=ids)
        updated = 0
        for row, msg in zip(stored, messages):
            if msg is None:
                continue
            media = await save_media(msg, job["source_id"], account_id=job.get("account_id"))
            if not media:
                continue
            result = await pool.fetch(SQL_SET_MEDIA, row["id"], json.dumps(media))
            if result[0]["updated"]:
                updated += 1
    except Exception as exc:
        if floodgate.is_flood_wait(exc):
            wait_s = await _cool_on_flood_wait(pool, job, exc)
            raise BackfillCooling(wait_s) from exc
        raise
    requeued = await pool.fetch(SQL_RESET_PRICE, job["source_id"])
    logger.info(
        "media pass: %d message(s) gained media, %d requeued for OCR (source=%s)",
        updated, requeued[0]["requeued"], job["source_id"],
    )
    return updated


async def run_backfill(pool: asyncpg.Pool, stop: asyncio.Event) -> None:
    logger.info("backfill executor started")
    while not stop.is_set():
        try:
            jobs = await pool.fetch(SQL_CLAIM)
            for job in jobs:
                session_rows = await pool.fetch(SQL_SESSION_OF, job["account_id"])
                if not session_rows:
                    await pool.fetch(SQL_FINISH, job["job_id"], False, 0, "account missing")
                    continue
                session_name = str(session_rows[0]["session_file"]).removesuffix(".session")
                client = await hub.get_client(session_name)
                if client is None:
                    await pool.fetch(SQL_FINISH, job["job_id"], False, 0, "account unauthorized")
                    continue
                worker = _backfill_media if job["job_mode"] == "media" else _backfill_history
                count = 0
                try:
                    count = await worker(pool, job, client)
                    await pool.fetch(SQL_FINISH, job["job_id"], True, count, None)
                    logger.info(
                        "backfill job %s (%s) done: %d item(s) source=%s",
                        job["job_id"], job["job_mode"], count, job["source_id"],
                    )
                except BackfillCooling as exc:
                    # official FloodWait: the account breaker is already
                    # tripped and gateway_limits carries the cooling flag;
                    # the job parks as failed instead of retrying into a
                    # heavier ban — re-run it manually after the cooldown
                    await pool.fetch(
                        SQL_FINISH, job["job_id"], False, 0,
                        f"{exc} (official FloodWait; account tripped)",
                    )
                    logger.warning("backfill job %s parked: %s", job["job_id"], exc)
                except Exception as exc:
                    await pool.fetch(SQL_FINISH, job["job_id"], False, count, str(exc))
                    logger.warning("backfill job %s failed: %s", job["job_id"], exc)
        except Exception:
            logger.exception("backfill cycle failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_S)
        except TimeoutError:
            pass
