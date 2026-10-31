"""nexus-bridge process skeleton (M0).

One asyncio process. Current scope:
- wait for database availability with retry/backoff,
- run a periodic connection check loop,
- keep an idle placeholder where the M1 protocol listeners attach.

The real gateway work (multi-account long connections, album debounce
aggregation, outbox dispatch) lands in M1; this module only proves the
process lifecycle and database wiring.
"""
import asyncio
import logging
import os
import signal
import time

import asyncpg

from runner import account_control, backfill, dialogs, duty_collection, hub, listener, login, media_meta, presence, probe, recollect, sender
from runner.dbconn import connect_kwargs

logger = logging.getLogger("nexus-bridge")

CHECK_INTERVAL = float(os.getenv("NEXUS_BRIDGE_CHECK_INTERVAL", "30"))
CONNECT_MAX_DELAY = 30.0


async def connect_with_retry(stop: asyncio.Event) -> asyncpg.Pool:
    delay = 2.0
    while not stop.is_set():
        try:
            pool = await asyncpg.create_pool(**connect_kwargs(), min_size=1, max_size=4)
            logger.info("database pool established")
            return pool
        except Exception as exc:
            logger.warning("database unreachable (%s); retrying", exc)
        try:
            # stop-aware sleep: shut down promptly on SIGTERM/SIGINT
            await asyncio.wait_for(stop.wait(), timeout=delay)
            break
        except TimeoutError:
            pass
        delay = min(delay * 1.6, CONNECT_MAX_DELAY)
    raise SystemExit("stopped before the database became reachable")


async def connection_check_loop(pool: asyncpg.Pool, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            started = time.perf_counter()
            await pool.fetchval("SELECT 1")
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.info("connection check ok (%.1f ms)", elapsed_ms)
        except Exception as exc:
            logger.error("connection check failed: %s", exc)
        try:
            await asyncio.wait_for(stop.wait(), timeout=CHECK_INTERVAL)
        except TimeoutError:
            pass


async def maintenance_loop(pool: asyncpg.Pool, stop: asyncio.Event) -> None:
    """Hourly housekeeping: auto-close idle chat sessions so gateway
    accounts keep circulating in the anti-collision pool."""
    from runner import sqlres as _sqlres

    sql = _sqlres.load("maintenance_close.sql")
    max_idle = int(os.getenv("NEXUS_BRIDGE_MAX_IDLE_HOURS", "72"))
    while not stop.is_set():
        try:
            rows = await pool.fetch(sql, max_idle)
            if rows[0]["closed"]:
                logger.info("maintenance closed %d stale session(s)", rows[0]["closed"])
        except Exception:
            logger.exception("maintenance cycle failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=3600)
        except TimeoutError:
            pass


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    logger.info("nexus-bridge starting (M0 skeleton; protocol listeners arrive in M1)")
    pool = await connect_with_retry(stop)

    tasks = [
        asyncio.create_task(connection_check_loop(pool, stop), name="connection-check"),
        asyncio.create_task(listener.run_listener(pool, stop), name="listener-skeleton"),
        asyncio.create_task(sender.run_sender(pool, stop), name="outbox-sender"),
        asyncio.create_task(presence.run_presence(pool, stop), name="presence-reader"),
        asyncio.create_task(probe.run_probe(pool, stop), name="probe-executor"),
        asyncio.create_task(maintenance_loop(pool, stop), name="maintenance"),
        asyncio.create_task(dialogs.run_dialogs(pool, stop), name="dialog-refresh"),
        asyncio.create_task(backfill.run_backfill(pool, stop), name="backfill"),
        asyncio.create_task(duty_collection.run_duty_collection(pool, stop), name="duty-collection"),
        asyncio.create_task(recollect.run_recollect(pool, stop), name="recollect"),
        asyncio.create_task(login.run_login_watcher(pool, stop), name="login-watcher"),
        asyncio.create_task(account_control.run_account_control(pool, stop), name="account-control"),
        asyncio.create_task(media_meta.run_enrich(pool, stop), name="media-enrich"),
    ]
    await stop.wait()
    logger.info("shutdown signal received")
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await hub.disconnect_all()
    await pool.close()
    logger.info("nexus-bridge stopped")


if __name__ == "__main__":
    asyncio.run(main())
