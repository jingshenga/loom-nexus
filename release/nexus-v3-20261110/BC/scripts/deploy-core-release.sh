#!/usr/bin/env bash
set -Eeuo pipefail

# Run on the Nexus NAS project root after loading a labeled candidate image.
# Usage: scripts/deploy-core-release.sh <candidate-image> <git-commit> <image-id>

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
die() { printf 'BLOCKED: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "missing command: $1"; }

[[ $# -eq 3 ]] || die "usage: $0 <candidate-image> <git-commit> <image-id>"
CANDIDATE_IMAGE="$1"
RELEASE_COMMIT="$2"
EXPECTED_IMAGE_ID="$3"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT_DIR/docker-compose.yaml}"
BASELINE_FILE="${CORE_BASELINE_FILE:-$ROOT_DIR/.nexus-core-baseline}"

need docker
need python3
[[ -f "$COMPOSE_FILE" ]] || die "Compose file not found"
[[ "$RELEASE_COMMIT" =~ ^[0-9a-f]{40,64}$ ]] || die "release commit must be a full Git SHA"

CURRENT_IMAGE_ID="$(docker inspect --format '{{.Image}}' nexus-core 2>/dev/null)" || die "nexus-core is not running"
CURRENT_STATE="$(docker inspect --format '{{.State.Health.Status}}' nexus-core 2>/dev/null)" || die "cannot read Core health"
[[ "$CURRENT_STATE" == healthy ]] || die "current Core is not healthy"

if [[ ! -f "$BASELINE_FILE" ]]; then
  printf '%s\n' "$CURRENT_IMAGE_ID" > "$BASELINE_FILE" || die "cannot record production baseline"
fi
RECORDED_BASELINE="$(tr -d '[:space:]' < "$BASELINE_FILE")"
[[ "$CURRENT_IMAGE_ID" == "$RECORDED_BASELINE" ]] || die "running Core differs from recorded baseline"

docker image inspect "$CANDIDATE_IMAGE" >/dev/null 2>&1 || die "candidate image is not loaded locally"
CANDIDATE_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$CANDIDATE_IMAGE")"
[[ "$CANDIDATE_IMAGE_ID" == "$EXPECTED_IMAGE_ID" ]] || die "candidate Image ID mismatch"
[[ "$CANDIDATE_IMAGE_ID" != "$CURRENT_IMAGE_ID" ]] || die "candidate equals the running Core image"
LABEL_REVISION="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$CANDIDATE_IMAGE")"
LABEL_BASE="$(docker image inspect --format '{{ index .Config.Labels "io.nexus.base-image-id" }}' "$CANDIDATE_IMAGE")"
[[ "$LABEL_REVISION" == "$RELEASE_COMMIT" ]] || die "candidate is not labeled with the release commit"
[[ "$LABEL_BASE" == "$CURRENT_IMAGE_ID" ]] || die "candidate was not based on the current Core Image ID"

CURRENT_LAYERS="$(docker image inspect --format '{{json .RootFS.Layers}}' "$CURRENT_IMAGE_ID")"
CANDIDATE_LAYERS="$(docker image inspect --format '{{json .RootFS.Layers}}' "$CANDIDATE_IMAGE_ID")"
python3 - "$CURRENT_LAYERS" "$CANDIDATE_LAYERS" <<'PY' || die "candidate layers do not descend from current Core"
import json, sys
base, candidate = map(json.loads, sys.argv[1:])
assert len(candidate) > len(base) and candidate[:len(base)] == base
PY

docker run --rm --network none --entrypoint python "$CANDIDATE_IMAGE" -c '
from pathlib import Path
from app.main import app
from app.config import CoreSettings
routes = {(path, method.upper()) for path, operations in app.openapi()["paths"].items()
          for method in operations if method.lower() in {"get", "post", "put", "patch", "delete"}}
required = {
    ("/api/admin/rules/lifecycle-review", "GET"),
    ("/api/admin/rules/router-management", "GET"),
    ("/api/admin/rules/router-dryrun", "POST"),
    ("/api/admin/rules/shadow-diagnostics", "GET"),
    ("/api/admin/rules/active-review-queue", "GET"),
    ("/api/admin/rules/active-review-queue/{queue_id}", "GET"),
    ("/api/admin/rules/active-review-queue/{queue_id}/resolve", "POST"),
    ("/api/admin/rules/active-review-queue/{queue_id}/requeue", "POST"),
    ("/api/admin/models", "GET"),
    ("/api/admin/models/vision4/upload", "POST"),
    ("/api/admin/models/{version_id}/predict", "POST"),
    ("/api/admin/dashboard", "GET"),
    ("/api/admin/bots", "GET"),
    ("/api/admin/chat/sessions", "GET"),
}
assert not required - routes, f"missing API routes: {sorted(required - routes)}"
assert {"vision4_mode", "vision4_model_dir", "vision4_model_store_dir", "active_router_enabled", "active_router_source_ids", "active_router_kill_switch", "ocr_review_assist_enabled"} <= set(CoreSettings.model_fields), "Vision4, Active Router, or OCR review config missing"
settings = CoreSettings()
assert not settings.active_router_enabled and not settings.active_router_source_ids and settings.active_router_kill_switch, "Active Router must be globally disabled by default"
assert not settings.ocr_review_assist_enabled, "OCR review assist must be disabled by default"
assets = list(Path("/app/static").rglob("*.js"))
assert assets, "built frontend assets missing"
bundle = "\n".join(p.read_text(errors="ignore") for p in assets)
assert all(p in bundle for p in ("/admin", "/admin/models", "/admin/lifecycle", "/admin/rule-router", "/admin/rule-review")), "Admin/V3 frontend routes missing"
' >/dev/null || die "candidate lacks Lifecycle, Router, or Vision4 components"

COMPOSE_JSON="$(docker compose -f "$COMPOSE_FILE" config --format json)" || die "Compose configuration is invalid"
python3 - "$COMPOSE_JSON" "$CURRENT_IMAGE_ID" <<'PY' || die "Compose must pin the running Core image and must not build Core"
import json, subprocess, sys
cfg = json.loads(sys.argv[1])
core = cfg["services"]["nexus-core"]
assert not core.get("build"), "nexus-core build context must be removed"
assert core.get("image"), "nexus-core image must be pinned"
assert "NEXUS_VISION4_MODE" in core.get("environment", {}), "Vision4 mode setting missing"
assert "NEXUS_VISION4_MODEL_DIR" in core.get("environment", {}), "Vision4 model path missing"
assert any(str(m.get("target", "")).startswith("/app/data/models") and m.get("read_only") for m in core.get("volumes", [])), "Vision4 model mount must stay read-only"
environment = core.get("environment", {})
def environment_value(name):
    value = environment.get(name)
    return value.get("value") if isinstance(value, dict) else value
assert environment_value("NEXUS_VISION4_MODEL_STORE_DIR") == "/app/data/model-versions", "Vision4 model store path missing"
assert any(m.get("target") == "/app/data/model-versions" and not m.get("read_only") for m in core.get("volumes", [])), "dedicated Vision4 model version store must be writable"
enabled = str(environment_value("NEXUS_ACTIVE_ROUTER_ENABLED")).lower() in {"1", "true"}
allowed = [part.strip() for part in str(environment_value("NEXUS_ACTIVE_ROUTER_SOURCE_IDS") or "").split(",") if part.strip()]
kill = str(environment_value("NEXUS_ACTIVE_ROUTER_KILL_SWITCH")).lower() in {"1", "true"}
assert (not enabled and not allowed and kill) or (enabled and len(allowed) == 1 and not kill), "Active must be fully off or armed for exactly one source with kill switch off"
image_id = subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", core["image"]], text=True).strip()
assert image_id == sys.argv[2], "Compose image does not resolve to the running Core baseline"
PY

MISSING_OBJECTS="$(docker compose -f "$COMPOSE_FILE" exec -T nexus-db sh -lc \
  'exec psql -XAtq -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' 2>/dev/null <<'SQL'
BEGIN READ ONLY;
SET LOCAL statement_timeout='5s';
SELECT object_name FROM (VALUES
 ('table:rule_templates', to_regclass('public.rule_templates') IS NOT NULL),
 ('table:rule_template_versions', to_regclass('public.rule_template_versions') IS NOT NULL),
 ('table:rule_instance_versions', to_regclass('public.rule_instance_versions') IS NOT NULL),
 ('table:rule_parse_runs', to_regclass('public.rule_parse_runs') IS NOT NULL),
 ('table:rule_shadow_comparisons', to_regclass('public.rule_shadow_comparisons') IS NOT NULL),
 ('table:lifecycle_review_events', to_regclass('public.lifecycle_review_events') IS NOT NULL),
 ('table:rule_router_mode_events', to_regclass('public.rule_router_mode_events') IS NOT NULL),
 ('table:rule_shadow_error_counts', to_regclass('public.rule_shadow_error_counts') IS NOT NULL),
 ('table:rule_shadow_cleanup_runs', to_regclass('public.rule_shadow_cleanup_runs') IS NOT NULL),
 ('table:rule_active_review_queue', to_regclass('public.rule_active_review_queue') IS NOT NULL),
 ('table:rule_active_review_events', to_regclass('public.rule_active_review_events') IS NOT NULL),
 ('table:rule_active_ocr_rechecks', to_regclass('public.rule_active_ocr_rechecks') IS NOT NULL),
 ('table:vision4_model_versions', to_regclass('public.vision4_model_versions') IS NOT NULL),
 ('table:vision4_model_events', to_regclass('public.vision4_model_events') IS NOT NULL),
 ('safety:legacy_raw_requeue_trigger_absent', NOT EXISTS (
   SELECT 1 FROM pg_trigger t WHERE t.tgrelid=to_regclass('public.listing_media_ocr')
     AND t.tgname='trg_active_raw_after_ocr' AND NOT t.tgisinternal)),
 ('trigger:active_ocr_review_retry', EXISTS (
   SELECT 1 FROM pg_trigger t WHERE t.tgrelid=to_regclass('public.listing_media_ocr')
     AND t.tgname='trg_active_ocr_review_retry' AND NOT t.tgisinternal)),
   ('constraint:active_review_manual_requeue', EXISTS (
     SELECT 1 FROM pg_constraint c
     WHERE c.conrelid=to_regclass('public.rule_active_review_events')
       AND c.conname='rule_active_review_events_action_check'
       AND position('manual_requeue' IN pg_get_constraintdef(c.oid)) > 0)),
 ('constraint:active_review_manual_requeue', EXISTS (
   SELECT 1 FROM pg_constraint c
   WHERE c.conrelid=to_regclass('public.rule_active_review_events')
     AND c.conname='rule_active_review_events_action_check'
     AND position('manual_requeue' IN pg_get_constraintdef(c.oid)) > 0)),
 ('constraint:router_mode_previous_active', EXISTS (
   SELECT 1 FROM pg_constraint c
   WHERE c.conrelid=to_regclass('public.rule_router_mode_events')
     AND c.conname='rule_router_mode_events_previous_mode_check'
     AND position('active' IN pg_get_constraintdef(c.oid)) > 0)),
 ('constraint:router_mode_new_active', EXISTS (
   SELECT 1 FROM pg_constraint c
   WHERE c.conrelid=to_regclass('public.rule_router_mode_events')
     AND c.conname='rule_router_mode_events_new_mode_check'
     AND position('active' IN pg_get_constraintdef(c.oid)) > 0)),
 ('function:rule_upsert_versioned', to_regproc('public.rule_upsert_versioned') IS NOT NULL),
 ('function:rule_router_mode_set', to_regproc('public.rule_router_mode_set') IS NOT NULL),
 ('function:rule_shadow_bindings_for_source', to_regproc('public.rule_shadow_bindings_for_source') IS NOT NULL),
 ('function:lifecycle_review_resolve', to_regproc('public.lifecycle_review_resolve') IS NOT NULL)
) AS required(object_name, present) WHERE NOT present;
ROLLBACK;
SQL
)" || die "read-only schema compatibility check failed"
[[ -z "$MISSING_OBJECTS" ]] || die "V3 schema objects missing: $MISSING_OBJECTS"
ACTIVE_SOURCES="$(docker compose -f "$COMPOSE_FILE" exec -T nexus-db sh -lc \
  'exec psql -XAtq -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT id FROM ingest_sources WHERE router_mode=chr(97)||chr(99)||chr(116)||chr(105)||chr(118)||chr(101) ORDER BY id"' 2>/dev/null)" \
  || die "could not verify source modes"
