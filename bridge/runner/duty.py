"""Low-cost on-duty snapshot intake for high-volume Telegram groups."""
import hashlib
import json
import logging
import re
from datetime import datetime, time, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from runner import sqlres

logger = logging.getLogger("nexus-bridge.duty")

SQL_SNAPSHOT = sqlres.load("duty_snapshot_upsert.sql")
SQL_LINK_LOOKUP = sqlres.load("duty_link_lookup.sql")
SQL_LINK_UPSERT = sqlres.load("duty_link_upsert.sql")
SQL_LISTING_LOOKUP = sqlres.load("duty_listing_lookup.sql")
SQL_PROFILE_COLLECTION_ENQUEUE = sqlres.load("profile_collection_enqueue_from_duty.sql")

TG_URL_RE = re.compile(r"https?://t\.me/([A-Za-z0-9_]+)(?:/(\d+))?[^\s]*", re.I)
MENTION_RE = re.compile(r"(?<![A-Za-z0-9_])@([A-Za-z][A-Za-z0-9_]{3,31})\b")
CONTACT_RE = re.compile(
    r"(?:电报|[Tt][Gg])[:：]\s*@?([A-Za-z][A-Za-z0-9_]{3,31})\b",
)


def decode_config(value) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    return json.loads(value)


def business_window(observed_at: datetime, cutoff_hour: int = 4):
    zone = ZoneInfo("Asia/Shanghai")
    local = observed_at.astimezone(zone)
    duty_date = (local - timedelta(hours=cutoff_hour)).date()
    expires_at = datetime.combine(
        duty_date + timedelta(days=1), time(cutoff_hour), tzinfo=zone,
    )
    return duty_date, expires_at


def message_matches(message, config: dict) -> bool:
    sender_id = config.get("sender_peer_id")
    if sender_id and int(message.sender_id or 0) != int(sender_id):
        return False
    prefix = str(config.get("match_prefix") or "").strip()
    return bool(prefix and (message.raw_text or "").lstrip().startswith(prefix))


def entity_urls(message) -> list[str]:
    urls = []
    for entity, shown in message.get_entities_text() or []:
        url = getattr(entity, "url", None)
        if url:
            urls.append(url)
        elif shown and shown.startswith(("http://", "https://")):
            urls.append(shown)
    urls.extend(match.group(0) for match in TG_URL_RE.finditer(message.raw_text or ""))
    return list(dict.fromkeys(urls))


def profile_usernames(message, config: dict | None = None) -> list[str]:
    config = config or {}
    excluded = {str(x).lower().lstrip("@") for x in config.get("exclude_usernames") or []}
    require_profile = bool(config.get("profile_query_required"))
    found = []
    for url in entity_urls(message):
        parsed = urlparse(url)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 1 or (require_profile and "profile" not in parsed.query.lower()):
            continue
        username = parts[0].lower()
        if username not in excluded:
            found.append(username)
    if not require_profile:
        for username in MENTION_RE.findall(message.raw_text or ""):
            if username.lower() not in excluded:
                found.append(username.lower())
    return sorted(set(found))


def _utf16_index(text: str, offset: int) -> int:
    units = 0
    for index, char in enumerate(text):
        if units >= offset:
            return index
        units += len(char.encode("utf-16-le")) // 2
    return len(text)


