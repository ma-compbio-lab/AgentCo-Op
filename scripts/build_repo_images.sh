#!/usr/bin/env bash
# Build sandboxed Docker images for wrapped case-study repos.
# Prerequisite: Docker daemon running (Docker Desktop on macOS).
#
# Usage:
#   ./scripts/build_repo_images.sh                 # build all case-study images
#   ./scripts/build_repo_images.sh geneagent       # one only
#   ./scripts/build_repo_images.sh gears
#
# Each target:
#   1. Verifies the pinned commit in configs/external_commits.yaml matches
#      external/<Repo>/.git/HEAD (warns on drift; TODO_PIN is allowed).
#   2. Copies external/<Repo>/ into a temp build context.
#   3. Copies the wrapper adapter + Dockerfile.
#   4. docker build -t agentcoop/<name>:<short_sha> -f Dockerfile.agentcoop .
#   5. Also tags agentcoop/<name>:latest.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXTERNAL="$ROOT/external"
WRAPPERS="$ROOT/agentcoop/wrappers"
COMMITS="$ROOT/configs/external_commits.yaml"

pick_commit() {
  local key="$1"
  python3 - "$key" <<'PY'
import sys, yaml
key = sys.argv[1]
with open("configs/external_commits.yaml") as f:
    d = yaml.safe_load(f)
print(d["repos"].get(key, {}).get("commit", "TODO_PIN"))
PY
}

build_one() {
  local repo_key="$1"        # geneagent / gears / scgpt / ...
  local external_name="$2"   # GeneAgent / GEARS / scGPT / ...
  local wrapper_dir="$3"     # path under agentcoop/wrappers
  local image_name="$4"      # agentcoop/<name>

  echo "=== $repo_key ==="
  local external_dir="$EXTERNAL/$external_name"
  if [[ ! -d "$external_dir" ]]; then
    echo "SKIP: $external_dir not cloned. Run: git clone <url> $external_dir"
    return 0
  fi
  if [[ ! -d "$wrapper_dir" ]]; then
    echo "SKIP: wrapper dir $wrapper_dir missing"
    return 0
  fi

  local head_sha
  head_sha=$(git -C "$external_dir" rev-parse HEAD)
  local pinned
  pinned=$(pick_commit "$repo_key")
  if [[ "$pinned" != "TODO_PIN" && "$head_sha" != "$pinned" ]]; then
    echo "WARNING: $repo_key HEAD ($head_sha) differs from pinned ($pinned)"
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
  geneagent|all)
    build_one geneagent GeneAgent "$WRAPPERS/geneagent" agentcoop/geneagent
    ;;
esac

case "$target" in
  gears|all)
    build_one gears GEARS "$WRAPPERS/gears" agentcoop/gears
    ;;
esac

case "$target" in
  scgpt|all)
    build_one scgpt scGPT "$WRAPPERS/scgpt" agentcoop/scgpt
    ;;
esac

case "$target" in
  scfoundation|all)
    build_one scfoundation scFoundation "$WRAPPERS/scfoundation" agentcoop/scfoundation
    ;;
esac

case "$target" in
  geneformer|all)
    build_one geneformer Geneformer "$WRAPPERS/geneformer" agentcoop/geneformer
    ;;
esac

echo "Built images:"
docker images | grep agentcoop || true
