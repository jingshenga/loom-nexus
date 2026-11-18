#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE_REF="$1"
BASE_ID="$2"
CANDIDATE="$3"
COMMIT="$4"
[[ "$BASE_ID" =~ ^sha256:[0-9a-f]{64}$ && "$COMMIT" =~ ^[0-9a-f]{40}$ ]]
[[ "$CANDIDATE" == nexus-bridge:collection-reuse-* && "$CANDIDATE" != "$BASE_REF" ]]
[[ "$(docker image inspect --format '{{.Id}}' "$BASE_REF")" == "$BASE_ID" ]]
PLATFORM="$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$BASE_REF")"

docker build --platform "$PLATFORM" --pull=false -f "$ROOT/bridge/Dockerfile.release-overlay" \
    --build-arg BASE_IMAGE="$BASE_REF" --build-arg BASE_IMAGE_ID="$BASE_ID" \
    --build-arg SOURCE_COMMIT="$COMMIT" -t "$CANDIDATE" "$ROOT"

python3 - "$BASE_ID" "$CANDIDATE" "$COMMIT" <<'PY'
import json
import subprocess
import sys

base_id, candidate_ref, commit = sys.argv[1:]
base, candidate = json.loads(subprocess.check_output(["docker", "image", "inspect", base_id, candidate_ref]))
labels = candidate["Config"]["Labels"]
if labels.get("org.opencontainers.image.revision") != commit or labels.get("io.nexus.base-image-id") != base_id:
    raise SystemExit("Bridge candidate provenance mismatch")
layers = base["RootFS"]["Layers"]
if candidate["RootFS"]["Layers"][:len(layers)] != layers:
    raise SystemExit("Bridge candidate does not inherit production layers")
print("BRIDGE_ROOTFS_OK", candidate["Id"])
PY

docker run --rm --platform "$PLATFORM" --network none --entrypoint python \
    -v "$ROOT/bridge/tests:/nexus-tests:ro" "$CANDIDATE" -m unittest discover -s /nexus-tests
