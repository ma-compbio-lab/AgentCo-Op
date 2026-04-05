#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agentcoop.experiment_runner import (
    aggregate_humaneval_repeats,
    aggregate_math_repeats,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate already-aggregated benchmark repeats into AFlow-style 3-run summaries.")
    parser.add_argument("--benchmark", choices=("math", "humaneval"), required=True)
    parser.add_argument("--output-dir", default="runs/repeat-aggregated", help="Directory for repeat aggregate outputs.")
    parser.add_argument("run_dirs", nargs="+", help="Per-repeat aggregate run directories.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir).resolve() / args.benchmark
    if args.benchmark == "math":
        summary = aggregate_math_repeats(args.run_dirs, base_dir=output_dir)
    else:
        summary = aggregate_humaneval_repeats(args.run_dirs, base_dir=output_dir)
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
