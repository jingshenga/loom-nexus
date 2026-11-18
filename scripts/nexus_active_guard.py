#!/usr/bin/env python3
"""Continuous fail-closed supervisor for the approved source-2 Active route."""

import asyncio
import json
import logging
import os
import signal
import urllib.request
from datetime import datetime, timezone

import asyncpg


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("nexus-active-guard")
SOURCE_ID = 2
SOURCE_PEER_ID = -1003083173117
SERVICE_ACTOR_ID = int(os.environ.get("NEXUS_ACTIVE_GUARD_ACTOR_ID", "4"))
CORE_URL = os.environ.get("NEXUS_ACTIVE_GUARD_CORE_URL", "http://nexus-core:8000").rstrip("/")
POLL_SECONDS = 15
ROUTER_MODE_LOCK = 814271002
MAX_ACTIVE_ERRORS_15M = 3
MAX_PENDING_REVIEW = 100
MAX_PENDING_AGE_SECONDS = 1800
MAX_ACTIVE_BYTES = 524288000

METRICS_SQL = """
SELECT json_build_object(
 'modes',(SELECT json_agg(json_build_object('id',id,'mode',router_mode) ORDER BY id) FROM ingest_sources),
 'peer_id',(SELECT peer_id FROM ingest_sources WHERE id=2),
 'binding_signature',(SELECT md5(COALESCE(string_agg(concat_ws(':',rule_id,rule_type,purpose,match_group,mode,
    priority,rule_version_id,config_hash),'|' ORDER BY purpose,priority DESC,rule_id),''))
    FROM rule_shadow_bindings_for_source(2)),
 'pending_raw',(SELECT count(*) FROM raw_messages WHERE source_id=2 AND extract_state IN ('new','processing')),
 'oldest_pending_seconds',(SELECT COALESCE(extract(epoch FROM NOW()-min(posted_at)),0)::int
    FROM raw_messages WHERE source_id=2 AND extract_state IN ('new','processing')),
 'active_errors_15m',(SELECT count(*) FROM rule_active_review_queue WHERE source_id=2
    AND reason IN ('active_router_error','active_router_disabled') AND created_at >= NOW()-INTERVAL '15 minutes'),
 'active_queue_open',(SELECT count(*) FROM rule_active_review_queue WHERE source_id=2 AND status='open'),
 'oldest_active_review_seconds',(SELECT COALESCE(extract(epoch FROM NOW()-min(created_at)),0)::int
    FROM rule_active_review_queue WHERE source_id=2 AND status='open'),
 'active_bytes',(SELECT pg_total_relation_size('public.rule_active_review_queue'::regclass)),
 'source2_published_signature',(SELECT md5(COALESCE(string_agg(concat_ws(':',l.id,l.status,
    md5(COALESCE(l.manual_fields,'{}'::jsonb)::text)),'|' ORDER BY l.id),'')) FROM listings l
    JOIN raw_messages r ON r.id=l.source_msg_id WHERE r.source_id=2 AND l.status='published'),
 'source2_published_count',(SELECT count(*) FROM listings l JOIN raw_messages r ON r.id=l.source_msg_id
    WHERE r.source_id=2 AND l.status='published'),
 'active_db_sessions',(SELECT count(*)::int FROM pg_stat_activity WHERE state='active'),
 'idle_transactions',(SELECT count(*)::int FROM pg_stat_activity WHERE state='idle in transaction'),
 'max_transaction_seconds',(SELECT COALESCE(max(extract(epoch FROM NOW()-xact_start)),0)::int
    FROM pg_stat_activity WHERE xact_start IS NOT NULL),
 'lock_waits',(SELECT count(*)::int FROM pg_locks WHERE NOT granted)
)::text
"""


def db_kwargs():
    return {
        "host": os.environ.get("NEXUS_DB_HOST", "nexus-db"),
        "port": int(os.environ.get("NEXUS_DB_PORT", "5432")),
        "user": os.environ["NEXUS_DB_USER"],
        "password": os.environ["NEXUS_DB_PASSWORD"],
        "database": os.environ.get("NEXUS_DB_NAME", "nexus"),
        "command_timeout": 8,
        "server_settings": {"statement_timeout": "6000", "lock_timeout": "2000"},
    }


def core_health():
    with urllib.request.urlopen(CORE_URL + "/api/health", timeout=8) as response:
        result = json.loads(response.read())
    return result.get("status") == "ok" and result.get("db") == "up"


