from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.med_qa_eval import run_eval


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate Agent-Cop baseline (single agent) on MedQA."
    )
    parser.add_argument("--config", default="conf/config_w_api.yaml")
    parser.add_argument("--split", default="test")
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--config-name", default=None, help="Dataset config name (default: bigbio_qa)")
    parser.add_argument("--data-dir", default=None, help="Use a locally saved dataset (load_from_disk).")
    parser.add_argument(
        "--jsonl-dir",
        default=None,
        help="Use a local MedQA JSONL folder (e.g., data_clean/questions/US).",
    )
    parser.add_argument(
        "--prefer-local-jsonl",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Auto-use data/med_qa_repo/data_clean/questions/US when present.",
    )
    parser.add_argument(
        "--progress",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Show a single-line progress bar.",
    )
    parser.add_argument("--max-samples", type=int, default=None)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--output", default="logs/med_qa_results.jsonl")
    parser.add_argument("--summary", default="logs/med_qa_summary.json")
    parser.add_argument("--failures-csv", default="logs/med_qa_failures.csv")
    parser.add_argument("overrides", nargs="*", help="Hydra-style overrides (key=value)")
    args = parser.parse_args()

    overrides = list(args.overrides or [])
    overrides.append("method=baseline")
    args.overrides = overrides
    asyncio.run(run_eval(args))


if __name__ == "__main__":
    main()
