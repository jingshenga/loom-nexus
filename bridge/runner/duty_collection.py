"""Collect only messages linked from a matched daily on-duty digest."""
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

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
PROFILE_LOOKUP_INTERVAL_SECONDS = max(
    1, int(os.getenv("NEXUS_PROFILE_LOOKUP_INTERVAL_SECONDS", "15")),
)
LEGACY_COLLECTION_ENABLED = os.getenv(
    "NEXUS_DUTY_LEGACY_COLLECTION_ENABLED", "0",
).strip().lower() in {"1", "true", "yes", "on"}
SQL_SCAN_CLAIM = sqlres.load("duty_recovery_scan_claim.sql")
SQL_SCAN_FINISH = sqlres.load("duty_recovery_scan_finish.sql")
SQL_PROFILE_CLAIM = sqlres.load("profile_collection_claim.sql")
SQL_PROFILE_FINISH = sqlres.load("profile_collection_finish.sql")
SQL_PROFILE_DEFER = sqlres.load("profile_collection_defer.sql")
SQL_PROFILE_RESULT = sqlres.load("profile_collection_result.sql")
SQL_PROFILE_OCR_ENQUEUE = sqlres.load("profile_collection_ocr_enqueue.sql")


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
TG_BOT_USERNAME_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_]{1,28}bot)\b", re.I)
PROMO_MARKER_RE = re.compile(
    r"博彩|彩票|贷款|返利|刷单|高回报|招商|加盟|优惠券|代购|推广|扫码|兼职日结"
)
PROMO_URL_RE = re.compile(r"(?:https?://|www\.|t\.me/)", re.I)
MAX_PINNED_MESSAGES = 3
MAX_PROFILE_PHOTOS = 4
MAX_PROFILE_VIDEOS = 2
MAX_PROFILE_V2_PHOTOS = 6
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}


def _is_obvious_promo(text: str) -> bool:
    """Reject only clear ad/scam text; ordinary contact links remain eligible."""
    normalized = (text or "").strip()
    markers = PROMO_MARKER_RE.findall(normalized)
    urls = PROMO_URL_RE.findall(normalized)
    return bool(re.search(r"博彩|彩票|贷款|返利|刷单|高回报", normalized)) or (
        len(markers) >= 2 and len(urls) >= 2
    )


def _profile_media_kind(message) -> str | None:
    file = getattr(message, "file", None)
    mime_type = str(getattr(file, "mime_type", "") or "").lower()
    extension = str(getattr(file, "ext", "") or "").lower()
    media = getattr(message, "media", None)
    document = getattr(message, "document", None) or getattr(media, "document", None)
    document_mime = str(getattr(document, "mime_type", "") or "").lower()
    document_attributes = getattr(document, "attributes", ()) or ()
    photo = getattr(message, "photo", None)
    if (getattr(message, "video", None) or getattr(message, "video_note", None)
            or mime_type.startswith("video/") or extension in VIDEO_EXTENSIONS
            or document_mime.startswith("video/")
            or any(type(attr).__name__ == "DocumentAttributeVideo" for attr in document_attributes)
            or bool(getattr(photo, "video_sizes", None))):
        return "video"
    if photo:
        return "photo"
    return None


def _has_profile_content(message) -> bool:
    text = (message.raw_text or "").strip()
    if text and _is_obvious_promo(text):
        return False
    return bool(text or message.photo or message.video or message.video_note)


def _profile_links(text: str) -> list[tuple[str, str | None]]:
    links = [(m.group(1), m.group(2)) for m in TG_PROFILE_LINK_RE.finditer(text or "")]
    links.extend((name, None) for name in TG_MENTION_RE.findall(text or ""))
    links.extend((name, None) for name in TG_BOT_USERNAME_RE.findall(text or ""))
    unique = list(dict.fromkeys(links))
    return sorted(unique, key=lambda item: not item[0].lower().endswith("bot"))


def _entity_url(entity, fallback: str | None = None) -> str | None:
    username = getattr(entity, "username", None)
    return f"https://t.me/{username}" if username else fallback


