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

git -C "$ROOT" archive --format=tar "$COMMIT" \
  scripts/nexus-release-entrypoint.sh \
  scripts/check_admin_feature_manifest.py \
  scripts/admin-feature-manifest.json \
  scripts/migrations \
  core/app \
  core/tests \
  core/frontend \
  release/nexus-v3-20261110/BC/core/Dockerfile \
  release/nexus-v3-20261110/BC/scripts/build-core-release.sh \
  release/nexus-v3-20261110/BC/scripts/deploy-core-release.sh \
  release/nexus-v3-20261110/BC/scripts/release-core-unified.sh \
  | tar -xf - -C "$STAGE"
printf '%s\n' "$COMMIT" > "$STAGE/.nexus-release-commit"
cat > "$STAGE/README.txt" <<'EOF'
Nexus V3 one-shot NAS release package

Prerequisite: the NAS project must be /volume3/docker/nexus, the running
Core must be healthy and all sources must be Legacy when the release starts.
The build uses the live Core Image ID observed immediately before building.
Docker Compose and PostgreSQL 16 client tools must be available.

After extracting this package on the NAS, run this single command from its
root directory:

  bash scripts/nexus-release-entrypoint.sh

The entrypoint verifies all package files before starting the release gate.
The release script will not restart or extend an Active Guard; it stops while
any source is Active. It snapshots Compose,
the current Core image, database, and key queue/listing state, then applies
only the listed idempotent migrations and updates Core from the canonical
core/app and core/frontend sources. It does not rebuild
Bridge or PostgreSQL, change Vision4 weights, enable Active, or modify
production rules and Listings. Failures after Core replacement restore the
previous Compose and Core image. Database DDL is intentionally retained on
rollback because it is forward-compatible.

Keep this package and the printed backup directory until browser and API
acceptance is complete. Active and OCR assist remain disabled by default.
EOF
(
  cd "$STAGE"
  find . -type f ! -name SHA256SUMS.source ! -name .nexus-release-commit -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 sha256sum > SHA256SUMS.source
)
ARCHIVE="$OUT_DIR/nexus-v3-unified-$COMMIT.tar.gz"
tar -czf "$ARCHIVE" -C "$STAGE" .
sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"
printf 'PACKAGE_OK commit=%s archive=%s\n' "$COMMIT" "$ARCHIVE"
