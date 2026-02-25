#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

OUT_BASE="${OUT_BASE:-logs}"
RUN_ROOT="${RUN_ROOT:-spatialbench_compare_$(date +%Y%m%d_%H%M%S)}"
RUN_DIR="${RUN_DIR:-${OUT_BASE}/${RUN_ROOT}}"

echo "Running baseline + adaptive SpatialBench evals..."
echo "  run dir: $RUN_DIR"

mkdir -p "$RUN_DIR"

OUT_DIR="$RUN_DIR" RESULT_PREFIX="baseline" scripts/run_spatialbench_baseline.sh "$@"
OUT_DIR="$RUN_DIR" RESULT_PREFIX="adaptive" scripts/run_spatialbench_adaptive.sh "$@"

python scripts/build_spatialbench_report.py --run-dir "$RUN_DIR"

echo
echo "Artifacts (single folder):"
echo "  $RUN_DIR/baseline_summary.json"
echo "  $RUN_DIR/baseline_results.jsonl"
echo "  $RUN_DIR/adaptive_summary.json"
echo "  $RUN_DIR/adaptive_results.jsonl"
echo "  $RUN_DIR/overall_report.json"
echo "  $RUN_DIR/overall_report.md"
