"""Contact presence poller (publicly visible status only, per spec cut).

Contacts are the extracted @usernames of PUBLISHED listings — never the
channel bot that posted the card. The first pass resolves each username
to a user id and caches it back onto the listings (presence_bind_peer);
later passes refresh statuses in ONE batched users.getUsers round-trip.

Three tiers land in contact_presence:
    online  – UserStatusOnline
    recent  – Offline(was_online) / Recently / LastWeek / LastMonth
    unknown – anything else (privacy, resolve failure, non-user)

SIM mode does not run this loop (sim intake writes synthetic presence).
"""
import asyncio
import logging
import os

import asyncpg

from runner import sqlres

logger = logging.getLogger("nexus-bridge.presence")

SQL_UPSERT = sqlres.load("presence_upsert.sql")
SQL_BIND = sqlres.load("presence_bind.sql")
SQL_TARGETS = sqlres.load("presence_targets.sql")

ACCOUNT_QUERY = """
SELECT session_file FROM bridge_accounts
WHERE status = 'active' ORDER BY id LIMIT 1
"""

_BATCH = 50


def _tier(status) -> str:
    name = type(status).__name__
    if name == "UserStatusOnline":
        return "online"
    if name in (
        "UserStatusOffline", "UserStatusRecently",
        "UserStatusLastWeek", "UserStatusLastMonth",
    ):
        return "recent"
    return "unknown"


def _is_user(entity) -> bool:
    return type(entity).__name__ == "User"


async def _fresh_statuses(client, entities: list) -> dict:
    """One users.getUsers round-trip per chunk for up-to-date statuses
    (session-cached entities would otherwise replay stale status)."""
    from telethon.tl.functions.users import GetUsersRequest
    from telethon.tl.types import InputUser

    inputs = []
    for e in entities:
        access_hash = getattr(e, "access_hash", None)
        if access_hash is not None and _is_user(e):
            inputs.append(InputUser(e.id, access_hash))
    statuses = {}
    for i in range(0, len(inputs), _BATCH):
        try:
            users = await client(GetUsersRequest(id=inputs[i:i + _BATCH]))
        except Exception as exc:
            logger.warning("batched status fetch failed: %s", exc)
            continue
        for user in users:
            if user is not None:
                statuses[user.id] = user.status
    return statuses


async def _shared_client(pool: asyncpg.Pool):
    rows = await pool.fetch(ACCOUNT_QUERY)
    if not rows:
        return None
    from runner import hub

    session_name = str(rows[0]["session_file"]).removesuffix(".session")
    return await hub.get_client(session_name)


async def run_presence(pool: asyncpg.Pool, stop: asyncio.Event) -> None:
    if os.getenv("NEXUS_BRIDGE_SIM", "0") == "1":
        logger.info("presence handled by sim intake; real loop idle")
        await stop.wait()
        return
    if not (os.getenv("NEXUS_BRIDGE_API_ID", "")
            and os.getenv("NEXUS_BRIDGE_API_HASH", "")):
        logger.info("presence idle (no protocol credentials)")
        await stop.wait()
        return
    interval = float(os.getenv("NEXUS_BRIDGE_PRESENCE_INTERVAL", "300"))
    logger.info("presence loop started (every %.0fs)", interval)
    while not stop.is_set():
        try:
            client = await _shared_client(pool)
            if client is None:
                logger.warning("presence: no authorized client")
            else:
                targets = await pool.fetch(SQL_TARGETS)
                resolved = []   # [(username, entity)]
                for row in targets:
                    username = row["contact_username"]
                    known_peer = row["contact_peer_id"]
                    try:
                        entity = await client.get_entity(
                            known_peer if known_peer else username,
                        )
                    except Exception as exc:
                        logger.info(
                            "presence resolve failed for @%s: %s",
                            username, str(exc)[:80],
                        )
                        continue
                    if known_peer != entity.id:
                        # first resolution (or changed id): cache it
                        await pool.fetch(SQL_BIND, username, entity.id)
                    resolved.append((username, entity))
                statuses = await _fresh_statuses(
                    client, [e for _u, e in resolved],
                )
                tiers = {}
                for username, entity in resolved:
                    tier = _tier(statuses.get(entity.id, entity.status))
                    tiers[tier] = tiers.get(tier, 0) + 1
                    await pool.fetch(SQL_UPSERT, entity.id, username, tier)
                logger.info(
                    "presence checked %d contact(s): %s",
                    len(resolved),
                    ", ".join(f"{k}={v}" for k, v in sorted(tiers.items()))
                    or "none",
                )
        except Exception:
            logger.exception("presence cycle failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass
