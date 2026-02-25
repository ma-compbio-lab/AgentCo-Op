#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_NAME="${ENV_NAME:-spatialbench}"
CONFIG_PATH="${CONFIG_PATH:-conf/config_w_api.yaml}"
EVAL_DIR="${EVAL_DIR:-third_party/spatialbench/evals_full}"
OUT_BASE="${OUT_BASE:-logs}"
RUN_TAG="${RUN_TAG:-spatialbench_full_baseline_$(date +%Y%m%d_%H%M%S)}"
OUT_DIR="${OUT_DIR:-${OUT_BASE}/${RUN_TAG}}"
MAX_SAMPLES="${MAX_SAMPLES:-}"  # empty => full set
CONCURRENCY="${CONCURRENCY:-8}"
REQUIRE_FULL="${REQUIRE_FULL:-true}"
PREFETCH_FIRST="${PREFETCH_FIRST:-true}"

mkdir -p "$OUT_DIR"

if [[ ! -d "$EVAL_DIR" ]]; then
  echo "ERROR: EVAL_DIR not found: $EVAL_DIR" >&2
  echo "Hint: SpatialBench full eval JSON files are not shipped in this repo." >&2
  echo "      Place your full eval set under a local directory and set EVAL_DIR=/path/to/evals_full" >&2
  exit 1
fi

TOTAL_EVALS=$(find "$EVAL_DIR" -type f -name '*.json' ! -name 'manifest.json' | wc -l | tr -d ' ')
if [[ "$REQUIRE_FULL" == "true" && "$TOTAL_EVALS" -le 10 ]]; then
  echo "ERROR: EVAL_DIR appears to be canonical/sample set ($TOTAL_EVALS evals)." >&2
  echo "Set REQUIRE_FULL=false if you intentionally want to run canonical examples." >&2
  exit 1
fi

if [[ "$PREFETCH_FIRST" == "true" ]]; then
  PREFETCH_ARGS=(--eval-dir "$EVAL_DIR" --summary "$OUT_DIR/prefetch_summary.json")
  if [[ -n "$MAX_SAMPLES" ]]; then
    PREFETCH_ARGS+=(--max-samples "$MAX_SAMPLES")
  fi
  conda run -n "$ENV_NAME" python eval/spatialbench_prefetch.py "${PREFETCH_ARGS[@]}"
fi

RUN_ARGS=()
if [[ -n "$MAX_SAMPLES" ]]; then
  RUN_ARGS+=(--max-samples "$MAX_SAMPLES")
fi

CONCURRENCY="$CONCURRENCY" \
EVAL_DIR="$EVAL_DIR" \
CONFIG_PATH="$CONFIG_PATH" \
OUT_DIR="$OUT_DIR" \
RESULT_PREFIX="baseline" \
bash scripts/run_spatialbench_baseline.sh "${RUN_ARGS[@]}" "$@"

echo
echo "Full SpatialBench baseline done:"
echo "  $OUT_DIR/baseline_summary.json"
echo "  $OUT_DIR/baseline_results.jsonl"
