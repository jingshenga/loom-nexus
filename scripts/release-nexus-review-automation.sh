#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BC="$ROOT/release/nexus-v3-20261110/BC"
PROJECT_DIR="${NEXUS_PROJECT_DIR:-/volume3/docker/nexus}"
export COMPOSE_FILE="$PROJECT_DIR/docker-compose.yaml"
export CORE_BASELINE_FILE="$PROJECT_DIR/.nexus-core-baseline"
export NEXUS_RELEASE_COMMIT="$(tr -d '[:space:]' < "$ROOT/.nexus-release-commit")"
export NEXUS_CORE_BASE_IMAGE="$(docker inspect --format '{{.Config.Image}}' nexus-core)"
export NEXUS_CORE_BASE_IMAGE_ID="$(docker inspect --format '{{.Image}}' nexus-core)"
export NEXUS_CORE_CANDIDATE_IMAGE="nexus-core:review-automation-${NEXUS_RELEASE_COMMIT:0:12}"
[[ "$NEXUS_RELEASE_COMMIT" =~ ^[0-9a-f]{40}$ ]]
[[ "$(docker inspect --format '{{.State.Health.Status}}' nexus-core)" == healthy ]]
[[ "$(docker inspect --format '{{.State.Health.Status}}' nexus-db)" == healthy ]]
[[ "$(tr -d '[:space:]' < "$CORE_BASELINE_FILE")" == "$NEXUS_CORE_BASE_IMAGE_ID" ]]
(cd "$ROOT" && sha256sum -c --quiet SHA256SUMS.source)
bash "$BC/scripts/build-core-release.sh"
bash "$ROOT/scripts/check-nexus-collection-postgres.sh" "$NEXUS_CORE_CANDIDATE_IMAGE" \
    "$NEXUS_CORE_BASE_IMAGE_ID" "$(docker inspect --format '{{.Image}}' nexus-db)"
MIGRATION="$ROOT/scripts/migrations/2026-11-19-review-automation.sql"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$PROJECT_DIR/release-backups/nexus-review-automation-$STAMP"
mkdir -m 0700 "$BACKUP"
cp -p "$COMPOSE_FILE" "$BACKUP/docker-compose.yaml"
cp -p "$CORE_BASELINE_FILE" "$BACKUP/.nexus-core-baseline"
docker compose exec -T nexus-db sh -c 'exec pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$BACKUP/postgres.dump"
docker exec -i nexus-db pg_restore --list < "$BACKUP/postgres.dump" >/dev/null
sha256sum "$BACKUP/postgres.dump" "$BACKUP/docker-compose.yaml" "$MIGRATION" > "$BACKUP/SHA256SUMS"
[[ "$(docker inspect --format '{{.Image}}' nexus-core)" == "$NEXUS_CORE_BASE_IMAGE_ID" ]]
docker compose exec -T nexus-db sh -c 'exec psql -Xq -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "$MIGRATION"
CORE_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$NEXUS_CORE_CANDIDATE_IMAGE")"
bash "$BC/scripts/deploy-core-release.sh" "$NEXUS_CORE_CANDIDATE_IMAGE" "$NEXUS_RELEASE_COMMIT" "$CORE_IMAGE_ID"
printf 'REVIEW_AUTOMATION_RELEASE_OK backup=%s\n' "$BACKUP"
