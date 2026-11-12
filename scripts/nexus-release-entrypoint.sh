#!/usr/bin/env bash
set -Eeuo pipefail

PACKAGE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PACKAGE_DIR"

[[ -f .nexus-release-commit && -f SHA256SUMS.source ]] || {
  printf 'BLOCKED: release marker or source checksum list is missing\n' >&2
  exit 1
}
sha256sum -c SHA256SUMS.source
COMMIT="$(tr -d '[:space:]' < .nexus-release-commit)"
[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]] || {
  printf 'BLOCKED: release commit marker is invalid\n' >&2
  exit 1
}
export NEXUS_RELEASE_COMMIT="$COMMIT"
exec bash release/nexus-v3-20261110/BC/scripts/release-core-unified.sh
