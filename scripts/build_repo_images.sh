#!/usr/bin/env bash
# Build sandboxed Docker images for the wrapped repos.
# Prerequisite: Docker daemon running (Docker Desktop on macOS).
#
# Usage:
#   ./scripts/build_repo_images.sh                 # build both
#   ./scripts/build_repo_images.sh biodiscovery    # one only
#   ./scripts/build_repo_images.sh spatial
#
# The build context is assembled on the fly:
#   1. Verify the pinned commit in configs/external_commits.yaml matches
#      external/<Repo>/.git/HEAD.
#   2. Copy external/<Repo>/ into a temp dir as `repo/`.
#   3. Copy the wrapper adapter + Dockerfile.
#   4. docker build -t agentcoop/<name>:<short_sha> -f Dockerfile.agentcoop .
#   5. Tag as agentcoop/<name>:latest.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXTERNAL="$ROOT/external"
WRAPPERS="$ROOT/agentcoop/wrappers"
COMMITS="$ROOT/configs/external_commits.yaml"

pick_commit() {
  local key="$1"
  python3 -c "
import yaml, sys
with open('$COMMITS') as f:
  d = yaml.safe_load(f)
print(d['repos']['$key']['commit'])
"
}

build_one() {
  local key="$1"       # biodiscovery / spatial
  local external_dir="$2"
  local wrapper_dir="$3"
  local image_name="$4"

  echo "=== $key ==="
  if [[ ! -d "$external_dir" ]]; then
    echo "ERROR: $external_dir not cloned. Run: git clone ... external/$key"
    exit 1
  fi

  # Verify commit pin
  local head_sha
  head_sha=$(git -C "$external_dir" rev-parse HEAD)
  local pinned
  pinned=$(pick_commit "$key")
  if [[ "$pinned" != "NOT_PUBLIC" && "$head_sha" != "$pinned" ]]; then
    echo "WARNING: $key HEAD ($head_sha) differs from pinned ($pinned)"
    echo "         Update configs/external_commits.yaml or checkout the pin."
  fi

  local ctx
  ctx=$(mktemp -d)
  trap "rm -rf '$ctx'" RETURN
  cp -R "$external_dir" "$ctx/repo"
  cp "$wrapper_dir/adapter.py" "$ctx/adapter.py"
  cp "$wrapper_dir/Dockerfile.agentcoop" "$ctx/Dockerfile.agentcoop"

  local short="${head_sha:0:12}"
  echo "Building $image_name:$short from $ctx"
  ( cd "$ctx" && docker build \
    -t "$image_name:$short" \
    -t "$image_name:latest" \
    -f Dockerfile.agentcoop . )
  echo "Done: $image_name:$short"
}

target="${1:-all}"

case "$target" in
  biodiscovery|all)
    build_one biodiscovery_agent "$EXTERNAL/BioDiscoveryAgent" \
      "$WRAPPERS/biodiscovery" agentcoop/biodiscovery
    ;;
esac

case "$target" in
  spatial|all)
    build_one spatial_agent "$EXTERNAL/SpatialAgent" \
      "$WRAPPERS/spatialagent" agentcoop/spatialagent
    ;;
esac

echo "Built images:"
docker images | grep agentcoop || true