def classified_profile_usernames(message, config: dict):
    excluded = {str(x).lower().lstrip("@") for x in config.get("exclude_usernames") or []}
    require_profile = bool(config.get("profile_query_required"))
    text = message.raw_text or ""
    collected, green_only, green_blue = set(), set(), set()
    entities = []
    for entity, _ in message.get_entities_text() or []:
        url = getattr(entity, "url", None)
        if url:
            entities.append((entity, url))

    previous_end = 0
    for entity, url in entities:
        start = _utf16_index(text, int(getattr(entity, "offset", 0)))
        prior_end = previous_end
        end = int(getattr(entity, "offset", 0)) + int(getattr(entity, "length", 0))
        previous_end = max(previous_end, _utf16_index(text, end))
        parsed = urlparse(url)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 1 or (require_profile and "profile" not in parsed.query.lower()):
            continue
        username = parts[0].lower()
        if username in excluded:
            continue

        line_start = max(text.rfind("\n", 0, start) + 1, prior_end)
        prefix = text[line_start:start].rstrip("\u2060\u200b\ufeff \t\r")
        marks = set()
        while prefix:
            prefix = prefix.rstrip("\u2060\u200b\ufeff \t\r\ufe0f")
            if prefix.endswith("🟢"):
                marks.add("green")
                prefix = prefix[:-1]
            elif prefix.endswith("💙"):
                marks.add("blue")
                prefix = prefix[:-1]
            else:
                break

        if marks == {"green"}:
            green_only.add(username)
        else:
            collected.add(username)
            if marks == {"green", "blue"}:
                green_blue.add(username)

    # Keep legacy behavior for profile links represented only as visible URLs.
    for username in profile_usernames(message, config):
        if username not in collected and username not in green_only:
            collected.add(username)
    green_only.difference_update(collected)
    return sorted(collected), sorted(green_only), sorted(green_blue)


def linked_posts(message, target_channel: str) -> list[tuple[str, int, str]]:
    wanted = target_channel.lower().lstrip("@")
    found = []
    for url in entity_urls(message):
        match = TG_URL_RE.match(url)
        if match and match.group(2) and match.group(1).lower() == wanted:
            found.append((match.group(1), int(match.group(2)), url))
    return list(dict.fromkeys(found))


def listing_contact_username(message) -> str | None:
    match = CONTACT_RE.search(message.raw_text or "")
    return match.group(1).lower() if match else None


async def _resolve_link(
    pool, client, channel: str, msg_id: int, entity=None, target_peer_id=None,
):
    from telethon import utils

    if target_peer_id is not None:
        rows = await pool.fetch(SQL_LINK_LOOKUP, int(target_peer_id), msg_id)
        if rows:
            return rows[0]["contact_username"]
    entity = entity or await client.get_entity(
        int(target_peer_id) if target_peer_id is not None else channel,
    )
    target_peer_id = utils.get_peer_id(entity)
    if target_peer_id is not None:
        rows = await pool.fetch(SQL_LINK_LOOKUP, target_peer_id, msg_id)
        if rows:
            return rows[0]["contact_username"]

    rows = await pool.fetch(SQL_LISTING_LOOKUP, target_peer_id, msg_id)
    username = rows[0]["contact_username"] if rows else None
    if not username:
        target = await client.get_messages(entity, ids=msg_id)
        username = listing_contact_username(target) if target else None
        if not username and target:
            usernames = profile_usernames(target)
            username = usernames[0] if len(usernames) == 1 else None
    if username:
        username = username.lower().lstrip("@")
        await pool.fetch(SQL_LINK_UPSERT, target_peer_id, msg_id, username)
    return username


async def parse_usernames(pool, client, message, config: dict):
    mode = config.get("identity_mode")
    if mode == "direct_username_links":
        if config.get("duty_identity_markers"):
            usernames, green_only, green_blue = classified_profile_usernames(message, config)
            return usernames, [], {}, green_only, green_blue
        return profile_usernames(message, config), [], {}, [], []
    if mode != "linked_listing_messages":
        return [], ["unsupported identity mode"], {}, [], []

    channel = str(config.get("target_channel_username") or "").lstrip("@")
    posts = linked_posts(message, channel)
    if not posts:
        return [], [], {}, [], []
    target_peer_id = config.get("target_peer_id")
    entity = None
    if target_peer_id is None:
        from telethon import utils

        try:
            entity = await client.get_entity(channel)
            target_peer_id = utils.get_peer_id(entity)
        except Exception as exc:
            logger.warning("duty target channel resolve failed (%s): %s", channel, exc)
            return [], sorted({url for _, _, url in posts}), {}, [], []
    usernames = []
    unresolved = []
    linked_targets = {}
    for link_channel, msg_id, url in posts:
        try:
            username = await _resolve_link(
                pool, client, link_channel, msg_id, entity, target_peer_id,
            )
        except Exception as exc:
            logger.warning("duty link resolve failed (%s/%s): %s", link_channel, msg_id, exc)
            username = None
        if username:
            usernames.append(username)
            linked_targets.setdefault(username, (target_peer_id, msg_id))
        else:
            unresolved.append(url)
    return sorted(set(usernames)), sorted(set(unresolved)), linked_targets, [], []


