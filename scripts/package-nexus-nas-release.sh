#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMMIT="$(git -C "$ROOT" rev-parse HEAD)"
[[ -z "$(git -C "$ROOT" status --porcelain)" ]] || {
  printf 'BLOCKED: commit all reviewed source changes before packaging\n' >&2
  exit 1
}

OUT_DIR="${1:-/private/tmp/nexus-v3-unified-release}"
mkdir -p "$OUT_DIR"
STAGE="$(mktemp -d "$OUT_DIR/.stage.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT

(cd "$ROOT/core/frontend" && npm ci --no-audit --no-fund && npm run build)

git -C "$ROOT" archive --format=tar "$COMMIT" \
  scripts/nexus-release-entrypoint.sh \
  scripts/build-nexus-bridge-overlay.sh \
  scripts/nexus_active_guard.py \
  scripts/test_nexus_active_guard.py \
  scripts/check_admin_feature_manifest.py \
  scripts/admin-feature-manifest.json \
  scripts/migrations \
  scripts/test_nexus_collection_plan.sql \
  scripts/test_nexus_checkin_stage.sql \
  scripts/test_nexus_bot_precheck.sql \
  scripts/test_nexus_collection_ocr_postgres.py \
  bridge/Dockerfile.release-overlay \
  bridge/runner \
  bridge/tests \
  core/app \
  core/tests \
  core/frontend \
  release/nexus-v3-20261110/BC/core/Dockerfile \
  release/nexus-v3-20261110/BC/scripts/build-core-release.sh \
  release/nexus-v3-20261110/BC/scripts/deploy-core-release.sh \
  release/nexus-v3-20261110/BC/scripts/release-core-unified.sh \
  | tar -xf - -C "$STAGE"
mkdir -p "$STAGE/core/frontend/dist"
cp -R "$ROOT/core/frontend/dist/." "$STAGE/core/frontend/dist/"
printf '%s\n' "$COMMIT" > "$STAGE/.nexus-release-commit"
cat > "$STAGE/README.txt" <<'EOF'
Nexus V3 one-shot NAS release package

Prerequisite: the NAS project must be /volume3/docker/nexus, the running
Core must be healthy and all sources must be Legacy when the release starts.
The build uses the live Core and Bridge Image IDs observed before building.
Docker Compose and PostgreSQL 16 client tools must be available.

After extracting this package on the NAS, run this single command from its
root directory:

  bash scripts/nexus-release-entrypoint.sh

The entrypoint verifies all package files before starting the release gate.
The release script will not restart or extend an Active Guard; it stops while
any source is Active. It snapshots Compose,
the current Core and Bridge images, database, and key queue/listing state.
It applies the listed additive migrations and deploys the canonical Core,
Bridge and Guard sources in one release. PostgreSQL, Vision4 weights, and
source rules are preserved. Failures restore the previous Compose, Core,
Bridge and Guard; forward-compatible database DDL is retained.

Keep this package and the printed backup directory until browser and API
acceptance is complete. Source modes and runtime settings are preserved.
EOF
(
  cd "$STAGE"
  find . -type f ! -name SHA256SUMS.source -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 sha256sum > SHA256SUMS.source
)
ARCHIVE="$OUT_DIR/nexus-v3-unified-$COMMIT.tar.gz"
tar -czf "$ARCHIVE" -C "$STAGE" .
sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"
printf 'PACKAGE_OK commit=%s archive=%s\n' "$COMMIT" "$ARCHIVE"
