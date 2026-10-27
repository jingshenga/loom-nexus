"""Source-stream intake with 3-second album debounce aggregation.

Normalized events enter IngestPipeline.accept(); messages sharing a
grouped_id are buffered and flushed as ONE raw_messages row after the
group stays quiet for DEBOUNCE_S seconds (hard-capped HARD_CAP_S after
the first part, so a steady stream cannot slide the window forever).
Standalone messages persist immediately.

Persistence calls the database functions defined in scripts/schema.sql;
statement text is loaded from runner/sql/*.sql and every runtime value
is passed as a bound parameter — no SQL is built in Python.
"""
import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

import asyncpg

from runner import sqlres

logger = logging.getLogger("nexus-bridge.listener")

DEBOUNCE_S = 3.0
HARD_CAP_S = 8.0

MEDIA_ORDER = {"photo": 0, "video": 1, "file": 2}

SQL_ENSURE_SOURCE = sqlres.load("ensure_source.sql")
SQL_APPEND_RAW = sqlres.load("append_raw.sql")
SQL_ADVANCE_SOURCE = sqlres.load("advance_source.sql")
SQL_PRESENCE = sqlres.load("presence_upsert.sql")

# internal message links (type B aggregate messages)
LINK_PATTERN = re.compile(r"https?://t\.me/(?:c/)?([A-Za-z0-9_]+)/(\d+)")


async def expand_embedded_links(text, fetch_target):
    """Type B handling: replace each internal message link in the text
    with the fetched target message text. fetch_target(chat_ref, msg_id)
    returns the target text or None (link kept as-is). Returns
    (expanded_text, fetched_count)."""
    if not text:
        return text, 0
    parts = []
    last = 0
    fetched = 0
    for match in LINK_PATTERN.finditer(text):
        parts.append(text[last:match.start()])
        target_text = await fetch_target(match.group(1), int(match.group(2)))
        if target_text:
            parts.append(f"\n[引用] {target_text.strip()}")
            fetched += 1
        else:
            parts.append(match.group(0))
        last = match.end()
    parts.append(text[last:])
    return "".join(parts), fetched


def order_media(media: list) -> list:
    """Best-scored photos first, then videos, then other files."""
    order = {"photo": 0, "video": 1, "file": 2}
    return sorted(
        media or [],
        key=lambda m: (
            order.get(m.get("kind"), 3),
            -float(m.get("score") or 0) if m.get("kind") == "photo" else 0,
        ),
    )


class _Album:
    """Buffered album group keyed by (source_id, grouped_id)."""

    __slots__ = ("anchor_msg_id", "sender_peer_id", "posted_at", "texts", "media")

    def __init__(self, anchor_msg_id, sender_peer_id, posted_at):
        self.anchor_msg_id = anchor_msg_id
        self.sender_peer_id = sender_peer_id
        self.posted_at = posted_at
        self.texts = []
        self.media = []


