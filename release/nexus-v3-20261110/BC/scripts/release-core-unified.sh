#!/usr/bin/env bash
set -Eeuo pipefail

BC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_DIR="$(cd "$BC_DIR/../../.." && pwd)"
PROJECT_DIR="${NEXUS_PROJECT_DIR:-/volume3/docker/nexus}"
COMPOSE_FILE="${COMPOSE_FILE:-$PROJECT_DIR/docker-compose.yaml}"
COMMIT="${NEXUS_RELEASE_COMMIT:-$(git -C "$REPO_DIR" rev-parse HEAD 2>/dev/null || tr -d '[:space:]' < "$REPO_DIR/.nexus-release-commit")}"
CANDIDATE_IMAGE="${NEXUS_CORE_CANDIDATE_IMAGE:-nexus-core:collection-reuse-${COMMIT:0:12}}"
BRIDGE_CANDIDATE="nexus-bridge:collection-reuse-${COMMIT:0:12}"

die() { printf 'BLOCKED: %s\n' "$*" >&2; exit 1; }
ROLLBACK_REQUIRED=0
ROLLBACK_RUNNING=0
rollback_after_failure() {
    local status=$?
    if (( status != 0 && ROLLBACK_REQUIRED == 1 && ROLLBACK_RUNNING == 0 )); then
        ROLLBACK_RUNNING=1
        ROLLBACK_REQUIRED=0
        printf 'POST_DEPLOY_FAILURE: restoring Core %s and Bridge %s\n' "$CURRENT_ID" "$CURRENT_BRIDGE_ID" >&2
        if [[ -f "$BACKUP_DIR/.nexus-core-baseline" ]]; then
            cp -p "$BACKUP_DIR/.nexus-core-baseline" "$PROJECT_DIR/.nexus-core-baseline" || true
        else
            rm -f "$PROJECT_DIR/.nexus-core-baseline"
        fi
        if [[ -f "$BACKUP_DIR/nexus_active_guard.py" ]]; then
            cp -p "$BACKUP_DIR/nexus_active_guard.py" "$PROJECT_DIR/scripts/nexus_active_guard.py" || true
        fi
        if cp -p "$BACKUP_DIR/docker-compose.yaml" "$COMPOSE_FILE" \
            && docker compose -f "$COMPOSE_FILE" up -d --no-deps --no-build nexus-core nexus-bridge nexus-active-guard; then
            for _ in $(seq 1 60); do
                state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' nexus-core 2>/dev/null || true)"
                image_id="$(docker inspect --format '{{.Image}}' nexus-core 2>/dev/null || true)"
                bridge_state="$(docker inspect --format '{{.State.Status}} {{.Image}}' nexus-bridge 2>/dev/null || true)"
                if [[ "$state" == healthy && "$image_id" == "$CURRENT_ID" && "$bridge_state" == "running $CURRENT_BRIDGE_ID" ]]; then
                    printf 'POST_DEPLOY_ROLLBACK_OK image_id=%s compose=%s\n' "$image_id" "$BACKUP_DIR/docker-compose.yaml" >&2
                    break
                fi
                if (( _ == 60 )); then
                    printf 'POST_DEPLOY_ROLLBACK_FAILED expected=%s actual=%s state=%s\n' "$CURRENT_ID" "$image_id" "$state" >&2
                fi
                sleep 2
            done
        else
            printf 'POST_DEPLOY_ROLLBACK_FAILED compose restore or Core restart failed; backup=%s\n' "$BACKUP_DIR" >&2
        fi
    fi
    return "$status"
}
trap rollback_after_failure EXIT
[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]] || die "release commit marker is invalid"
[[ -f "$COMPOSE_FILE" ]] || die "production Compose not found: $COMPOSE_FILE"
docker compose -f "$COMPOSE_FILE" ps --status running >/dev/null || die "Compose is unavailable"

