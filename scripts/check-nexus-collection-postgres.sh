#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CANDIDATE="$1"
PREVIOUS="$2"
PG_IMAGE="$3"
PG_ID="$(docker run --rm --network none -e POSTGRES_HOST_AUTH_METHOD=trust -d "$PG_IMAGE")"
trap 'docker stop -t 3 "$PG_ID" >/dev/null 2>&1 || true' EXIT

for _ in $(seq 1 30); do
  if docker exec "$PG_ID" pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1; then break; fi
  sleep 1
done
docker exec "$PG_ID" createdb -U postgres nexus_collection
if [[ -n "${NEXUS_ISOLATED_SCHEMA_FILE:-}" ]]; then
  docker exec -i "$PG_ID" psql -Xq -v ON_ERROR_STOP=1 -U postgres -d nexus_collection < "$NEXUS_ISOLATED_SCHEMA_FILE"
else
  docker exec nexus-db sh -c 'exec pg_dump --schema-only --no-owner --no-privileges --no-comments --no-security-labels -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
    | docker exec -i "$PG_ID" psql -Xq -v ON_ERROR_STOP=1 -U postgres -d nexus_collection
fi

for _ in 1 2; do
  for migration in collection-reuse-job-audit collection-reuse-plan collection-reuse-stage collection-ocr-safe-flags bot-collection-precheck; do
    docker exec -i "$PG_ID" psql -Xq -v ON_ERROR_STOP=1 -U postgres -d nexus_collection \
      < "$ROOT/scripts/migrations/2026-11-18-$migration.sql"
  done
done
for fixture in collection_plan checkin_stage bot_precheck; do
  docker exec -i "$PG_ID" psql -Xq -v ON_ERROR_STOP=1 -U postgres -d nexus_collection \
    < "$ROOT/scripts/test_nexus_$fixture.sql"
done

PLATFORM="$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$CANDIDATE")"
CORE_RUN=(docker run --rm --platform "$PLATFORM" --network "container:$PG_ID" --entrypoint python
    -e PYTHONPATH=/app
    -e NEXUS_DB_HOST=127.0.0.1 -e NEXUS_DB_NAME=nexus_collection -e NEXUS_DB_USER=postgres
    -e NEXUS_COLLECTION_TEST_DSN=postgresql://postgres@127.0.0.1/nexus_collection
    -e NEXUS_ACTIVE_ROUTER_ENABLED=true -e NEXUS_ACTIVE_ROUTER_SOURCE_IDS=2 -e NEXUS_ACTIVE_ROUTER_KILL_SWITCH=false)
"${CORE_RUN[@]}" -v "$ROOT/scripts/test_nexus_collection_ocr_postgres.py:/nexus-tests/ocr.py:ro" \
    "$CANDIDATE" /nexus-tests/ocr.py
"${CORE_RUN[@]}" \
    -v "$ROOT/release/nexus-v3-20261110/BC/core/tests/e2e_active_pipeline_postgres.py:/nexus-tests/active.py:ro" \
    "$CANDIDATE" /nexus-tests/active.py

docker run --rm -i --platform "$PLATFORM" --network "container:$PG_ID" --entrypoint python "$PREVIOUS" - <<'PY'
import asyncio
import asyncpg
from app import sqlres

async def main():
    c = await asyncpg.connect(host="127.0.0.1", user="postgres", database="nexus_collection")
    try:
        await c.fetchrow(sqlres.load("review_detail.sql"), -1)
        await c.fetch(sqlres.load("profile_collection_enqueue_manual.sql"), -1, "fixture_compat", None)
    finally:
        await c.close()

asyncio.run(main())
PY
printf 'ISOLATED_COLLECTION_GATE_OK candidate=%s db_image=%s\n' "$CANDIDATE" "$PG_IMAGE"