[[ -z "$ACTIVE_SOURCES" ]] || die "Active Router is enabled for sources: $ACTIVE_SOURCES"

PREVIOUS_REF="$(docker inspect --format '{{.Config.Image}}' nexus-core)"
PREVIOUS_ID="$CURRENT_IMAGE_ID"
ROLLBACK_TAG="nexus-core:rollback-$(date -u +%Y%m%dT%H%M%SZ)"
docker image tag "$PREVIOUS_ID" "$ROLLBACK_TAG"
BACKUP_FILE="${COMPOSE_FILE}.before-core-$(date -u +%Y%m%dT%H%M%SZ).bak"
cp -p "$COMPOSE_FILE" "$BACKUP_FILE" || die "Compose backup failed"

python3 - "$COMPOSE_FILE" "$CANDIDATE_IMAGE" <<'PY' || die "could not update only the nexus-core image reference"
import os, re, sys, tempfile
path, image = sys.argv[1:]
text = open(path, encoding="utf-8").read()
match = re.search(r"(?ms)^  nexus-core:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|^networks:\n|\Z)", text)
assert match, "nexus-core service block not found"
section = match.group(1)
section2, count = re.subn(r"(?m)^(    image:\s*)\S+\s*$", lambda m: m.group(1) + image, section)
assert count == 1, "expected exactly one Core image line"
env_match = re.search(r"(?ms)^    environment:\n(.*?)(?=^    [A-Za-z0-9_-]+:\n|\Z)", section2)
assert env_match, "Core environment mapping not found"
env = env_match.group(1)
setting = "      NEXUS_VISION4_MODEL_STORE_DIR: /app/data/model-versions\n"
if "NEXUS_VISION4_MODEL_STORE_DIR:" in env:
    assert re.search(r"(?m)^      NEXUS_VISION4_MODEL_STORE_DIR:\s*/app/data/model-versions\s*$", env), "model store path differs from the release contract"