CURRENT_ID="$(docker inspect --format '{{.Image}}' nexus-core 2>/dev/null)" || die "nexus-core is not running"
CURRENT_REF="$(docker inspect --format '{{.Config.Image}}' nexus-core)"
CURRENT_BRIDGE_ID="$(docker inspect --format '{{.Image}}' nexus-bridge 2>/dev/null)" || die "nexus-bridge is unavailable"
CURRENT_BRIDGE_REF="$(docker inspect --format '{{.Config.Image}}' nexus-bridge)"
[[ "$(docker inspect --format '{{.State.Status}}' nexus-bridge)" == running ]] || die "current Bridge is not running"
[[ "$(docker inspect --format '{{.State.Health.Status}}' nexus-core 2>/dev/null)" == healthy ]] \
    || die "current Core is not healthy"
[[ "$CURRENT_ID" =~ ^sha256:[0-9a-f]{64}$ ]] \
    || die "running Core Image ID is invalid; stop and reconcile source first"

router_runtime_settings() {
    docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' nexus-core \
      | awk -F= '$1 ~ /^(NEXUS_VISION4_MODE|NEXUS_ACTIVE_ROUTER_ENABLED|NEXUS_ACTIVE_ROUTER_SOURCE_IDS|NEXUS_ACTIVE_ROUTER_KILL_SWITCH|NEXUS_OCR_REVIEW_ASSIST_ENABLED)$/ {print}' \
      | LC_ALL=C sort
}
PREDEPLOY_ROUTER_SETTINGS="$(router_runtime_settings)"

export NEXUS_CORE_BASE_IMAGE="$CURRENT_REF"
export NEXUS_CORE_BASE_IMAGE_ID="$CURRENT_ID"
export NEXUS_CORE_CANDIDATE_IMAGE="$CANDIDATE_IMAGE"
export NEXUS_RELEASE_COMMIT="$COMMIT"
bash "$BC_DIR/scripts/build-core-release.sh"
CANDIDATE_ID="$(docker image inspect --format '{{.Id}}' "$CANDIDATE_IMAGE" 2>/dev/null)" \
    || die "candidate image was not built"
bash "$REPO_DIR/scripts/build-nexus-bridge-overlay.sh" "$CURRENT_BRIDGE_REF" "$CURRENT_BRIDGE_ID" "$BRIDGE_CANDIDATE" "$COMMIT" \
    || die "Bridge candidate build or regression failed"
BRIDGE_CANDIDATE_ID="$(docker image inspect --format '{{.Id}}' "$BRIDGE_CANDIDATE")"
DB_IMAGE_ID="$(docker inspect --format '{{.Image}}' nexus-db)"
[[ "$(docker inspect --format '{{.State.Health.Status}}' nexus-db)" == healthy ]] || die "current PostgreSQL is not healthy"
bash "$REPO_DIR/scripts/check-nexus-collection-postgres.sh" "$CANDIDATE_IMAGE" "$CURRENT_ID" "$DB_IMAGE_ID" \
    || die "isolated PostgreSQL collection gate failed"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$PROJECT_DIR/release-backups/nexus-v3-unified-$STAMP"
mkdir -m 0700 -p "$BACKUP_DIR" || die "backup directory creation failed"
cp -p "$COMPOSE_FILE" "$BACKUP_DIR/docker-compose.yaml" || die "Compose backup failed"
if [[ -f "$PROJECT_DIR/scripts/nexus_active_guard.py" ]]; then
    cp -p "$PROJECT_DIR/scripts/nexus_active_guard.py" "$BACKUP_DIR/nexus_active_guard.py"
fi
if [[ -f "$PROJECT_DIR/.nexus-core-baseline" ]]; then
    cp -p "$PROJECT_DIR/.nexus-core-baseline" "$BACKUP_DIR/.nexus-core-baseline"
