"""Bridge-side control actions for gateway accounts."""
import asyncio
import logging

from runner import hub, sqlres

logger = logging.getLogger("nexus-bridge.account-control")

SQL_CLAIM = sqlres.load("account_disconnect_claim.sql")
SQL_FINISH = sqlres.load("account_disconnect_finish.sql")
SQL_ACTIVATE = sqlres.load("account_activate_finish.sql")


async def run_account_control(pool, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            rows = await pool.fetch(SQL_CLAIM)
            if rows:
                request = rows[0]
                session_name = str(request["session_file"]).removesuffix(".session")
                error = None
                try:
                    if request["action"] == "activate":
                        await hub.allow_client(session_name)
                        await pool.execute(SQL_ACTIVATE, request["account_id"])
                        logger.info("account session enabled: %s", session_name)
                    else:
                        await hub.force_disconnect(session_name)
                        logger.info("account session released: %s", session_name)
                except Exception as exc:
                    error = str(exc)[:200]
                    logger.exception("account session release failed: %s", session_name)
                await pool.execute(SQL_FINISH, request["request_id"], error)
                continue
        except Exception:
            logger.exception("account control cycle failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.5)
        except TimeoutError:
            pass