else:
    section2 = section2[:env_match.end(1)] + setting + section2[env_match.end(1):]
volume_match = re.search(r"(?ms)^    volumes:\n(.*?)(?=^    [A-Za-z0-9_-]+:\n|\Z)", section2)
assert volume_match, "Core volume mapping not found"
volumes = volume_match.group(1)
mount = "      - ${NEXUS_DATA_DIR:-./data}/model-versions:/app/data/model-versions:rw\n"
if "/app/data/model-versions" in volumes:
    assert re.search(r"(?m)^      - .*:/app/data/model-versions:rw\s*$", volumes), "model store mount must be writable and dedicated"
else:
    section2 = section2[:volume_match.end(1)] + mount + section2[volume_match.end(1):]
updated = text[:match.start(1)] + section2 + text[match.end(1):]
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".compose-core-")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    f.write(updated); f.flush(); os.fsync(f.fileno())
os.chmod(tmp, os.stat(path).st_mode)
os.replace(tmp, path)
PY

mkdir -p "$ROOT_DIR/data/model-versions" || { cp -p "$BACKUP_FILE" "$COMPOSE_FILE"; die "model version directory creation failed"; }
chmod 0750 "$ROOT_DIR/data/model-versions" || { cp -p "$BACKUP_FILE" "$COMPOSE_FILE"; die "model version directory permissions failed"; }