fi
docker image tag "$CURRENT_ID" "nexus-core:rollback-nexus-v3-unified-$STAMP"
docker image tag "$CURRENT_BRIDGE_ID" "nexus-bridge:rollback-nexus-v3-unified-$STAMP"
docker save "$CURRENT_ID" | gzip -c > "$BACKUP_DIR/previous-core.tar.gz" || die "previous Core image archive failed"
sha256sum "$BACKUP_DIR/previous-core.tar.gz" > "$BACKUP_DIR/previous-core.tar.gz.sha256"
docker save "$CURRENT_BRIDGE_ID" | gzip -c > "$BACKUP_DIR/previous-bridge.tar.gz" || die "previous Bridge image archive failed"
sha256sum "$BACKUP_DIR/previous-bridge.tar.gz" > "$BACKUP_DIR/previous-bridge.tar.gz.sha256"

docker compose -f "$COMPOSE_FILE" exec -T nexus-db sh -lc \
  'exec pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$BACKUP_DIR/postgres.dump" \
  || die "PostgreSQL backup failed"
[[ -s "$BACKUP_DIR/postgres.dump" ]] || die "PostgreSQL backup is empty"
docker exec -i nexus-db pg_restore --list < "$BACKUP_DIR/postgres.dump" >/dev/null \
  || die "PostgreSQL backup is unreadable"
sha256sum "$BACKUP_DIR/postgres.dump" > "$BACKUP_DIR/postgres.dump.sha256"
sha256sum "$BACKUP_DIR/docker-compose.yaml" > "$BACKUP_DIR/docker-compose.yaml.sha256"

docker compose -f "$COMPOSE_FILE" exec -T nexus-db sh -lc \
  'exec psql -XAtq -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  > "$BACKUP_DIR/predeploy-state.txt" <<'SQL'
BEGIN READ ONLY;
SELECT 'source_modes',COALESCE(string_agg(id||':'||router_mode,',' ORDER BY id),'') FROM ingest_sources;
SELECT 'listing_status',COALESCE(string_agg(status||':'||n,',' ORDER BY status),'')
FROM (SELECT status,count(*)::bigint n FROM listings GROUP BY status) s;
SELECT 'raw_processing',count(*) FROM raw_messages WHERE extract_state='processing';
SELECT 'active_open_reviews',count(*) FROM rule_active_review_queue WHERE status='open';
ROLLBACK;
SQL
grep -q 'source_modes' "$BACKUP_DIR/predeploy-state.txt" || die "could not record production baseline"
ACTIVE_COUNT="$(docker compose -f "$COMPOSE_FILE" exec -T nexus-db sh -lc \
  'exec psql -XAtq -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT count(*) FROM ingest_sources WHERE router_mode<>chr(108)||chr(101)||chr(103)||chr(97)||chr(99)||chr(121)"' \
)" || die "could not verify source modes"
[[ "$ACTIVE_COUNT" == "0" ]] || die "all sources must be Legacy before this release"

apply_migration() {
    local file="$REPO_DIR/scripts/migrations/$1"
    [[ -f "$file" ]] || die "migration is missing: $file"
    sha256sum "$file" >> "$BACKUP_DIR/migrations.sha256"
    docker compose -f "$COMPOSE_FILE" exec -T nexus-db sh -lc \
      'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "$file" \
      || die "migration failed: $1; Core remains on the previous image"
}
apply_migration 2026-11-12-active-review-unmatched-triage.sql
apply_migration 2026-11-12-active-ocr-review-assist.sql
apply_migration 2026-11-12-active-ocr-recheck-isolation.sql
apply_migration 2026-11-12-vision4-model-registry.sql
apply_migration 2026-11-14-active-router-supervisor-engine-audit.sql
apply_migration 2026-11-18-collection-reuse-job-audit.sql
apply_migration 2026-11-18-collection-reuse-plan.sql
apply_migration 2026-11-18-collection-reuse-stage.sql
apply_migration 2026-11-18-collection-ocr-safe-flags.sql
apply_migration 2026-11-18-bot-collection-precheck.sql

GUARD_SOURCE="$REPO_DIR/scripts/nexus_active_guard.py"
[[ -f "$GUARD_SOURCE" ]] || die "continuous Active supervisor source is missing"
docker run --rm --network none --entrypoint python \
    -e PYTHONPATH=/release-guard \
    -v "$GUARD_SOURCE:/release-guard/nexus_active_guard.py:ro" \
    -v "$REPO_DIR/scripts/test_nexus_active_guard.py:/release-tests/test_nexus_active_guard.py:ro" \
    "$CANDIDATE_IMAGE" -m unittest discover -s /release-tests -p 'test_nexus_active_guard.py' \
    || die "Active supervisor JSONB/fail-closed regression failed"
