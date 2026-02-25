#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

OUT_BASE="${OUT_BASE:-logs}"
RUN_ROOT="${RUN_ROOT:-spatialbench_full_compare_$(date +%Y%m%d_%H%M%S)}"
RUN_DIR="${RUN_DIR:-${OUT_BASE}/${RUN_ROOT}}"

echo "Running full SpatialBench baseline + adaptive..."
echo "  run dir: $RUN_DIR"

mkdir -p "$RUN_DIR"

OUT_DIR="$RUN_DIR" bash scripts/run_spatialbench_full_baseline.sh "$@"
OUT_DIR="$RUN_DIR" PREFETCH_FIRST=false bash scripts/run_spatialbench_full_adaptive.sh "$@"

python scripts/build_spatialbench_report.py --run-dir "$RUN_DIR"

echo
echo "Artifacts (single folder):"
if [[ -f "$RUN_DIR/prefetch_summary.json" ]]; then
  echo "  $RUN_DIR/prefetch_summary.json"
fi
echo "  $RUN_DIR/baseline_summary.json"
echo "  $RUN_DIR/baseline_results.jsonl"
echo "  $RUN_DIR/adaptive_summary.json"
echo "  $RUN_DIR/adaptive_results.jsonl"
echo "  $RUN_DIR/overall_report.json"
echo "  $RUN_DIR/overall_report.md"