async def process_message(pool, client, rule, message) -> bool:
    config = decode_config(rule["config_json"])
    if not message_matches(message, config):
        return False
    usernames, unresolved, linked_targets, green_only, green_blue = await parse_usernames(
        pool, client, message, config,
    )
    observed_at = message.edit_date or message.date or datetime.now(timezone.utc)
    duty_date, expires_at = business_window(
        observed_at, int(config.get("business_cutoff_hour") or 4),
    )
    digest = hashlib.sha256(json.dumps(
        {"usernames": usernames, "green_only": green_only,
         "green_blue": green_blue, "unresolved": unresolved},
        ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")).hexdigest()
    rows = await pool.fetch(
        SQL_SNAPSHOT, rule["rule_id"], rule["source_id"], duty_date,
        message.id, digest, json.dumps(usernames), json.dumps(unresolved),
        json.dumps(green_only), json.dumps(green_blue),
        observed_at, expires_at,
    )
    changed = bool(rows[0]["changed"])
    collection_source_id = config.get("collection_source_id")
    today, _ = business_window(datetime.now(timezone.utc), int(config.get("business_cutoff_hour") or 4))
    if (
        config.get("auto_collect_enabled", True)
        and collection_source_id
        and duty_date == today
    ):
        for username in usernames:
            target_peer_id, target_msg_id = linked_targets.get(username, (None, None))
            await pool.fetch(
                SQL_PROFILE_COLLECTION_ENQUEUE, duty_date,
                int(collection_source_id), username, target_peer_id, target_msg_id,
            )
    logger.info(
        "duty snapshot %s: rule=%s source=%s date=%s matched=%d unresolved=%d",
        "updated" if changed else "unchanged", rule["rule_id"],
        rule["source_id"], duty_date, len(usernames), len(unresolved),
    )
    return changed


def make_handler(pool, client, rule):
    async def handler(event):
        try:
            await process_message(pool, client, rule, event.message)
        except Exception:
            logger.exception("duty event failed (rule=%s)", rule["rule_id"])
    return handler


async def recover_latest(pool, client, rule) -> None:
    config = decode_config(rule["config_json"])
    sender_id = config.get("sender_peer_id")
    search_text = config.get("search_text") or config.get("match_prefix")
    if not sender_id or not search_text:
        return
    try:
        sender = await client.get_entity(int(sender_id))
        cutoff = int(config.get("business_cutoff_hour") or 4)
        business_today, _ = business_window(datetime.now(timezone.utc), cutoff)
        wanted_dates = {business_today, business_today - timedelta(days=1)}
        recovered = set()
        async for message in client.iter_messages(
            rule["peer_id"], from_user=sender, search=search_text, limit=30,
        ):
            if message_matches(message, config):
                observed_at = message.edit_date or message.date or datetime.now(timezone.utc)
                duty_date, _ = business_window(
                    observed_at, cutoff,
                )
                if duty_date not in wanted_dates or duty_date in recovered:
                    continue
                await process_message(pool, client, rule, message)
                recovered.add(duty_date)
                if recovered == wanted_dates:
                    return
    except Exception:
        logger.exception("duty recovery failed (rule=%s)", rule["rule_id"])
