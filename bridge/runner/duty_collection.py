"""Collect only messages linked from a matched daily on-duty digest."""
import asyncio
import json
import logging

from runner import backfill, hub, sqlres
from runner.duty import listing_contact_username, profile_usernames

logger = logging.getLogger("nexus-bridge.duty-collection")

SQL_CLAIM = sqlres.load("duty_collection_claim.sql")
SQL_EXISTING = sqlres.load("duty_collection_existing.sql")
SQL_APPEND = sqlres.load("append_sample.sql")
SQL_FINISH = sqlres.load("duty_collection_finish.sql")
POLL_S = 3.0


def _matches_username(message, username: str) -> bool:
    wanted = username.lower().lstrip("@")
    if listing_contact_username(message) == wanted:
        return True
    return wanted in profile_usernames(message)


async def _find_message(client, job):
    entity = await client.get_entity(job["source_peer_id"])
    if job["target_msg_id"] is not None:
        message = await client.get_messages(entity, ids=job["target_msg_id"])
        if message is None:
            return entity, None
        return entity, message

    async for message in client.iter_messages(
        entity, search=job["contact_username"], limit=20,
    ):
        if _matches_username(message, job["contact_username"]):
            return entity, message
    return entity, None


async def _fetch_album(client, entity, message):
    if not message.grouped_id:
        return [message]
    nearby = await client.get_messages(
        entity, min_id=message.id - 10, max_id=message.id + 11, limit=21,
    )
    return sorted(
        (part for part in nearby if part and part.grouped_id == message.grouped_id),
        key=lambda part: part.id,
    ) or [message]


async def _collect(pool, client, job) -> tuple[str, int | None, str | None]:
    entity, message = await _find_message(client, job)
    if message is None:
        return "not_found", None, "no exact linked message or username match in the target source"
    if not _matches_username(message, job["contact_username"]):
        return "not_found", None, "Telegram message did not match the exact username"

    parts = await _fetch_album(client, entity, message)
    anchor = next((part for part in parts if (part.raw_text or "").strip()), parts[0])
    existing = await pool.fetchrow(
        SQL_EXISTING, job["source_id"], anchor.id, anchor.grouped_id,
    )
    if existing:
        return "skipped", existing["id"], "source message already exists"

    media = []
    expected_media = 0
    for part in parts:
        has_media = bool(part.photo or part.video or part.video_note)
        size = getattr(part.file, "size", 0) or 0 if has_media and part.file else 0
        if part.photo or (has_media and size <= backfill.MAX_MEDIA_BYTES):
            expected_media += 1
        media.extend(await backfill.save_media(
            part, job["source_id"], stable_prefix=f"duty_{part.id}",
        ))
    if len(media) < expected_media:
        raise RuntimeError("linked message media could not be fully downloaded")

    raw_text = (anchor.raw_text or "").strip()
    if not raw_text and not media:
        raise RuntimeError("linked message has no extractable text or media")
    rows = await pool.fetch(
        SQL_APPEND, job["source_id"], anchor.id, anchor.sender_id,
        anchor.grouped_id, raw_text, anchor.date,
        json.dumps(backfill.order_media(media)),
    )
    raw_id = rows[0]["raw_id"]
    if raw_id is None:
        return "skipped", None, "source message was already collected"
    return "fetched", raw_id, None


async def run_duty_collection(pool, stop) -> None:
    logger.info("on-duty linked-message collection worker started")
    while not stop.is_set():
        try:
            jobs = await pool.fetch(SQL_CLAIM)
            for job in jobs:
                try:
                    if not job["session_file"]:
                        raise RuntimeError("assigned Telegram account is unavailable")
                    session_name = str(job["session_file"]).removesuffix(".session")
                    client = await hub.get_client(session_name)
                    if client is None:
                        raise RuntimeError("Telegram account is unauthorized")
                    status, raw_id, error = await _collect(pool, client, job)
                    await pool.fetch(SQL_FINISH, job["job_id"], status, raw_id, error)
                    logger.info(
                        "on-duty linked-message job %s: job=%s username=%s source=%s",
                        status, job["job_id"], job["contact_username"], job["source_id"],
                    )
                except Exception as exc:
                    await pool.fetch(SQL_FINISH, job["job_id"], "failed", None, str(exc)[:1000])
                    logger.warning(
                        "on-duty linked-message job failed (job=%s): %s",
                        job["job_id"], exc,
                    )
        except Exception:
            logger.exception("on-duty linked-message polling failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_S)
        except TimeoutError:
            pass