async def _rate_limited_entity(pool, client, account_id, username):
    wait_s = await pool.fetchval(SQL_PROFILE_RATE_WAIT, account_id) or 0
    if wait_s:
        await asyncio.sleep(wait_s)
    entity = await client.get_entity(username)
    await pool.fetch(SQL_PROFILE_RATE, account_id, PROFILE_LOOKUP_INTERVAL_SECONDS)
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
    from telethon.tl.types import Channel, Chat, PeerChannel

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
                if isinstance(entity, (Channel, Chat)):
                    channel = entity
                    channel_url = _entity_url(channel, channel_url)
            except Exception as exc:
                if _is_flood_wait(exc):
                    raise
                pass

    links = _profile_links(about or "")
    lookup_failures = 0
    resolved_non_bots = 0
    for name, msg_id in links[:8]:
        link = f"https://t.me/{name}" + (f"/{msg_id}" if msg_id else "")
        try:
            entity = await _rate_limited_entity(pool, client, job["account_id"], name)
        except Exception as exc:
            if _is_flood_wait(exc):
                raise
            lookup_failures += 1
            continue
        if getattr(entity, "bot", False):
            bot_url = bot_url or _entity_url(entity, link)
        elif channel is None and isinstance(entity, (Channel, Chat)):
            channel = entity
            channel_url = _entity_url(entity, link)
        else:
            resolved_non_bots += 1
        if bot_url and channel:
            break

    if getattr(user, "bot", False):
        bot_url = bot_url or f"https://t.me/{user.username}"
    if job.get("rule_type") == "bot_collection":
        logger.info(
            "bot profile lookup (job=%s candidates=%s lookup_failures=%s "
            "resolved_non_bots=%s found=%s)",
            job["job_id"], min(len(links), 8), lookup_failures,
            resolved_non_bots, bool(bot_url),
        )
    return channel, channel_url, bot_url, personal_id, full_user