rollback() {
  printf 'Rolling back Core to %s (%s)\n' "$PREVIOUS_REF" "$PREVIOUS_ID" >&2
  cp -p "$BACKUP_FILE" "$COMPOSE_FILE" || die "rollback failed: Compose restore failed"
  docker compose -f "$COMPOSE_FILE" up -d --no-deps --no-build nexus-core >&2 \
    || die "rollback failed: Core restore command failed"
  for _ in $(seq 1 60); do
    state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' nexus-core 2>/dev/null || true)"
    image_id="$(docker inspect --format '{{.Image}}' nexus-core 2>/dev/null || true)"
    if [[ "$state" == healthy && "$image_id" == "$PREVIOUS_ID" ]]; then
      printf 'ROLLBACK_OK image_id=%s compose=%s\n' "$image_id" "$BACKUP_FILE" >&2
      return 0
    fi
    if [[ "$state" == exited || "$state" == dead ]]; then break; fi
    sleep 2
  done
  die "rollback failed: previous Core image did not become healthy"
}

if ! docker compose -f "$COMPOSE_FILE" config --quiet; then rollback; die "candidate Compose validation failed"; fi
if ! docker compose -f "$COMPOSE_FILE" up -d --no-deps --no-build nexus-core; then rollback; die "Core deployment command failed"; fi

