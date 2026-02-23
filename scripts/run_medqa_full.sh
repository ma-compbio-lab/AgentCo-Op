#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_NAME="${ENV_NAME:-agent}"
CONFIG_PATH="${CONFIG_PATH:-conf/config_w_api.yaml}"
METHOD="${METHOD:-adaptive}"              # adaptive | orchestrated | baseline
SPLIT="${SPLIT:-test}"
CONCURRENCY="${CONCURRENCY:-8}"
MAX_SAMPLES="${MAX_SAMPLES:-}"            # optional; empty means full split
JSONL_DIR="${JSONL_DIR:-data/med_qa_repo/data_clean/questions/US}"
OUT_BASE="${OUT_BASE:-logs}"
RUN_TAG="${RUN_TAG:-medqa_full_${METHOD}_$(date +%Y%m%d_%H%M%S)}"
OUT_DIR="${OUT_BASE}/${RUN_TAG}"
PROGRESS_INTERVAL="${PROGRESS_INTERVAL:-2}"

mkdir -p "$OUT_DIR"

if [[ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]]; then
  # Common on your machine.
  source "$HOME/miniconda3/etc/profile.d/conda.sh"
elif command -v conda >/dev/null 2>&1; then
  # Fallback for other conda installations.
  eval "$(conda shell.bash hook)"
else
  echo "ERROR: conda not found. Please activate your Python env manually." >&2
  exit 1
fi

JSONL_ARGS=()
if [[ -d "$JSONL_DIR" ]]; then
  JSONL_ARGS=(--jsonl-dir "$JSONL_DIR")
else
  echo "WARN: local JSONL dir not found: $JSONL_DIR" >&2
  echo "      Falling back to eval script default source selection." >&2
fi

echo "Running MedQA full eval..."
echo "  env        : $ENV_NAME"
echo "  config     : $CONFIG_PATH"
echo "  method     : $METHOD"
echo "  split      : $SPLIT"
echo "  concurrency: $CONCURRENCY"
if [[ -n "$MAX_SAMPLES" ]]; then
  echo "  max-samples: $MAX_SAMPLES"
else
  echo "  max-samples: full split"
fi
echo "  output dir : $OUT_DIR"
echo "  progress   : external monitor (completed/total + acc_so_far)"

RESULTS_PATH="$OUT_DIR/results.jsonl"
SUMMARY_PATH="$OUT_DIR/summary.json"
FAILURES_PATH="$OUT_DIR/failures.csv"
RUN_LOG="$OUT_DIR/run.log"

TOTAL_SAMPLES=""
if [[ -n "$MAX_SAMPLES" ]]; then
  TOTAL_SAMPLES="$MAX_SAMPLES"
elif [[ -d "$JSONL_DIR" ]]; then
  SPLIT_FILE="$JSONL_DIR/${SPLIT}.jsonl"
  if [[ -f "$SPLIT_FILE" ]]; then
    TOTAL_SAMPLES="$(wc -l < "$SPLIT_FILE" | tr -d '[:space:]')"
  fi
fi

CMD=(
  conda run -n "$ENV_NAME" python eval/med_qa_eval.py
  --config "$CONFIG_PATH"
  --split "$SPLIT"
  --concurrency "$CONCURRENCY"
  --no-progress
  --summary "$SUMMARY_PATH"
  --output "$RESULTS_PATH"
  --failures-csv "$FAILURES_PATH"
  method="$METHOD"
  exec.enabled=false
)

if [[ "${#JSONL_ARGS[@]}" -gt 0 ]]; then
  CMD+=("${JSONL_ARGS[@]}")
fi

if [[ -n "$MAX_SAMPLES" ]]; then
  CMD+=(--max-samples "$MAX_SAMPLES")
fi

if [[ "$#" -gt 0 ]]; then
  CMD+=("$@")
fi

"${CMD[@]}" >"$RUN_LOG" 2>&1 &
EVAL_PID=$!

cleanup() {
  if kill -0 "$EVAL_PID" 2>/dev/null; then
    kill "$EVAL_PID" 2>/dev/null || true
  fi
}
trap cleanup INT TERM

print_progress() {
  local completed correct acc pct
  completed=0
  correct=0
  if [[ -f "$RESULTS_PATH" ]]; then
    completed="$(wc -l < "$RESULTS_PATH" | tr -d '[:space:]')"
    correct="$(grep -c '"ok": true' "$RESULTS_PATH" 2>/dev/null || true)"
  fi
  acc="$(awk -v c="$correct" -v n="$completed" 'BEGIN { if (n>0) printf "%.3f", c/n; else printf "0.000" }')"
  if [[ -n "$TOTAL_SAMPLES" ]]; then
    pct="$(awk -v c="$completed" -v t="$TOTAL_SAMPLES" 'BEGIN { if (t>0) printf "%.1f", (c*100)/t; else printf "0.0" }')"
    printf "\r[progress] %s/%s (%s%%) | acc_so_far=%s" "$completed" "$TOTAL_SAMPLES" "$pct" "$acc"
  else
    printf "\r[progress] %s/? | acc_so_far=%s" "$completed" "$acc"
  fi
}

while kill -0 "$EVAL_PID" 2>/dev/null; do
  print_progress
  sleep "$PROGRESS_INTERVAL"
done

set +e
wait "$EVAL_PID"
STATUS=$?
set -e

print_progress
echo

if [[ $STATUS -ne 0 ]]; then
  echo "MedQA eval failed. Showing last 120 log lines from $RUN_LOG" >&2
  tail -n 120 "$RUN_LOG" >&2 || true
  exit $STATUS
fi

echo
echo "Done. Artifacts:"
echo "  Summary : $SUMMARY_PATH"
echo "  Results : $RESULTS_PATH"
echo "  Failures: $FAILURES_PATH"
echo "  Run log : $RUN_LOG"
