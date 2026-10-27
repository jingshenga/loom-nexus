"""Refresh selected listing source messages from Telegram on demand."""
import asyncio
import json
import logging

from runner import hub, sqlres
from runner.backfill import order_media, save_media

logger = logging.getLogger("nexus-bridge.recollect")

SQL_CLAIM = sqlres.load("recollect_claim.sql")
SQL_SAVE = sqlres.load("recollect_save_message.sql")
SQL_FINISH = sqlres.load("recollect_finish.sql")
POLL_S = 3.0


async def _fetch_job(pool, job) -> None:
    if job["current_listing_id"] is None or job["platform_msg_id"] is None:
        raise RuntimeError("card or source message was removed")
    if job["peer_id"] is None or job["session_file"] is None:
        raise RuntimeError("Telegram source account is unavailable")
    session_name = str(job["session_file"]).removesuffix(".session")
    client = await hub.get_client(session_name)
    if client is None:
        raise RuntimeError("Telegram account is unauthorized")

    entity = await client.get_entity(job["peer_id"])
    message = await client.get_messages(entity, ids=job["platform_msg_id"])
    if message is None:
        raise RuntimeError("Telegram message no longer exists or is inaccessible")

    parts = [message]
    if message.grouped_id:
        nearby = await client.get_messages(
            entity, min_id=message.id - 10, max_id=message.id + 11, limit=21,
        )
        parts = sorted(
            (part for part in nearby if part and part.grouped_id == message.grouped_id),
            key=lambda part: part.id,
        ) or parts

    media = []
    for part in parts:
        media.extend(await save_media(
            part, job["raw_id"], stable_prefix=f"tg_{part.id}",
        ))
    expected_media = sum(
        bool(part.photo or part.video or part.video_note) for part in parts
    )
    if expected_media and len(media) < expected_media:
        raise RuntimeError("Telegram media could not be fully downloaded")
    text_message = next((part for part in parts if part.raw_text), message)
    rows = await pool.fetch(
        SQL_SAVE, job["raw_id"], text_message.raw_text or "",
        text_message.date, json.dumps(order_media(media)) if media else None,
        job["listing_id"],
    )
    if not rows:
        raise RuntimeError("card was removed before refreshed content could be applied")


async def run_recollect(pool, stop) -> None:
    logger.info("selected-message recollection worker started")
    while not stop.is_set():
        try:
            jobs = await pool.fetch(SQL_CLAIM)
            for job in jobs:
                try:
                    await _fetch_job(pool, job)
                    await pool.fetch(SQL_FINISH, job["job_id"], "done", None)
                    logger.info(
                        "Telegram recollection completed: job=%s listing=%s msg=%s",
                        job["job_id"], job["listing_id"], job["platform_msg_id"],
                    )
                except Exception as exc:
                    await pool.fetch(SQL_FINISH, job["job_id"], "failed", str(exc)[:1000])
                    logger.warning("Telegram recollection failed (job=%s): %s", job["job_id"], exc)
        except Exception:
            logger.exception("Telegram recollection polling failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_S)
        except TimeoutError:
            pass