class IngestPipeline:
    """Debounce + persist pipeline shared by sim and protocol paths."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool
        self._albums: dict = {}
        self._timers: dict = {}
        self._deadlines: dict = {}
        self.albums_merged = 0
        self.standalone_stored = 0

    async def ensure_source(self, peer_id: int, title: str, username: str | None) -> int:
        rows = await self._pool.fetch(SQL_ENSURE_SOURCE, peer_id, title, username)
        return rows[0]["source_id"]

    async def accept(self, source_id, platform_msg_id, sender_peer_id,
                     grouped_id, text, media, posted_at):
        if grouped_id is None:
            raw_id = await self._persist(
                source_id, platform_msg_id, sender_peer_id, None,
                [text or ""], media or [], posted_at,
            )
            self.standalone_stored += 1
            logger.info("standalone stored -> raw id=%s", raw_id)
            return raw_id

        key = (source_id, grouped_id)
        album = self._albums.get(key)
        now = time.monotonic()
        if album is None:
            album = self._albums[key] = _Album(platform_msg_id, sender_peer_id, posted_at)
            self._deadlines[key] = now + HARD_CAP_S
        if text and text not in album.texts:
            album.texts.append(text)
        if media:
            album.media.extend(media)

        timer = self._timers.get(key)
        if timer is not None:
            timer.cancel()
        quiet_s = DEBOUNCE_S
        remaining = self._deadlines[key] - now
        if remaining < quiet_s:
            quiet_s = max(0.05, remaining)
        self._timers[key] = asyncio.create_task(self._quiet_flush(key, quiet_s))
        return None

    async def _quiet_flush(self, key, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            await self.flush_album(key)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("album flush failed for %s", key)

    async def flush_album(self, key) -> None:
        self._timers.pop(key, None)
        self._deadlines.pop(key, None)
        album = self._albums.pop(key, None)
        if album is None:
            return
        raw_id = await self._persist(
            key[0], album.anchor_msg_id, album.sender_peer_id, key[1],
            album.texts, order_media(album.media), album.posted_at,
        )
        self.albums_merged += 1
        logger.info(
            "album merged: source=%s anchor=%s media=%d texts=%d -> raw id=%s",
            key[0], album.anchor_msg_id, len(album.media), len(album.texts), raw_id,
        )

    async def drain(self) -> None:
        """Flush any open albums (used on shutdown)."""
        for key in list(self._albums):
            await self.flush_album(key)

    async def _persist(self, source_id, platform_msg_id, sender_peer_id,
                       grouped_id, texts, media, posted_at):
        merged_text = "\n".join(t for t in texts if t)
        rows = await self._pool.fetch(
            SQL_APPEND_RAW, source_id, platform_msg_id, sender_peer_id,
            grouped_id, merged_text, json.dumps(media), posted_at,
        )
        raw_id = rows[0]["raw_id"]
        await self._pool.fetch(SQL_ADVANCE_SOURCE, source_id, platform_msg_id)
        return raw_id


async def run_listener(pool: asyncpg.Pool, stop: asyncio.Event) -> None:
    pipeline = IngestPipeline(pool)
    try:
        if os.getenv("NEXUS_BRIDGE_SIM", "0") == "1":
            await _run_sim(pipeline, stop)
        else:
            await _run_real(pipeline, pool, stop)
    finally:
        await pipeline.drain()


def _render_sim_image(abs_path: str, line: str) -> None:
    """Render a simple black-on-white JPEG with one text line (for the
    OCR fallback scenario; container has no CJK font, so English)."""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (640, 240), (250, 250, 250))
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default(size=56)
    except TypeError:
        font = None
    draw.text((24, 90), line, fill=(16, 16, 16), font=font)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    image.save(abs_path, quality=90)


async def _run_sim(pipeline: IngestPipeline, stop: asyncio.Event) -> None:
    interval = float(os.getenv("NEXUS_BRIDGE_SIM_INTERVAL", "15"))
    media_dir = os.getenv("NEXUS_MEDIA_DIR", "/app/data/media")
    source_id = await pipeline.ensure_source(900000001, "sim-source", "sim_source_feed")
    logger.info("sim intake on source=%s (4 rotating scenarios)", source_id)

    async def sim_link_fetch(chat_ref: str, msg_id: int):
        return "昵称: 玲珑 价格: 666 元 城区: 西城"

    seq = int(time.time() * 1000) << 8
    cycle = 0
    while not stop.is_set():
        seq += 16
        scenario = cycle % 4
        if scenario == 0:
            # full structured text -> regex-only extraction
            await pipeline.accept(
                source_id, seq, 700000001, None,
                "昵称: 小雪 价格: 888 元 城区: 朝阳 @xiaoxue88", [],
                datetime.now(timezone.utc),
            )
            logger.info("sim: scenario 0 structured text sent")
            if cycle % 8 == 0:
                # type C: enqueue a bio probe for this contact (manual gate)
                await pipeline._pool.fetch(
                    sqlres.load("probe_enqueue.sql"),
                    700000001,
                    "主理人 · 主页入口见链接",
                    json.dumps(["https://t.me/demo_room"]),
                )
                logger.info("sim: bio probe enqueued for contact 700000001")
        elif scenario == 1:
            # album caption missing price + image containing it -> OCR
            for part, line in enumerate(("Price 555 CNY", "Contact Lily")):
                rel = f"sim/ocr_{seq}_{part}.jpg"
                try:
                    _render_sim_image(os.path.join(media_dir, rel), line)
                    media = [{"kind": "photo", "path": rel}]
                except Exception as exc:
                    logger.warning("sim image render failed: %s", exc)
                    media = []
                caption = "昵称: 阿夏 城区: 海淀" if part == 0 else ""
                await pipeline.accept(
                    source_id, seq + part, 700000002, seq, caption, media,
                    datetime.now(timezone.utc),
                )
            logger.info("sim: scenario 1 album with OCR-needed price sent")
        elif scenario == 2:
            # lifecycle term -> auto archive or drop
            await pipeline.accept(
                source_id, seq, 700000001, None,
                "已售，感谢关注", [], datetime.now(timezone.utc),
            )
            logger.info("sim: scenario 2 lifecycle message sent")
        else:
            # type B: aggregate link message -> target fetched and merged
            base_text = f"今日优选合辑 见引用 https://t.me/demo_digest/{seq % 10000}"
            merged, fetched = await expand_embedded_links(base_text, sim_link_fetch)
            await pipeline.accept(
                source_id, seq, 700000003, None, merged, [],
                datetime.now(timezone.utc),
            )
            logger.info("sim: scenario 3 type-B link message merged %d target(s)", fetched)
        # publicly visible presence (sim): alternate the two contacts
        await pipeline._pool.fetch(SQL_PRESENCE, 700000001, cycle % 2 == 0)
        await pipeline._pool.fetch(SQL_PRESENCE, 700000002, cycle % 2 == 1)
        cycle += 1
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


async def _run_real(pipeline: IngestPipeline, pool: asyncpg.Pool, stop: asyncio.Event) -> None:
    if not os.getenv("NEXUS_BRIDGE_API_ID") or not os.getenv("NEXUS_BRIDGE_API_HASH"):
        logger.warning(
            "protocol credentials missing (NEXUS_BRIDGE_API_ID/_API_HASH); "
            "listener idle — set them in .env or enable NEXUS_BRIDGE_SIM=1"
        )
        await stop.wait()
        return
    from runner.protocol import run_protocol_intake
    await run_protocol_intake(pipeline, pool, stop)
