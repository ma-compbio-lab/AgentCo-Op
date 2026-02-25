#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_NAME="${ENV_NAME:-spatialbench}"
CONFIG_PATH="${CONFIG_PATH:-conf/config_w_api.yaml}"
EVAL_DIR="${EVAL_DIR:-third_party/spatialbench/evals_canonical}"
OUT_BASE="${OUT_BASE:-logs}"
RUN_TAG="${RUN_TAG:-spatialbench_adaptive_$(date +%Y%m%d_%H%M%S)}"
OUT_DIR="${OUT_DIR:-${OUT_BASE}/${RUN_TAG}}"
MAX_SAMPLES="${MAX_SAMPLES:-}"  # empty => full set
CONCURRENCY="${CONCURRENCY:-1}"
RESULT_PREFIX="${RESULT_PREFIX:-}"

if [[ -n "$RESULT_PREFIX" ]]; then
  RESULTS_FILE="${OUT_DIR}/${RESULT_PREFIX}_results.jsonl"
  SUMMARY_FILE="${OUT_DIR}/${RESULT_PREFIX}_summary.json"
else
  RESULTS_FILE="${OUT_DIR}/results.jsonl"
  SUMMARY_FILE="${OUT_DIR}/summary.json"
fi

mkdir -p "$OUT_DIR"

CMD=(
  conda run -n "$ENV_NAME" python eval/spatialbench_eval.py
  --config "$CONFIG_PATH"
  --eval-dir "$EVAL_DIR"
  --output "$RESULTS_FILE"
  --summary "$SUMMARY_FILE"
  --continue-on-error
  --concurrency "$CONCURRENCY"
  method=adaptive
)

if [[ -n "$MAX_SAMPLES" ]]; then
  CMD+=(--max-samples "$MAX_SAMPLES")
fi

if [[ "$#" -gt 0 ]]; then
  CMD+=("$@")
fi

echo "Running SpatialBench adaptive..."
echo "  env        : $ENV_NAME"
echo "  config     : $CONFIG_PATH"
echo "  eval dir   : $EVAL_DIR"
echo "  output dir : $OUT_DIR"
echo "  max-samples: ${MAX_SAMPLES:-full}"
echo "  concurrency: $CONCURRENCY"
if [[ -n "$RESULT_PREFIX" ]]; then
  echo "  prefix     : $RESULT_PREFIX"
fi

"${CMD[@]}"

echo
echo "Done. Artifacts:"
echo "  $SUMMARY_FILE"
echo "  $RESULTS_FILE"