[[ "$(docker inspect --format '{{.Image}}' nexus-core)" == "$CURRENT_ID" \
   && "$(docker inspect --format '{{.Image}}' nexus-bridge)" == "$CURRENT_BRIDGE_ID" \
   && "$(docker inspect --format '{{.Image}}' nexus-db)" == "$DB_IMAGE_ID" ]] \
    || die "production baseline changed during candidate build"
ROLLBACK_REQUIRED=1
install -m 0644 "$GUARD_SOURCE" "$PROJECT_DIR/scripts/nexus_active_guard.py" \
    || die "could not install the Active supervisor script"
sha256sum "$PROJECT_DIR/scripts/nexus_active_guard.py" > "$BACKUP_DIR/nexus_active_guard.py.sha256"
python3 - "$COMPOSE_FILE" "$CANDIDATE_IMAGE" "$BRIDGE_CANDIDATE" <<'PY' || die "could not pin Bridge and the continuous Active supervisor"
import os, re, sys, tempfile
path, image, bridge_image = sys.argv[1:]
text = open(path, encoding="utf-8").read()
bridge = re.search(r"(?ms)^  nexus-bridge:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|^networks:\n|\Z)", text)
assert bridge, "nexus-bridge service missing from production Compose"
bridge_section, count = re.subn(r"(?m)^(    image:[ \t]*)\S+[ \t]*$", lambda m: m.group(1) + bridge_image, bridge.group(1))
assert count == 1, "expected one Bridge image setting"
text = text[:bridge.start(1)] + bridge_section + text[bridge.end(1):]
match = re.search(r"(?ms)^  nexus-active-guard:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|^networks:\n|\Z)", text)
assert match, "nexus-active-guard service missing from production Compose"
section = match.group(1)
section, count = re.subn(r"(?m)^(    image:\s*)\S+\s*$", lambda m: m.group(1) + image, section)
assert count == 1, "expected one supervisor image setting"
if re.search(r"(?m)^    restart:\s*", section):
    section = re.sub(r"(?m)^    restart:\s*\S+\s*$", "    restart: always", section)
else:
    section = "    restart: always\n" + section
assert "nexus_active_guard.py:/app/scripts/nexus_active_guard.py:ro" in section, "supervisor source must be mounted read-only from persistent project scripts"
text = text[:match.start(1)] + section + text[match.end(1):]
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".compose-guard-")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    f.write(text); f.flush(); os.fsync(f.fileno())
os.chmod(tmp, os.stat(path).st_mode)
os.replace(tmp, path)
PY
docker compose -f "$COMPOSE_FILE" config --quiet || die "supervisor Compose validation failed"

CORE_BASELINE_FILE="$PROJECT_DIR/.nexus-core-baseline" \
  COMPOSE_FILE="$COMPOSE_FILE" \
  bash "$BC_DIR/scripts/deploy-core-release.sh" "$CANDIDATE_IMAGE" "$COMMIT" "$CANDIDATE_ID" \
  || die "formal Core deployment failed; deploy script attempted automatic rollback"

docker compose -f "$COMPOSE_FILE" up -d --no-deps --no-build nexus-bridge || die "Bridge deployment failed"
for _ in $(seq 1 30); do
    [[ "$(docker inspect --format '{{.State.Status}} {{.Image}}' nexus-bridge)" == "running $BRIDGE_CANDIDATE_ID" ]] || die "Bridge is not running the candidate"
    if docker logs --tail 200 nexus-bridge 2>&1 | grep -F 'connection check ok' >/dev/null; then break; fi
    sleep 2
done
docker logs --tail 200 nexus-bridge 2>&1 | grep -F 'connection check ok' >/dev/null || die "Bridge did not confirm its database connection"