def _json_object(value):
    """asyncpg returns json/jsonb as text unless a custom codec is installed."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


async def write_heartbeat(conn, healthy, metrics=None, error=None):
    await conn.execute(
        "INSERT INTO active_router_guard_state(source_id,heartbeat_at,health_status,last_metrics_json,last_error,updated_at) "
        "VALUES($1,NOW(),$2,$3::jsonb,$4,NOW()) ON CONFLICT(source_id) DO UPDATE SET "
        "heartbeat_at=NOW(),health_status=EXCLUDED.health_status,last_metrics_json=EXCLUDED.last_metrics_json,"
        "last_error=EXCLUDED.last_error,updated_at=NOW()",
        SOURCE_ID, "healthy" if healthy else "degraded",
        json.dumps(metrics or {}, ensure_ascii=False), error,
    )


async def rollback_to_legacy(conn, reason):
    rows = await conn.fetch(
        "UPDATE ingest_sources SET router_mode='legacy' WHERE router_mode='active' RETURNING id",
    )
    for row in rows:
        source_id = int(row["id"])
        await conn.execute(
            "INSERT INTO rule_router_mode_events(source_id,previous_mode,new_mode,actor_user_id) "
            "VALUES($1,'active','legacy',$2)", source_id, SERVICE_ACTOR_ID,
        )
        log.error("automatic Legacy rollback source=%s reason=%s", source_id, reason)
    await conn.execute(
        "UPDATE active_router_guard_state SET active_started_at=NULL,active_baseline_json='{}'::jsonb,"
        "last_rollback_reason=$2,updated_at=NOW() WHERE source_id=$1", SOURCE_ID, reason,
    )


def anomaly_reason(metrics, state):
    enabled = os.environ.get("NEXUS_ACTIVE_ROUTER_ENABLED", "false").lower() in {"1", "true", "yes"}
    killed = os.environ.get("NEXUS_ACTIVE_ROUTER_KILL_SWITCH", "true").lower() in {"1", "true", "yes"}
    allowed = {part.strip() for part in os.environ.get("NEXUS_ACTIVE_ROUTER_SOURCE_IDS", "").split(",")
               if part.strip()}
    if not enabled or killed or allowed != {str(SOURCE_ID)}:
        return "active_router_runtime_gate_changed"
    if int(metrics.get("peer_id") or 0) != SOURCE_PEER_ID:
        return "source_identity_changed"
    modes = {int(item["id"]): item["mode"] for item in (metrics.get("modes") or [])}
    if set(modes) != {1, 2, 5, 6}:
        return "source_set_changed"
    if any(mode not in {"legacy", "shadow"} for source_id, mode in modes.items() if source_id != SOURCE_ID):
        return "unexpected_active_source"
    baseline = _json_object(state["active_baseline_json"])
    if not baseline:
        return "missing_activation_baseline"
    if metrics.get("binding_signature") != baseline.get("binding_signature"):
        return "source_rule_binding_changed"
    if metrics.get("source2_published_signature") != baseline.get("source2_published_signature"):
        return "published_listing_changed"
    if int(metrics.get("source2_published_count") or 0) != int(baseline.get("source2_published_count") or 0):
        return "published_listing_count_changed"
    if int(metrics.get("active_errors_15m") or 0) >= MAX_ACTIVE_ERRORS_15M:
        return "active_error_threshold"
    if int(metrics.get("active_bytes") or 0) >= MAX_ACTIVE_BYTES:
        return "active_review_storage_limit"
    if (int(metrics.get("active_queue_open") or 0) >= MAX_PENDING_REVIEW
            and int(metrics.get("oldest_active_review_seconds") or 0) >= MAX_PENDING_AGE_SECONDS):
        return "active_review_backlog"
    if (int(metrics.get("pending_raw") or 0) >= MAX_PENDING_REVIEW
            and int(metrics.get("oldest_pending_seconds") or 0) >= MAX_PENDING_AGE_SECONDS):
        return "source_raw_backlog"
    if (int(metrics.get("lock_waits") or 0) > 0
            and int(metrics.get("max_transaction_seconds") or 0) > 60):
        return "database_lock_anomaly"
    if int(metrics.get("idle_transactions") or 0) > 2:
        return "database_idle_transaction_anomaly"
    return None


async def check_once():
    conn = await asyncpg.connect(**db_kwargs())
    try:
        health_ok = False
        try:
            health_ok = core_health()
        except Exception as exc:
            await write_heartbeat(conn, False, error=type(exc).__name__)
        async with conn.transaction():
            await conn.fetchval("SELECT pg_advisory_xact_lock($1)", ROUTER_MODE_LOCK)
            metrics = json.loads(await conn.fetchval(METRICS_SQL))
            row = await conn.fetchrow(
                "SELECT active_started_at,active_baseline_json FROM active_router_guard_state WHERE source_id=$1",
                SOURCE_ID,
            )
            modes = {int(item["id"]): item["mode"] for item in (metrics.get("modes") or [])}
            if any(mode == "active" for source_id, mode in modes.items() if source_id != SOURCE_ID):
                await rollback_to_legacy(conn, "unexpected_active_source")
            elif modes.get(SOURCE_ID) == "active":
                if not health_ok:
                    await rollback_to_legacy(conn, "core_health_check_failed")
                elif row is None or row["active_started_at"] is None:
                    await rollback_to_legacy(conn, "active_mode_has_no_guard_lease")
                else:
                    reason = anomaly_reason(metrics, row)
                    if reason:
                        await rollback_to_legacy(conn, reason)
            await write_heartbeat(conn, health_ok, metrics, None if health_ok else "core_health_check_failed")
        if not health_ok:
            log.error("Core health check failed; source Active state has been revoked")
    finally:
        await conn.close()


async def run(stop):
    logging.info("continuous Active guard started for source %s", SOURCE_ID)
    while not stop.is_set():
        try:
            await check_once()
        except Exception as exc:
            log.exception("guard cycle failed")
            try:
                conn = await asyncpg.connect(**db_kwargs())
                await write_heartbeat(conn, False, error=type(exc).__name__)
                await conn.close()
            except Exception:
                log.error("guard heartbeat unavailable; Core will fail closed on stale heartbeat")
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_SECONDS)
        except TimeoutError:
            pass


if __name__ == "__main__":
    stopped = asyncio.Event()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopped.set)
    loop.run_until_complete(run(stopped))
    loop.close()
