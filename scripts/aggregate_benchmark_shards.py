#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dynaforge.experiment_runner import (
    aggregate_humaneval_runs,
    aggregate_math_runs,
    aggregate_medqa_runs,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate the latest shard runs for a benchmark full run.")
    parser.add_argument("--benchmark", choices=("medqa", "math", "humaneval"), required=True)
    parser.add_argument("--runs-root", default="runs", help="Root directory containing benchmark shard runs.")
    parser.add_argument("--subset", default="", help="Subset name. Defaults to full for MedQA and test otherwise.")
    parser.add_argument("--output-dir", default="runs/aggregated", help="Directory for aggregated output.")
    return parser.parse_args()


def discover_latest_shards(runs_root: Path, benchmark: str, subset: str) -> list[Path]:
    pattern = f"{benchmark}-{subset}-shard*of*/*/eval/task_results.json"
    if benchmark == "medqa":
        pattern = f"{benchmark}-{subset}-shard*of*/*/eval/task_results.json"
    latest_by_slug: dict[str, tuple[str, Path]] = {}
    for task_results_path in runs_root.glob(pattern):
        run_dir = task_results_path.parents[1]
        shard_slug = task_results_path.parts[-4]
        timestamp = task_results_path.parts[-3]
        current = latest_by_slug.get(shard_slug)
        if current is None or timestamp > current[0]:
            latest_by_slug[shard_slug] = (timestamp, run_dir)
    return [run_dir for _, run_dir in sorted(latest_by_slug.values(), key=lambda item: item[0])]


def main() -> int:
    args = parse_args()
    runs_root = Path(args.runs_root).resolve()
    subset = args.subset or ("full" if args.benchmark == "medqa" else "test")

    aggregate_fn: Callable[..., dict]
    if args.benchmark == "medqa":
        aggregate_fn = aggregate_medqa_runs
    elif args.benchmark == "math":
        aggregate_fn = aggregate_math_runs
    else:
        aggregate_fn = aggregate_humaneval_runs

    run_dirs = discover_latest_shards(runs_root, args.benchmark, subset)
    if not run_dirs:
        raise SystemExit(f"No shard runs found for {args.benchmark} subset={subset} under {runs_root}")

    output_dir = Path(args.output_dir).resolve() / args.benchmark / subset
    summary = aggregate_fn([str(path) for path in run_dirs], base_dir=output_dir)
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