healthy=0
for _ in $(seq 1 60); do
  state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' nexus-core 2>/dev/null || true)"
  if [[ "$state" == healthy ]]; then healthy=1; break; fi
  if [[ "$state" == exited || "$state" == dead ]]; then break; fi
  sleep 2
done
if [[ $healthy -ne 1 ]]; then rollback; die "new Core did not become healthy"; fi
DEPLOYED_IMAGE_ID="$(docker inspect --format '{{.Image}}' nexus-core)"
if [[ "$DEPLOYED_IMAGE_ID" != "$CANDIDATE_IMAGE_ID" ]]; then rollback; die "running Core Image ID does not match candidate"; fi

if ! docker compose -f "$COMPOSE_FILE" exec -T nexus-core python - <<'PY'
import json, os, urllib.error, urllib.request
from pathlib import Path
base = "http://127.0.0.1:8000"
assert urllib.request.urlopen(base + "/api/health", timeout=5).status == 200
for path in ("/admin", "/admin/models", "/admin/lifecycle", "/admin/rule-router", "/admin/rule-review"):
    response = urllib.request.urlopen(base + path, timeout=5)
    assert response.status == 200 and "<html" in response.read(65536).decode("utf-8", "ignore").lower()
for path in ("/api/admin/rules/lifecycle-review", "/api/admin/rules/router-management", "/api/admin/rules/shadow-diagnostics", "/api/admin/models", "/api/admin/dashboard"):
    try:
        response = urllib.request.urlopen(base + path, timeout=5)
        assert response.status == 200
    except urllib.error.HTTPError as exc:
        assert exc.code in (401, 403), f"{path}: unexpected HTTP {exc.code}"
spec = json.load(urllib.request.urlopen(base + "/openapi.json", timeout=5))
assert "post" in spec["paths"]["/api/admin/rules/router-dryrun"]
assert os.getenv("NEXUS_VISION4_MODE") is not None
assert Path(os.getenv("NEXUS_VISION4_MODEL_DIR", "/app/data/models/vision-model-v1")).exists()
PY
then
  rollback
  die "post-deploy smoke test failed"
fi

BASELINE_TMP="${BASELINE_FILE}.tmp"
if ! printf '%s\n' "$CANDIDATE_IMAGE_ID" > "$BASELINE_TMP" || ! mv "$BASELINE_TMP" "$BASELINE_FILE"; then
  rollback
  die "could not record new Core baseline"
fi
printf 'RELEASED image=%s image_id=%s commit=%s previous=%s rollback_tag=%s compose_backup=%s\n' \
  "$CANDIDATE_IMAGE" "$CANDIDATE_IMAGE_ID" "$RELEASE_COMMIT" "$PREVIOUS_ID" "$ROLLBACK_TAG" "$BACKUP_FILE"
