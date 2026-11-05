"""Schema readiness verifier for nexus-edge (PG 16).

The schema itself lives in scripts/schema.sql and is applied by the
official postgres entrypoint on the first boot of an empty nexus-db
data directory (mounted at /docker-entrypoint-initdb.d/). This script
executes no write-path SQL: it connects, checks that every expected
table, index, and the default rule seed exist, and reports the result.

Usage (inside the nexus-core container, after `docker compose up -d`):
    docker compose exec nexus-core python scripts/init_db.py

Exit codes: 0 = schema ready; 1 = objects missing (guidance logged).
"""
import asyncio
import logging
import os
import sys

import asyncpg

logger = logging.getLogger("nexus-init")

EXPECTED_TABLES = (
    "bridge_accounts",
    "chat_messages",
    "chat_sessions",
    "ingest_rules",
    "ingest_sources",
    "ingest_source_account_backups",
    "listings",
    "listing_media_ocr",
    "listing_duplicate_decisions",
    "ocr_classification_categories",
    "ocr_classification_labels",
    "ocr_classifier_versions",
    "outbox_jobs",
    "profile_collection_jobs",
    "profile_collection_results",
    "checkin_history_scans",
    "checkin_identity_index",
    "raw_messages",
    "source_rule_bindings",
    "user_favorites",
)
EXPECTED_INDEXES = (
    "idx_chat_single_active_session",
    "idx_listings_search",
    "idx_listing_media_ocr_profile_media",
    "idx_listing_media_ocr_profile_result_media",
    "idx_outbox_queue",
)
EXPECTED_SEED_RULE = "default-rule"

REAPPLY_HINT = (
    "schema incomplete — first-boot SQL is applied automatically only on an empty "
    "nexus-db data directory; apply the matching idempotent file from scripts/migrations/ "
    "with psql -v ON_ERROR_STOP=1 before restarting nexus-core"
)


def connect_kwargs() -> dict:
    """Connection parameters passed as typed kwargs (never SQL text)."""
    return {
        "host": os.getenv("NEXUS_DB_HOST", "localhost"),
        "port": int(os.getenv("NEXUS_DB_PORT", "5432")),
        "user": os.getenv("NEXUS_DB_USER", "nexus"),
        "password": os.getenv("NEXUS_DB_PASSWORD", "nexus"),
        "database": os.getenv("NEXUS_DB_NAME", "nexus"),
    }


async def collect_state() -> tuple[set, set, set]:
    conn = await asyncpg.connect(**connect_kwargs())
    try:
        tables = {
            row["table_name"]
            for row in await conn.fetch(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                """
            )
        }
        indexes = {
            row["indexname"]
            for row in await conn.fetch(
                """
                SELECT indexname FROM pg_indexes
                WHERE schemaname = 'public'
                """
            )
        }
        seed_rules = {
            row["rule_name"]
            for row in await conn.fetch(
                """
                SELECT rule_name FROM ingest_rules
                """
            )
        }
    finally:
        await conn.close()
    return tables, indexes, seed_rules


async def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    tables, indexes, seed_rules = await collect_state()

    for name in sorted(tables):
        logger.info("table ready: %s", name)

    missing_tables = sorted(set(EXPECTED_TABLES) - tables)
    missing_indexes = sorted(set(EXPECTED_INDEXES) - indexes)
    seed_ok = EXPECTED_SEED_RULE in seed_rules

    if missing_tables or missing_indexes or not seed_ok:
        for name in missing_tables:
            logger.error("missing table: %s", name)
        for name in missing_indexes:
            logger.error("missing index: %s", name)
        if not seed_ok:
            logger.error("missing seed rule: %s", EXPECTED_SEED_RULE)
        logger.error(REAPPLY_HINT)
        return 1

    logger.info(
        "schema ready: %d tables, %d indexes, seed rule present",
        len(tables),
        len(indexes),
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
