"""Contact bio penetration chain executor (type C, manual-gated).

Admin approves a probe in the workbench; this loop then claims it,
joins the candidate room, and merges the pinned message with the
owner's three most recent messages into ONE raw_messages row on a
dedicated probe source, which the extraction pipeline then processes.

SIM mode synthesizes the room content; real mode joins and reads via
the protocol client (active once credentials exist).
"""
import asyncio
import json
import logging
import os
import time

import asyncpg

from runner import sqlres

logger = logging.getLogger("nexus-bridge.probe")

SQL_ENQUEUE = sqlres.load("probe_enqueue.sql")
SQL_CLAIM = sqlres.load("probe_claim.sql")
SQL_MARK = sqlres.load("probe_mark.sql")
SQL_ENSURE_SOURCE = sqlres.load("ensure_source.sql")
SQL_APPEND_RAW = sqlres.load("append_raw.sql")
SQL_ADVANCE = sqlres.load("advance_source.sql")

POLL_S = float(os.getenv("NEXUS_BRIDGE_PROBE_INTERVAL", "5"))

SIM_ROOM_TEXT = (
    "【房间置顶】昵称: 沐月 价格: 432 元 城区: 东城\n"
    "【房主近况】今日有空，可预约\n"
    "【房主近况】价格可小刀，诚心来\n"
    "【房主近况】谢谢关注，见置顶"
)


async def _fetch_room_content_real(links) -> str:
    """Join the first candidate room and merge pinned + owner's last 3."""
    from telethon import TelegramClient

    link = (links or [""])[0].rstrip("/").rsplit("/", 1)[-1]
    if not link:
        raise RuntimeError("no candidate link")
    client = TelegramClient(
        os.path.join(
            os.getenv("NEXUS_BRIDGE_SESSION_DIR", "/app/data/sessions"),
            "presence-checker",
        ),
        int(os.getenv("NEXUS_BRIDGE_API_ID", "0")),
        os.getenv("NEXUS_BRIDGE_API_HASH", ""),
    )
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("probe client unauthorized")
        entity = await client.get_entity(link)
        pinned = await client.get_messages(entity, limit=1)
        recent = await client.get_messages(entity, limit=10)
        lines = []
        if pinned and pinned[0].pinned and pinned[0].raw_text:
            lines.append(f"【房间置顶】{pinned[0].raw_text.strip()}")
        for msg in (recent or [])[:3]:
            if msg.raw_text:
                lines.append(f"【房主近况】{msg.raw_text.strip()}")
        return "\n".join(lines)
    finally:
        await client.disconnect()


async def _fetch_room_content_sim(_links) -> str:
    return SIM_ROOM_TEXT


async def run_probe(pool: asyncpg.Pool, stop: asyncio.Event) -> None:
    sim = os.getenv("NEXUS_BRIDGE_SIM", "0") == "1"
    fetch_room = _fetch_room_content_sim if sim else _fetch_room_content_real
    source_id = None
    logger.info("probe executor started (mode=%s)", "sim" if sim else "real")
    while not stop.is_set():
        try:
            rows = await pool.fetch(SQL_CLAIM)
            if not rows:
                await _sleep(stop, POLL_S)
                continue
            if source_id is None:
                src = await pool.fetch(
                    SQL_ENSURE_SOURCE, 900000002, "sim-room", "sim_room_feed",
                )
                source_id = src[0]["source_id"]
            for row in rows:
                probe_id = row["id"]
                links = row["candidate_links"]
                if isinstance(links, str):
                    links = json.loads(links)
                try:
                    content = await fetch_room(links)
                    raw_rows = await pool.fetch(
                        SQL_APPEND_RAW, source_id, int(time.time() * 1000),
                        row["contact_peer_id"], None, content, "[]", None,
                    )
                    await pool.fetch(
                        SQL_ADVANCE, source_id, int(time.time() * 1000),
                    )
                    await pool.fetch(SQL_MARK, probe_id, "fetched")
                    logger.info(
                        "probe %s fetched room content -> raw id=%s",
                        probe_id, raw_rows[0]["raw_id"],
                    )
                except Exception as exc:
                    await pool.fetch(SQL_MARK, probe_id, "failed")
                    logger.warning("probe %s failed: %s", probe_id, exc)
        except Exception:
            logger.exception("probe cycle failed")
            await _sleep(stop, POLL_S)


async def _sleep(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass
