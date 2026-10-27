"""Collect only messages linked from a matched daily on-duty digest."""
import asyncio
import json
import logging
import re

from runner import backfill, hub, sqlres
from runner.duty import entity_urls, listing_contact_username, profile_usernames

logger = logging.getLogger("nexus-bridge.duty-collection")

SQL_CLAIM = sqlres.load("duty_collection_claim.sql")
SQL_EXISTING = sqlres.load("duty_collection_existing.sql")
SQL_APPEND = sqlres.load("append_sample.sql")
SQL_FINISH = sqlres.load("duty_collection_finish.sql")
SQL_PROFILE_UPDATE = sqlres.load("duty_profile_update.sql")
SQL_PROFILE_LISTING = sqlres.load("duty_profile_listing.sql")
SQL_PROFILE_RATE = sqlres.load("duty_profile_rate.sql")
SQL_PROFILE_RATE_WAIT = sqlres.load("duty_profile_rate_wait.sql")
SQL_DEFER = sqlres.load("duty_collection_defer.sql")
POLL_S = 3.0
SQL_SCAN_CLAIM = sqlres.load("duty_recovery_scan_claim.sql")
SQL_SCAN_FINISH = sqlres.load("duty_recovery_scan_finish.sql")


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
    try:
        entity, message = await _find_message(client, job)
    except Exception as exc:
        if _is_flood_wait(exc):
            raise
        logger.info("daily-source lookup failed; resolving profile (job=%s): %s", job["job_id"], exc)
        return await _collect_profile_listing(pool, client, job)
    if message is None:
        return await _collect_profile_listing(pool, client, job)
    if not _matches_username(message, job["contact_username"]):
        return await _collect_profile_listing(pool, client, job)

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


def _json_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, list) else []
        except ValueError:
            return []
    return []


def _is_flood_wait(exc: Exception) -> bool:
    return type(exc).__name__ in {"FloodWaitError", "FloodPremiumWaitError"}


def _flood_wait_seconds(exc: Exception) -> int:
    return max(1, int(getattr(exc, "seconds", 1) or 1))


TG_PROFILE_LINK_RE = re.compile(r"https?://t\.me/([A-Za-z0-9_]+)(?:/(\d+))?", re.I)
TG_MENTION_RE = re.compile(r"(?<![A-Za-z0-9_])@([A-Za-z][A-Za-z0-9_]{3,31})\b", re.I)


def _profile_links(text: str) -> list[tuple[str, str | None]]:
    links = [(m.group(1), m.group(2)) for m in TG_PROFILE_LINK_RE.finditer(text or "")]
    links.extend((name, None) for name in TG_MENTION_RE.findall(text or ""))
    return list(dict.fromkeys(links))


def _entity_url(entity, fallback: str | None = None) -> str | None:
    username = getattr(entity, "username", None)
    return f"https://t.me/{username}" if username else fallback


async def _rate_limited_entity(pool, client, account_id, username):
    wait_s = await pool.fetchval(SQL_PROFILE_RATE_WAIT, account_id) or 0
    if wait_s:
        await asyncio.sleep(wait_s)
    entity = await client.get_entity(username)
    await pool.fetch(SQL_PROFILE_RATE, account_id, 30)
    return entity


async def _save_profile(pool, job, user, about, channel, channel_url, bot_url,
                        personal_channel_id=None, pinned_id=None, pinned_text=None):
    name = " ".join(x for x in (getattr(user, "first_name", None), getattr(user, "last_name", None)) if x)
    await pool.fetch(
        SQL_PROFILE_UPDATE, job["job_id"], int(user.id), name, about,
        f"https://t.me/{user.username or job['contact_username']}",
        personal_channel_id, channel_url, bot_url, pinned_id, pinned_text,
    )


async def _profile_channel(pool, client, job, full, user, about, saved_channel_url, saved_bot_url):
    from telethon.tl.types import Channel, PeerChannel

    full_user = full.full_user
    channel = None
    channel_url = saved_channel_url or None
    bot_url = saved_bot_url or None
    personal_id = getattr(full_user, "personal_channel_id", None)
    if personal_id:
        channel = next((item for item in full.chats if getattr(item, "id", None) == personal_id), None)
        if channel is None:
            try:
                channel = await client.get_entity(PeerChannel(personal_id))
            except Exception as exc:
                if _is_flood_wait(exc):
                    raise
                logger.info("profile channel lookup failed (user=%s): %s", user.id, exc)
        channel_url = _entity_url(channel, channel_url)

    if channel is None and channel_url:
        saved_match = TG_PROFILE_LINK_RE.search(channel_url)
        if saved_match:
            try:
                entity = await _rate_limited_entity(pool, client, job["account_id"], saved_match.group(1))
                if isinstance(entity, Channel):
                    channel = entity
                    channel_url = _entity_url(channel, channel_url)
            except Exception as exc:
                if _is_flood_wait(exc):
                    raise
                pass

    links = _profile_links(about or "")
    resolved_link = False
    for name, msg_id in links[:8]:
        link = f"https://t.me/{name}" + (f"/{msg_id}" if msg_id else "")
        if name.lower().endswith("bot") and not bot_url:
            bot_url = link
        if channel is not None or resolved_link:
            continue
        try:
            entity = await _rate_limited_entity(pool, client, job["account_id"], name)
            resolved_link = True
        except Exception as exc:
            if _is_flood_wait(exc):
                raise
            continue
        if getattr(entity, "bot", False):
            bot_url = bot_url or link
        elif channel is None and isinstance(entity, Channel):
            channel = entity
            channel_url = _entity_url(entity, link)

    if getattr(user, "bot", False):
        bot_url = bot_url or f"https://t.me/{user.username}"
    return channel, channel_url, bot_url, personal_id, full_user