async def _collect_profile_listing(pool, client, job) -> tuple[str, int | None, str | None]:
    """Collect exact Telegram profile information, linked channel pin, and authored posts."""
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
    pinned_ids = set()
    selected_id = getattr(full_user, "personal_channel_message", None)
    if channel is not None:
        try:
            from telethon.tl.types import InputMessagesFilterPinned

            async for msg in client.iter_messages(
                channel, filter=InputMessagesFilterPinned, limit=MAX_PINNED_MESSAGES,
            ):
                messages.append(msg)
                pinned_ids.add(int(msg.id))
            if messages:
                pinned_id = messages[0].id
        except Exception as exc:
            if _is_flood_wait(exc):
                raise
            logger.info("linked channel pin lookup failed (user=%s): %s", user.id, exc)
        for msg_id in dict.fromkeys(x for x in (selected_id,) if x):
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
    if channel is not None and not any(_has_profile_content(m) for m in messages):
        try:
            async for msg in client.iter_messages(channel, from_user=user, limit=10):
                if int(msg.sender_id or 0) == int(user.id) and _has_profile_content(msg):
                    messages.append(msg)
        except Exception as exc:
            if _is_flood_wait(exc):
                raise
            logger.info("authored-message scan failed (user=%s): %s", user.id, exc)

    texts = []
    media = []
    sources = []
    filtered_count = 0
    seen_ids = set()
    for msg in messages:
        if msg.id in seen_ids:
            continue
        if not _has_profile_content(msg):
            filtered_count += 1
            continue
        parts = await _fetch_album(client, channel, msg) if channel is not None else [msg]
        anchor = next((part for part in parts if (part.raw_text or "").strip()), msg)
        if (anchor.raw_text or "").strip():
            label = "频道置顶消息" if msg.id in pinned_ids else "关联频道消息"
            texts.append(f"{label}：\n{anchor.raw_text.strip()}")
        channel_username = getattr(channel, "username", None) if channel is not None else None
        if channel_username:
            sources.append({
                "url": f"https://t.me/{channel_username}/{msg.id}",
                "message_id": int(msg.id),
                "posted_at": msg.date.isoformat() if msg.date else None,
                "matched_by": "exact_sender_user_id" if int(msg.sender_id or 0) == int(user.id)
                else "pinned_or_profile_linked_channel",
            })
        for part in parts:
            if part.id in seen_ids:
                continue
            seen_ids.add(part.id)
            is_photo = bool(part.photo)
            is_video = bool(part.video or part.video_note)
            if not (is_photo or is_video):
                continue
            media_kind = "photo" if is_photo else "video"
            media_limit = MAX_PROFILE_PHOTOS if is_photo else MAX_PROFILE_VIDEOS
            if sum(item.get("kind") == media_kind for item in media) >= media_limit:
                continue
            media.extend(await backfill.save_media(
                part, job["source_id"], stable_prefix=f"profile_{job['job_id']}_{part.id}",
            ))

    pinned_text = next((text.split("：\n", 1)[1] for text in texts if text.startswith("频道置顶消息：\n")), None)
    if not bot_url:
        related_text = "\n".join([about, *(msg.raw_text or "" for msg in messages)])
        links = _profile_links(related_text)
        for msg in messages:
            for url in entity_urls(msg):
                match = TG_PROFILE_LINK_RE.search(url)
                if match:
                    links.append((match.group(1), match.group(2)))
        links = sorted(dict.fromkeys(links), key=lambda item: not item[0].lower().endswith("bot"))
        for name, msg_id in links[:8]:
            link = f"https://t.me/{name}" + (f"/{msg_id}" if msg_id else "")
            try:
                linked = await _rate_limited_entity(pool, client, job["account_id"], name)
            except Exception as exc:
                if _is_flood_wait(exc):
                    raise
                continue
            if getattr(linked, "bot", False):
                bot_url = _entity_url(linked, link)
                break
    await _save_profile(
        pool, job, user, about, channel, channel_url, bot_url,
        personal_id, pinned_id, pinned_text,
    )
    description_parts = ([f"个人简介：\n{about}"] if about else []) + texts
    description = "\n\n".join(description_parts).strip()
    media = backfill.order_media(media)
    if not description and not media:
        reason = "profile checked; no usable bio, pin, or exact-user message was found"
        if filtered_count:
            reason += f"; filtered {filtered_count} obvious promotional message(s)"
        return "not_found", None, reason

    profile_url = f"https://t.me/{user.username or job['contact_username']}"
    evidence = {
        "collection_method": {"value": "exact Telegram profile → linked channel pin/authored posts", "src": "telegram_profile"},
        "profile_url": {"value": profile_url, "src": "telegram_profile"},
        "channel_url": {"value": channel_url or "", "src": "telegram_profile"},
        "bot_url": {"value": bot_url or "", "src": "telegram_profile"},
        "profile_about": {"value": about, "src": "telegram_profile"},
        "source_messages": {"value": sources, "src": "telegram_channel"},
        "profile_scan": {
            "value": {
                "pinned_limit": MAX_PINNED_MESSAGES,
                "authored_fallback_limit": 10,
                "filtered_obvious_promo_count": filtered_count,
                "photo_limit": MAX_PROFILE_PHOTOS,
                "video_limit": MAX_PROFILE_VIDEOS,
            },
            "src": "collection_policy",
        },
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


async def _collect_profile_trial(pool, client, job) -> tuple[str, str | None]:
    """Collect a V2 result into its isolated trial table; never writes a listing."""
    from telethon.tl.functions.users import GetFullUserRequest

    user = await _rate_limited_entity(pool, client, job["account_id"], job["contact_username"])
    if not getattr(user, "id", None) or not hasattr(user, "first_name"):
        raise RuntimeError("Telegram username did not resolve to a user profile")
    full = await client(GetFullUserRequest(user))
    full_user = full.full_user
    about = (getattr(full_user, "about", None) or "").strip()
    channel, channel_url, bot_url, _, full_user = await _profile_channel(
        pool, client, job, full, user, about, "", "",
    )
    cfg = job["config_json"]
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    cfg = cfg if isinstance(cfg, dict) else {}
    recent_window = max(1, min(10, int(cfg.get("recent_window", 10))))
    scan_limit = max(recent_window, min(100, int(cfg.get("max_scan_messages", 100))))
    photo_limit = max(1, min(MAX_PROFILE_V2_PHOTOS, int(cfg.get("max_photos", 6))))
    video_limit = max(0, min(MAX_PROFILE_VIDEOS, int(cfg.get("max_videos", 2))))
    stale_days = max(1, min(180, int(cfg.get("stale_channel_days", 60))))
    followup_limit = max(1, min(5, int(cfg.get("pinned_followup_messages", 5))))
    min_chars = max(1, int(cfg.get("intro_min_chars", 30)))
    min_lines = max(1, int(cfg.get("intro_min_lines", 3)))
    spotlight_id = getattr(full_user, "personal_channel_message", None)
    messages, pinned_ids = [], set()
    pinned_followup_ids = set()
    profile_link_ids = set()
    scanned = 0
    channel_last_message_at = None
    if channel is not None:
        channel_name = (getattr(channel, "username", None) or "").lower()
        channel_label = channel_url or _entity_url(channel) or "关联频道"
        latest = None
        try:
            async for latest in client.iter_messages(channel, limit=1):
                break
        except Exception as exc:
            if _is_flood_wait(exc):
                raise
            logger.info("V2 latest-message check unavailable (user=%s): %s", user.id, exc)
            return "source_unavailable", f"无法确认频道最近更新时间，已跳过：{channel_label}"
        if latest is not None and latest.date:
            latest_at = latest.date
            if latest_at.tzinfo is None:
                latest_at = latest_at.replace(tzinfo=timezone.utc)
            channel_last_message_at = latest_at
            if latest_at < datetime.now(timezone.utc) - timedelta(days=stale_days):
                return "stale_source", (
                    f"频道最近消息早于 {stale_days} 天，按规则跳过：{channel_label} "
                    f"（最近更新 {latest_at.date().isoformat()}）"
                )
        for link_name, message_id in _profile_links(about):
            if message_id and link_name.lower() == channel_name:
                try:
                    linked_message = await client.get_messages(channel, ids=int(message_id))
                    if linked_message is not None and not isinstance(linked_message, list):
                        messages.append(linked_message)
                        profile_link_ids.add(int(linked_message.id))
                except Exception as exc:
                    if _is_flood_wait(exc):
                        raise
        try:
            from telethon.tl.types import InputMessagesFilterPinned
            async for msg in client.iter_messages(channel, filter=InputMessagesFilterPinned, limit=3):
                messages.append(msg)
                pinned_ids.add(int(msg.id))
                scanned += 1
        except Exception as exc:
            if _is_flood_wait(exc):
                raise
            logger.info("V2 linked-channel pins unavailable (user=%s): %s", user.id, exc)
        if pinned_ids:
            anchor_id = max(pinned_ids)
            try:
                async for msg in client.iter_messages(
                    channel, min_id=anchor_id, limit=min(followup_limit, max(0, scan_limit - scanned)),
                ):
                    if int(msg.id) not in {int(item.id) for item in messages}:
                        messages.append(msg)
                        pinned_followup_ids.add(int(msg.id))
                        scanned += 1
            except Exception as exc:
                if _is_flood_wait(exc):
                    raise
                logger.info("V2 pinned follow-up scan unavailable (user=%s): %s", user.id, exc)
        if spotlight_id:
            try:
                msg = await client.get_messages(channel, ids=spotlight_id)
                if msg is not None and not isinstance(msg, list):
                    messages.append(msg)
            except Exception as exc:
                if _is_flood_wait(exc):
                    raise
        usable = [m for m in messages if _has_profile_content(m)]
        intro = about or "\n".join((m.raw_text or "").strip() for m in usable)
        pinned_photos = sum(_profile_media_kind(msg) == "photo" for msg in usable)
        if len(re.sub(r"\s+", "", intro)) < min_chars or pinned_photos < min(photo_limit, 6):
            recent = []
            try:
                async for msg in client.iter_messages(
                    channel, limit=min(recent_window, max(0, scan_limit - scanned)),
                ):
                    recent.append(msg)
            except Exception as exc:
                if _is_flood_wait(exc):
                    raise
                logger.info("V2 recent channel scan unavailable (user=%s): %s", user.id, exc)
            scanned += len(recent)
            messages.extend(recent)
            intro = about or "\n".join((m.raw_text or "").strip() for m in messages if _has_profile_content(m))
            recent_photos = sum(bool(msg.photo) for msg in messages)
            if (len(re.sub(r"\s+", "", intro)) < min_chars or recent_photos < min(photo_limit, 6)) and scanned < scan_limit and recent:
                remaining = scan_limit - scanned
                try:
                    async for msg in client.iter_messages(channel, limit=remaining, offset_id=min(m.id for m in recent)):
                        messages.append(msg)
                        scanned += 1
                except Exception as exc:
                    if _is_flood_wait(exc):
                        raise
                    logger.info("V2 older channel scan unavailable (user=%s): %s", user.id, exc)

    unique = []
    seen_ids = set()
    filtered_count = 0
    for msg in messages:
        if not msg or int(msg.id) in seen_ids:
            continue
        seen_ids.add(int(msg.id))
        if _is_obvious_promo((msg.raw_text or "").strip()):
            filtered_count += 1
            continue
        if _has_profile_content(msg):
            unique.append(msg)

    text_parts = [f"个人简介：\n{about}"] if about else []
    media, video_candidates, sources = [], [], []
    channel_username = getattr(channel, "username", None) if channel is not None else None
    for msg in unique:
        parts = await _fetch_album(client, channel, msg) if channel is not None else [msg]
        anchor = next((part for part in parts if (part.raw_text or "").strip()), msg)
        raw_text = (anchor.raw_text or "").strip()
        if len(re.sub(r"\s+", "", raw_text)) >= min_chars or any(p.photo or p.video for p in parts):
            if raw_text:
                text_parts.append(raw_text)
            if channel_username:
                sources.append({
                    "url": f"https://t.me/{channel_username}/{msg.id}",
                    "message_id": int(msg.id),
                    "posted_at": msg.date.isoformat() if msg.date else None,
                    "pinned": int(msg.id) in pinned_ids,
                    "pinned_followup": int(msg.id) in pinned_followup_ids,
                    "profile_linked": int(msg.id) in profile_link_ids,
                    "matched_by": "profile_link" if int(msg.id) in profile_link_ids else "linked_channel_any_sender",
                })
        for part in parts:
            if any(item.get("message_id") == int(part.id) for item in media):
                continue
            media_kind = _profile_media_kind(part)
            if media_kind == "photo" and sum(item["kind"] == "photo" for item in media) < photo_limit:
                entries = await backfill.save_media(
                    part, job["source_id"], stable_prefix=f"profile_v2_{job['job_id']}_{part.id}",
                    force_kind="photo",
                )
                media.extend({**item, "message_id": int(part.id)} for item in entries if item.get("kind") == "photo")
            elif media_kind == "video":
                video_candidates.append(part)

    if not any(item["kind"] == "photo" for item in media):
        for part in video_candidates[:video_limit]:
            entries = await backfill.save_media(
                part, job["source_id"], stable_prefix=f"profile_v2_{job['job_id']}_{part.id}",
                force_kind="video",
            )
            media.extend({**item, "message_id": int(part.id)} for item in entries if item.get("kind") == "video")
    description = "\n\n".join(dict.fromkeys(text_parts)).strip()
    if not description and not media:
        return "not_found", "No usable profile, linked-channel, or media content found"

    evidence = {
        "collection_method": "profile -> linked channel pins and next 5 -> recent 10 -> older/gallery messages",
        "profile_url": f"https://t.me/{user.username or job['contact_username']}",
        "source_messages": sources,
        "scan_count": scanned,
        "scan_limit": scan_limit,
        "stale_channel_days": stale_days,
        "channel_last_message_at": channel_last_message_at.isoformat() if channel_last_message_at else None,
        "pinned_followup_message_ids": sorted(pinned_followup_ids),
        "filtered_promo_count": filtered_count,
        "photo_limit": photo_limit,
        "video_limit_when_no_photos": video_limit,
        "avatar_used": False,
        "intro_min_chars": min_chars,
        "intro_min_lines": min_lines,
        "any_sender_in_linked_channel": True,
    }
    rows = await pool.fetch(
        SQL_PROFILE_RESULT, job["job_id"], int(user.id), job["contact_username"],
        evidence["profile_url"], channel_url or "", bot_url or "", description,
        json.dumps(backfill.order_media(media), ensure_ascii=False),
        json.dumps(evidence, ensure_ascii=False), json.dumps(cfg, ensure_ascii=False),
    )
    result_id = rows[0]["id"]
    await pool.execute(SQL_PROFILE_OCR_ENQUEUE, result_id, json.dumps(media, ensure_ascii=False))
    return "fetched", None


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
    logger.info(
        "profile collection worker started (lookup_interval=%ss legacy_enabled=%s)",
        PROFILE_LOOKUP_INTERVAL_SECONDS, LEGACY_COLLECTION_ENABLED,
    )
    while not stop.is_set():
        try:
            if LEGACY_COLLECTION_ENABLED:
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
            profile_jobs = await pool.fetch(SQL_PROFILE_CLAIM)
            for job in profile_jobs:
                try:
                    if not job["session_file"]:
                        raise RuntimeError("assigned Telegram account is unavailable")
                    client = await hub.get_client(str(job["session_file"]).removesuffix(".session"))
                    if client is None:
                        raise RuntimeError("Telegram account is unauthorized")
                    status, error = await _collect_profile_trial(pool, client, job)
                    await pool.fetch(SQL_PROFILE_FINISH, job["job_id"], status, error)
                    logger.info("profile V2 job %s: job=%s username=%s",
                                status, job["job_id"], job["contact_username"])
                except Exception as exc:
                    if _is_flood_wait(exc):
                        wait_s = _flood_wait_seconds(exc)
                        await pool.fetch(SQL_PROFILE_DEFER, job["job_id"], wait_s)
                        logger.warning("profile V2 job deferred by FloodWait (job=%s wait=%ss)",
                                       job["job_id"], wait_s)
                        continue
                    await pool.fetch(SQL_PROFILE_FINISH, job["job_id"], "failed", str(exc)[:1000])
                    logger.warning("profile V2 job failed (job=%s): %s", job["job_id"], exc)
        except Exception:
            logger.exception("on-duty linked-message polling failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_S)
        except TimeoutError:
            pass
