from __future__ import annotations

import argparse
import json
import sys

from dynaforge.config import load_hydra_config
from dynaforge.experiment_runner import aggregate_medqa_runs, run_medqa_experiment, run_stage0_validation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dynaforge-exp")
    subparsers = parser.add_subparsers(dest="command", required=True)

    stage0 = subparsers.add_parser("stage0")
    stage0.add_argument("--base-dir", default="runs")
    stage0.add_argument("--medqa-model", default="openai_gpt5_mini")
    stage0.add_argument("--medqa-smoke-limit", type=int, default=1)
    stage0.add_argument("overrides", nargs="*")

    medqa = subparsers.add_parser("medqa")
    medqa.add_argument("--experiment", default="medqa", help="Hydra experiment config name to compose")
    medqa.add_argument("--subset", choices=("smoke", "full"), default="smoke")
    medqa.add_argument("--limit", type=int, default=None)
    medqa.add_argument(
        "--task-ids",
        default="",
        help="Comma-separated MedQA task IDs or question IDs for focused reruns.",
    )
    medqa.add_argument("--num-shards", type=int, default=1)
    medqa.add_argument("--shard-index", type=int, default=0)
    medqa.add_argument(
        "--resume-run-dir",
        default="",
        help="Reuse an existing MedQA run directory and skip already-completed task results.",
    )
    medqa.add_argument("--base-dir", default="runs")
    medqa.add_argument("--model", default="openai_gpt5_mini")
    medqa.add_argument("overrides", nargs="*")

    medqa_aggregate = subparsers.add_parser("medqa-aggregate")
    medqa_aggregate.add_argument("--base-dir", default="runs")
    medqa_aggregate.add_argument("run_dirs", nargs="+")

    args = parser.parse_args(argv)
    if args.command == "stage0":
        medqa_cfg = load_hydra_config(overrides=["experiment=medqa", f"model={args.medqa_model}", *args.overrides])
        spatialbench_cfg = load_hydra_config(
            overrides=["experiment=spatialbench", f"model={args.medqa_model}", *args.overrides]
        )
        summary = run_stage0_validation(
            medqa_cfg,
            spatialbench_cfg,
            base_dir=args.base_dir,
            medqa_smoke_limit=args.medqa_smoke_limit,
        )
        print(json.dumps(summary, ensure_ascii=True, indent=2))
        return 0 if summary.get("success") else 1

    if args.command == "medqa":
        cfg = load_hydra_config(overrides=[f"experiment={args.experiment}", f"model={args.model}", *args.overrides])
        task_ids = [item.strip() for item in args.task_ids.split(",") if item.strip()]
        summary = run_medqa_experiment(
            cfg,
            subset=args.subset,
            limit=args.limit,
            task_ids=task_ids,
            num_shards=args.num_shards,
            shard_index=args.shard_index,
            base_dir=args.base_dir,
            resume_run_dir=args.resume_run_dir or None,
        )
        print(json.dumps(summary, ensure_ascii=True, indent=2))
        return 0 if summary.get("task_count", 0) > 0 else 1

    if args.command == "medqa-aggregate":
        summary = aggregate_medqa_runs(args.run_dirs, base_dir=args.base_dir)
        print(json.dumps(summary, ensure_ascii=True, indent=2))
        return 0 if summary.get("task_count", 0) > 0 else 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