async def _collect_profile_listing(pool, client, job) -> tuple[str, int | None, str | None]:
    """Collect exact Telegram profile information, linked channel pin, and authored posts."""
    from telethon.tl.functions.channels import GetFullChannelRequest
    from telethon.tl.functions.users import GetFullUserRequest

    user = await _rate_limited_entity(pool, client, job["account_id"], job["contact_username"])
    if not getattr(user, "id", None) or not hasattr(user, "first_name"):
        raise RuntimeError("Telegram username did not resolve to a user profile")
    full = await client(GetFullUserRequest(user))
    full_user = full.full_user
    about = (getattr(full_user, "about", None) or "").strip()
    channel, channel_url, bot_url, personal_id, full_user = await _profile_channel(
        pool, client, job, full, user, about, job["channel_url"], job["bot_url"],
    )
    await _save_profile(
        pool, job, user, about, channel, channel_url, bot_url, personal_id,
    )

    messages = []
    pinned_id = None
    if channel is not None:
        try:
            channel_full = await client(GetFullChannelRequest(channel))
            pinned_id = getattr(channel_full.full_chat, "pinned_msg_id", None)
        except Exception as exc:
            if _is_flood_wait(exc):
                raise
            logger.info("linked channel pin lookup failed (user=%s): %s", user.id, exc)
        selected_id = getattr(full_user, "personal_channel_message", None)
        for msg_id in dict.fromkeys(x for x in (pinned_id, selected_id) if x):
            try:
                msg = await client.get_messages(channel, ids=msg_id)
            except Exception as exc:
                if _is_flood_wait(exc):
                    raise
                msg = None
            if msg is not None and not isinstance(msg, list):
                messages.append(msg)

    # Only if the profile-linked pin/spotlight has no usable content, search
    # the last ten channel messages authored by this exact Telegram user ID.
    if channel is not None and not any(
        (m.raw_text or "").strip() or m.photo or m.video or m.video_note for m in messages
    ):
        try:
            async for msg in client.iter_messages(channel, from_user=user, limit=10):
                if int(msg.sender_id or 0) == int(user.id) and (
                    (msg.raw_text or "").strip() or msg.photo or msg.video or msg.video_note
                ):
                    messages.append(msg)
        except Exception as exc:
            if _is_flood_wait(exc):
                raise
            logger.info("authored-message scan failed (user=%s): %s", user.id, exc)

    texts = []
    media = []
    sources = []
    seen_ids = set()
    for msg in messages:
        if msg.id in seen_ids:
            continue
        parts = await _fetch_album(client, channel, msg) if channel is not None else [msg]
        anchor = next((part for part in parts if (part.raw_text or "").strip()), msg)
        if (anchor.raw_text or "").strip():
            label = "频道置顶消息" if msg.id == pinned_id else "关联频道消息"
            texts.append(f"{label}：\n{anchor.raw_text.strip()}")
        channel_username = getattr(channel, "username", None) if channel is not None else None
        if channel_username:
            sources.append(f"https://t.me/{channel_username}/{msg.id}")
        for part in parts:
            if part.id in seen_ids:
                continue
            seen_ids.add(part.id)
            media.extend(await backfill.save_media(
                part, job["source_id"], stable_prefix=f"profile_{job['job_id']}_{part.id}",
            ))

    pinned_text = next((text.split("：\n", 1)[1] for text in texts if text.startswith("频道置顶消息：\n")), None)
    if not bot_url:
        resolved_link = False
        related_text = "\n".join([about, *(msg.raw_text or "" for msg in messages)])
        links = _profile_links(related_text)
        for msg in messages:
            for url in entity_urls(msg):
                match = TG_PROFILE_LINK_RE.search(url)
                if match:
                    links.append((match.group(1), match.group(2)))
        for name, msg_id in dict.fromkeys(links):
            if name.lower().endswith("bot"):
                bot_url = f"https://t.me/{name}" + (f"/{msg_id}" if msg_id else "")
                break
            if channel is not None or resolved_link:
                continue
            try:
                linked = await _rate_limited_entity(pool, client, job["account_id"], name)
                resolved_link = True
            except Exception as exc:
                if _is_flood_wait(exc):
                    raise
                continue
            if getattr(linked, "bot", False):
                bot_url = f"https://t.me/{name}" + (f"/{msg_id}" if msg_id else "")
                break
    await _save_profile(
        pool, job, user, about, channel, channel_url, bot_url,
        personal_id, pinned_id, pinned_text,
    )
    description_parts = ([f"个人简介：\n{about}"] if about else []) + texts
    description = "\n\n".join(description_parts).strip()
    media = backfill.order_media(media)
    if not description and not media:
        return "not_found", None, "profile checked; no usable bio, pin, or exact-user message was found"

    profile_url = f"https://t.me/{user.username or job['contact_username']}"
    evidence = {
        "collection_method": {"value": "exact Telegram profile → linked channel pin/authored posts", "src": "telegram_profile"},
        "profile_url": {"value": profile_url, "src": "telegram_profile"},
        "channel_url": {"value": channel_url or "", "src": "telegram_profile"},
        "bot_url": {"value": bot_url or "", "src": "telegram_profile"},
        "profile_about": {"value": about, "src": "telegram_profile"},
        "source_messages": {"value": sources, "src": "telegram_channel"},
        "avatar_used": {"value": False, "src": "collection_policy"},
    }
    display_name = " ".join(x for x in (getattr(user, "first_name", None), getattr(user, "last_name", None)) if x)
    rows = await pool.fetch(
        SQL_PROFILE_LISTING, job["job_id"], int(user.id), job["contact_username"],
        display_name or job["contact_username"], description,
        json.dumps(media), json.dumps(evidence, ensure_ascii=False),
    )
    if not rows:
        raise RuntimeError("automatic profile card could not be persisted")
    return "fetched", None, None


