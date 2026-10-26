"""Dialog discovery: refresh the groups/channels visible to each
gateway account into dialog_cache (the admin source-management UI
reads this table). SIM mode seeds synthetic dialogs.
"""
import asyncio
import logging
import os

import asyncpg

from runner import sqlres

logger = logging.getLogger("nexus-bridge.dialogs")

SQL_UPSERT = sqlres.load("dialog_upsert.sql")
SQL_ACTIVE_ACCOUNTS = sqlres.load("active_accounts.sql")
SQL_SESSION_OF = sqlres.load("session_file_of.sql")

SIM_DIALOGS = (
    (900100001, "同城交流群", "city_talk", "group"),
    (900100002, "优选房源频道", "home_channel", "channel"),
    (900100003, "二手市场", "second_hand", "group"),
)

REFRESH_S = float(os.getenv("NEXUS_BRIDGE_DIALOG_REFRESH", "600"))


async def _refresh_real(pool: asyncpg.Pool) -> None:
    from runner import hub

    accounts_rows = await pool.fetch(SQL_ACTIVE_ACCOUNTS)
    for acc in accounts_rows:
        session_name = str(acc["session_file"]).removesuffix(".session")
        try:
            client = await hub.get_client(session_name)
            if client is None:
                logger.warning("dialogs skip %s (unauthorized)", session_name)
                continue
            count = 0
            async for dialog in client.iter_dialogs(limit=200):
                kind = (
                    "channel" if dialog.is_channel and not dialog.is_group
                    else "group" if dialog.is_group or dialog.is_user
                    else "chat"
                )
                await pool.fetch(
                    SQL_UPSERT, acc["id"], dialog.id,
                    (dialog.name or "")[:255] or None,
                    getattr(dialog.entity, "username", None),
                    kind,
                )
                count += 1
            logger.info("dialogs refreshed for %s: %d item(s)", session_name, count)
        except Exception:
            logger.exception("dialog refresh failed for %s", session_name)


async def _refresh_sim(pool: asyncpg.Pool) -> None:
    rows = await pool.fetch(sqlres.load("register_account.sql"), 6660001, "sim-alpha（模拟）", "sim-alpha", "sim-alpha.session")
    account_id = rows[0]["account_id"]
    for peer_id, title, username, kind in SIM_DIALOGS:
        await pool.fetch(SQL_UPSERT, account_id, peer_id, title, username, kind)
    logger.info("sim dialogs seeded: %d item(s)", len(SIM_DIALOGS))


async def run_dialogs(pool: asyncpg.Pool, stop: asyncio.Event) -> None:
    sim = os.getenv("NEXUS_BRIDGE_SIM", "0") == "1"
    refresh = _refresh_sim if sim else _refresh_real
    while not stop.is_set():
        try:
            await refresh(pool)
        except Exception:
            logger.exception("dialogs cycle failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=REFRESH_S)
        except TimeoutError:
            pass
