#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(git -C "$ROOT_DIR" rev-parse --show-toplevel)"
cd "$REPO_ROOT"

BASE_IMAGE="${NEXUS_CORE_BASE_IMAGE:-nexus-core:v3-lifecycle-router-shadow-20261110}"
EXPECTED_BASE_ID="${NEXUS_CORE_BASE_IMAGE_ID:-sha256:9a6b125ee195a46a7ebd3ab3deca13e108053b321c16f40001acbc33a90ceb7e}"
CANDIDATE_IMAGE="${NEXUS_CORE_CANDIDATE_IMAGE:-nexus-core:active-router-release}"

die() { printf 'BLOCKED: %s\n' "$*" >&2; exit 1; }
[[ "$EXPECTED_BASE_ID" =~ ^sha256:[0-9a-f]{64}$ ]] || die "invalid expected base Image ID"
[[ -z "$(git status --porcelain)" ]] || die "release worktree must be clean before build"

ACTUAL_BASE_ID="$(docker image inspect --format '{{.Id}}' "$BASE_IMAGE" 2>/dev/null)" \
  || die "base image is not present locally: $BASE_IMAGE"
[[ "$ACTUAL_BASE_ID" == "$EXPECTED_BASE_ID" ]] \
  || die "base Image ID mismatch: expected $EXPECTED_BASE_ID, found $ACTUAL_BASE_ID"

COMMIT="$(git rev-parse HEAD)"
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
printf 'BUILT image=%s image_id=%s base_image_id=%s commit=%s\n' \
  "$CANDIDATE_IMAGE" "$BUILT_ID" "$ACTUAL_BASE_ID" "$COMMIT"