docker compose -f "$COMPOSE_FILE" exec -T nexus-core python - <<'PY' \
  || die "post-deploy Vision4 inference check failed"
import tempfile
from pathlib import Path
from PIL import Image
from app.config import CoreSettings
from app.extractor.vision4_classifier import Vision4Classifier
settings = CoreSettings()
processor = __import__("app.main", fromlist=["app"])
processor_state = getattr(processor.app.state, "media_ocr_processor", None)
model_dir = processor_state._vision4.model_dir if processor_state else Path(settings.vision4_model_dir)
classifier = Vision4Classifier("active", str(model_dir))
with tempfile.TemporaryDirectory(prefix="nexus-v4-smoke-") as temp:
    sample = Path(temp) / "synthetic.png"
    Image.new("RGB", (64, 64), (128, 128, 128)).save(sample)
    assert classifier.predict(str(sample)).get("status") == "classified"
PY

ACTIVE_COUNT="$(docker compose -f "$COMPOSE_FILE" exec -T nexus-db sh -lc \
  'exec psql -XAtq -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT count(*) FROM ingest_sources WHERE router_mode<>chr(108)||chr(101)||chr(103)||chr(97)||chr(99)||chr(121)"' \
)" || die "could not verify post-deploy source modes"
[[ "$ACTIVE_COUNT" == "0" ]] || die "post-deploy source mode is not all Legacy"

docker compose -f "$COMPOSE_FILE" up -d --no-deps --no-build nexus-active-guard \
    || die "continuous Active supervisor did not start"
heartbeat=""
for _ in $(seq 1 30); do
    guard_state="$(docker inspect --format '{{.State.Status}}' nexus-active-guard 2>/dev/null || true)"
    heartbeat="$(docker compose -f "$COMPOSE_FILE" exec -T nexus-db sh -lc \
      'psql -XAtq -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT health_status FROM active_router_guard_state WHERE source_id=2 AND heartbeat_at>=NOW()-INTERVAL '\''90 seconds'\''"' 2>/dev/null || true)"
    if [[ "$guard_state" == running && "$heartbeat" == healthy ]]; then break; fi
    sleep 2
done
[[ "$(docker inspect --format '{{.State.Status}}' nexus-active-guard 2>/dev/null)" == running ]] \
    || die "continuous Active supervisor is not running"
[[ "$heartbeat" == healthy ]] || die "supervisor did not produce a healthy database heartbeat"
[[ "$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' nexus-active-guard)" == always ]] \
    || die "supervisor restart policy is not persistent across NAS reboot"
[[ "$(router_runtime_settings)" == "$PREDEPLOY_ROUTER_SETTINGS" ]] \
  || die "post-deploy Vision4/Router runtime settings changed"
docker compose -f "$COMPOSE_FILE" exec -T nexus-db sh -lc \
  'exec psql -XAtq -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT count(*) FROM vision4_model_versions"' \
  >/dev/null || die "model registry is unavailable after deployment"

cat > "$BACKUP_DIR/release-record.json" <<EOF
{
  "commit": "$COMMIT",
  "candidate_image": "$CANDIDATE_IMAGE",
  "candidate_image_id": "$CANDIDATE_ID",
  "production_base_image_id": "$CURRENT_ID",
  "rollback_image": "nexus-core:rollback-nexus-v3-unified-$STAMP",
  "bridge_image_id": "$BRIDGE_CANDIDATE_ID",
  "bridge_base_image_id": "$CURRENT_BRIDGE_ID",
  "bridge_rollback_image": "nexus-bridge:rollback-nexus-v3-unified-$STAMP",
  "backup_dir": "$BACKUP_DIR",
  "deployed_at_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "runtime_router_settings_unchanged": true,
  "all_sources_legacy": true
}
EOF
chmod 0600 "$BACKUP_DIR/release-record.json"
ROLLBACK_REQUIRED=0
printf 'RELEASE_OK commit=%s image_id=%s base=%s backup=%s\n' "$COMMIT" "$CANDIDATE_ID" "$CURRENT_ID" "$BACKUP_DIR"