async def _scan_recovery(client, job):
    channel_url = str(job["channel_url"] or "").strip()
    match = re.fullmatch(r"https?://t\.me/([A-Za-z0-9_]+)(?:/\d+)?/?(?:\?.*)?", channel_url, re.I)
    if not match:
        raise ValueError("save a public Telegram channel link before scanning")
    channel = match.group(1)
    entity = await client.get_entity(channel)
    cursor = int(job["scan_cursor"] or 0)
    count = int(job["scanned_count"] or 0)
    wanted = int(job["scan_target"])
    remaining = max(0, wanted - count)
    candidates = _json_list(job["candidates"])
    seen = {item.get("message_id") for item in candidates}
    async for message in client.iter_messages(entity, limit=remaining, offset_id=cursor):
        cursor = int(message.id)
        count += 1
        if _matches_username(message, job["contact_username"]) and message.id not in seen:
            seen.add(message.id)
            channel_name = getattr(entity, "username", None) or channel
            candidates.append({
                "message_id": int(message.id),
                "url": f"https://t.me/{channel_name}/{message.id}",
                "text": (message.raw_text or "")[:5000],
                "posted_at": message.date.isoformat() if message.date else None,
            })
    return count, cursor, candidates, "done"


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
                    if _is_flood_wait(exc):
                        wait_s = _flood_wait_seconds(exc)
                        await pool.fetch(SQL_DEFER, job["job_id"], job["account_id"], wait_s)
                        logger.warning(
                            "on-duty profile lookup rate limited; retry deferred (job=%s username=%s wait=%ss)",
                            job["job_id"], job["contact_username"], wait_s,
                        )
                        continue
                    await pool.fetch(SQL_FINISH, job["job_id"], "failed", None, str(exc)[:1000])
                    logger.warning(
                        "on-duty linked-message job failed (job=%s): %s",
                        job["job_id"], exc,
                    )
            scans = await pool.fetch(SQL_SCAN_CLAIM)
            for scan in scans:
                try:
                    if not scan["session_file"]:
                        raise RuntimeError("assigned Telegram account is unavailable")
                    client = await hub.get_client(str(scan["session_file"]).removesuffix(".session"))
                    if client is None:
                        raise RuntimeError("Telegram account is unauthorized")
                    count, cursor, candidates, status = await _scan_recovery(client, scan)
                    await pool.fetch(
                        SQL_SCAN_FINISH, scan["scan_id"], count, cursor,
                        json.dumps(candidates, ensure_ascii=False), status, None,
                    )
                    logger.info("on-duty recovery scan %s: job=%s scanned=%s candidates=%s",
                                status, scan["duty_job_id"], count, len(candidates))
                except Exception as exc:
                    await pool.fetch(
                        SQL_SCAN_FINISH, scan["scan_id"], scan["scanned_count"],
                        scan["scan_cursor"], json.dumps(_json_list(scan["candidates"])),
                        "failed", str(exc)[:1000],
                    )
                    logger.warning("on-duty recovery scan failed (job=%s): %s", scan["duty_job_id"], exc)
        except Exception:
            logger.exception("on-duty linked-message polling failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_S)
        except TimeoutError:
            pass
