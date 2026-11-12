#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BASE_IMAGE="${NEXUS_CORE_BASE_IMAGE:-nexus-core:v3-lifecycle-router-shadow-20261110}"
EXPECTED_BASE_ID="${NEXUS_CORE_BASE_IMAGE_ID:-sha256:9a6b125ee195a46a7ebd3ab3deca13e108053b321c16f40001acbc33a90ceb7e}"
CANDIDATE_IMAGE="${NEXUS_CORE_CANDIDATE_IMAGE:-nexus-core:active-router-release}"

die() { printf 'BLOCKED: %s\n' "$*" >&2; exit 1; }
[[ "$EXPECTED_BASE_ID" =~ ^sha256:[0-9a-f]{64}$ ]] || die "invalid expected base Image ID"

if git -C "$ROOT_DIR" rev-parse --show-toplevel >/dev/null 2>&1; then
  REPO_ROOT="$(git -C "$ROOT_DIR" rev-parse --show-toplevel)"
  cd "$REPO_ROOT"
  [[ -z "$(git status --porcelain)" ]] || die "release worktree must be clean before build"
  COMMIT="$(git rev-parse HEAD)"
  [[ -z "${NEXUS_RELEASE_COMMIT:-}" || "$NEXUS_RELEASE_COMMIT" == "$COMMIT" ]] \
    || die "requested release commit does not match checked out source"
else
  REPO_ROOT="$(cd "$ROOT_DIR/../../.." && pwd)"
  [[ -n "${NEXUS_RELEASE_COMMIT:-}" ]] || die "archive builds require NEXUS_RELEASE_COMMIT"
  [[ -f "$REPO_ROOT/.nexus-release-commit" && -f "$REPO_ROOT/SHA256SUMS.source" ]] \
    || die "source archive commit marker or checksum manifest missing"
  COMMIT="$(tr -d '[:space:]' < "$REPO_ROOT/.nexus-release-commit")"
  [[ "$COMMIT" == "$NEXUS_RELEASE_COMMIT" && "$COMMIT" =~ ^[0-9a-f]{40}$ ]] \
    || die "source archive commit does not match requested release commit"
  (cd "$REPO_ROOT" && sha256sum -c SHA256SUMS.source) \
    || die "source archive checksum verification failed"
fi

ACTUAL_BASE_ID="$(docker image inspect --format '{{.Id}}' "$BASE_IMAGE" 2>/dev/null)" \
  || die "base image is not present locally: $BASE_IMAGE"
[[ "$ACTUAL_BASE_ID" == "$EXPECTED_BASE_ID" ]] \
  || die "base Image ID mismatch: expected $EXPECTED_BASE_ID, found $ACTUAL_BASE_ID"

docker build --platform linux/amd64 --pull=false \
  --build-arg "BASE_IMAGE=$BASE_IMAGE" \
  --build-arg "BASE_IMAGE_ID=$ACTUAL_BASE_ID" \
  --build-arg "SOURCE_COMMIT=$COMMIT" \
  -f release/nexus-v3-20261110/BC/core/Dockerfile \
  -t "$CANDIDATE_IMAGE" \
  release/nexus-v3-20261110/BC

BUILT_ID="$(docker image inspect --format '{{.Id}}' "$CANDIDATE_IMAGE")"
LABEL_COMMIT="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$CANDIDATE_IMAGE")"
LABEL_BASE="$(docker image inspect --format '{{ index .Config.Labels "io.nexus.base-image-id" }}' "$CANDIDATE_IMAGE")"
[[ "$LABEL_COMMIT" == "$COMMIT" ]] || die "built image commit label mismatch"
[[ "$LABEL_BASE" == "$ACTUAL_BASE_ID" ]] || die "built image base label mismatch"
BASE_LAYERS="$(docker image inspect --format '{{json .RootFS.Layers}}' "$BASE_IMAGE")"
CANDIDATE_LAYERS="$(docker image inspect --format '{{json .RootFS.Layers}}' "$CANDIDATE_IMAGE")"
python3 - "$BASE_LAYERS" "$CANDIDATE_LAYERS" <<'PY' || die "candidate layers do not inherit the verified base image"
import json, sys
base, candidate = map(json.loads, sys.argv[1:])
assert len(candidate) > len(base) and candidate[:len(base)] == base
PY
docker run --rm --network none --entrypoint python "$CANDIDATE_IMAGE" -c '
from pathlib import Path
from app.config import CoreSettings
from app.extractor.media_ocr import MediaOcrProcessor
from app.extractor.vision4_classifier import Vision4Classifier
from app.main import app
paths = app.openapi()["paths"]
required = {
    "/api/admin/rules/lifecycle-review",
    "/api/admin/rules/router-management",
    "/api/admin/rules/router-dryrun",
    "/api/admin/rules/shadow-diagnostics",
    "/api/admin/rules/active-review-queue",
    "/api/admin/rules/active-review-queue/{queue_id}/requeue",
}
assert required <= set(paths), f"missing V3 API: {sorted(required - set(paths))}"
settings = CoreSettings()
assert not settings.active_router_enabled and not settings.active_router_source_ids and settings.active_router_kill_switch
assert MediaOcrProcessor and Vision4Classifier
assets = list(Path("/app/static").rglob("*.js"))
assert assets
bundle = "\n".join(p.read_text(errors="ignore") for p in assets)
assert all(path in bundle for path in ("/admin/lifecycle", "/admin/rule-router", "/admin/rule-review"))
' >/dev/null || die "candidate is missing required V3, Vision4, or default-off Active components"
printf 'BUILT image=%s image_id=%s base_image_id=%s commit=%s\n' \
  "$CANDIDATE_IMAGE" "$BUILT_ID" "$ACTUAL_BASE_ID" "$COMMIT"
