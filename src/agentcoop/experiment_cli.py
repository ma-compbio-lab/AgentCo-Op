from __future__ import annotations

import argparse
import json
import sys

from agentcoop.config import load_hydra_config
from agentcoop.experiment_runner import (
    aggregate_humaneval_repeats,
    aggregate_humaneval_runs,
    aggregate_math_repeats,
    aggregate_math_runs,
    run_case_study_experiment,
    run_humaneval_experiment,
    run_math_experiment,
    run_stage0_validation,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentcoop-exp")
    subparsers = parser.add_subparsers(dest="command", required=True)

    stage0 = subparsers.add_parser("stage0")
    stage0.add_argument("--base-dir", default="runs")
    stage0.add_argument("--benchmark-model", default="openai_gpt4o_mini")
    stage0.add_argument("overrides", nargs="*")

    math_aggregate = subparsers.add_parser("math-aggregate")
    math_aggregate.add_argument("--base-dir", default="runs")
    math_aggregate.add_argument("run_dirs", nargs="+")

    humaneval_aggregate = subparsers.add_parser("humaneval-aggregate")
    humaneval_aggregate.add_argument("--base-dir", default="runs")
    humaneval_aggregate.add_argument("run_dirs", nargs="+")

    math_repeat_aggregate = subparsers.add_parser("math-repeat-aggregate")
    math_repeat_aggregate.add_argument("--base-dir", default="runs")
    math_repeat_aggregate.add_argument("run_dirs", nargs="+")

    humaneval_repeat_aggregate = subparsers.add_parser("humaneval-repeat-aggregate")
    humaneval_repeat_aggregate.add_argument("--base-dir", default="runs")
    humaneval_repeat_aggregate.add_argument("run_dirs", nargs="+")

    math = subparsers.add_parser("math")
    math.add_argument("--experiment", default="math")
    math.add_argument("--subset", choices=("smoke", "validation", "test"), default="validation")
    math.add_argument("--limit", type=int, default=None)
    math.add_argument("--task-ids", default="")
    math.add_argument("--num-shards", type=int, default=1)
    math.add_argument("--shard-index", type=int, default=0)
    math.add_argument("--resume-run-dir", default="")
    math.add_argument("--base-dir", default="runs")
    math.add_argument("--model", default="openai_gpt4o_mini")
    math.add_argument("overrides", nargs="*")

    humaneval = subparsers.add_parser("humaneval")
    humaneval.add_argument("--experiment", default="humaneval")
    humaneval.add_argument("--subset", choices=("smoke", "validation", "test"), default="validation")
    humaneval.add_argument("--limit", type=int, default=None)
    humaneval.add_argument("--task-ids", default="")
    humaneval.add_argument("--num-shards", type=int, default=1)
    humaneval.add_argument("--shard-index", type=int, default=0)
    humaneval.add_argument("--resume-run-dir", default="")
    humaneval.add_argument("--base-dir", default="runs")
    humaneval.add_argument("--model", default="openai_gpt4o_mini")
    humaneval.add_argument("overrides", nargs="*")

    # New AFlow-aligned benchmarks
    for bench_name, bench_default_exp in [
        ("gsm8k", "gsm8k"),
        ("mbpp", "mbpp"),
        ("hotpotqa", "hotpotqa"),
        ("drop", "drop"),
    ]:
        bench_parser = subparsers.add_parser(bench_name)
        bench_parser.add_argument("--experiment", default=bench_default_exp)
        bench_parser.add_argument("--subset", choices=("smoke", "validation", "test"), default="validation")
        bench_parser.add_argument("--limit", type=int, default=None)
        bench_parser.add_argument("--task-ids", default="")
        bench_parser.add_argument("--num-shards", type=int, default=1)
        bench_parser.add_argument("--shard-index", type=int, default=0)
        bench_parser.add_argument("--resume-run-dir", default="")
        bench_parser.add_argument("--base-dir", default="runs")
        bench_parser.add_argument("--model", default="openai_gpt4o_mini")
        bench_parser.add_argument("overrides", nargs="*")

    case_study = subparsers.add_parser("case-study")
    case_study.add_argument("--experiment", default="scanpy_pbmc3k_case")
    case_study.add_argument("--base-dir", default="runs")
    case_study.add_argument("--model", default="")
    case_study.add_argument("overrides", nargs="*")

    args = parser.parse_args(argv)
    if args.command == "stage0":
        math_cfg = load_hydra_config(overrides=["experiment=math", f"model={args.benchmark_model}", *args.overrides])
        humaneval_cfg = load_hydra_config(
            overrides=["experiment=humaneval", f"model={args.benchmark_model}", *args.overrides]
        )
        summary = run_stage0_validation(
            math_cfg,
            humaneval_cfg,
            base_dir=args.base_dir,
        )
        print(json.dumps(summary, ensure_ascii=True, indent=2))
        return 0 if summary.get("success") else 1

    if args.command == "math-aggregate":
        summary = aggregate_math_runs(args.run_dirs, base_dir=args.base_dir)
        print(json.dumps(summary, ensure_ascii=True, indent=2))
        return 0 if summary.get("task_count", 0) > 0 else 1

    if args.command == "humaneval-aggregate":
        summary = aggregate_humaneval_runs(args.run_dirs, base_dir=args.base_dir)
        print(json.dumps(summary, ensure_ascii=True, indent=2))
        return 0 if summary.get("task_count", 0) > 0 else 1

    if args.command == "math-repeat-aggregate":
        summary = aggregate_math_repeats(args.run_dirs, base_dir=args.base_dir)
        print(json.dumps(summary, ensure_ascii=True, indent=2))
        return 0 if summary.get("repeat_count", 0) > 0 else 1

    if args.command == "humaneval-repeat-aggregate":
        summary = aggregate_humaneval_repeats(args.run_dirs, base_dir=args.base_dir)
        print(json.dumps(summary, ensure_ascii=True, indent=2))
        return 0 if summary.get("repeat_count", 0) > 0 else 1

    if args.command == "math":
        cfg = load_hydra_config(overrides=[f"experiment={args.experiment}", f"model={args.model}", *args.overrides])
        task_ids = [item.strip() for item in args.task_ids.split(",") if item.strip()]
        summary = run_math_experiment(
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

    if args.command == "humaneval":
        cfg = load_hydra_config(overrides=[f"experiment={args.experiment}", f"model={args.model}", *args.overrides])
        task_ids = [item.strip() for item in args.task_ids.split(",") if item.strip()]
        summary = run_humaneval_experiment(
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

    # Handle new AFlow-aligned benchmarks via runner classes
    _RUNNER_MAP = {
        "gsm8k": ("agentcoop.benchmarks.gsm8k_runner", "GSM8KRunner"),
        "mbpp": ("agentcoop.benchmarks.mbpp_runner", "MBPPRunner"),
        "hotpotqa": ("agentcoop.benchmarks.hotpotqa_runner", "HotpotQARunner"),
        "drop": ("agentcoop.benchmarks.drop_runner", "DROPRunner"),
    }
    if args.command in _RUNNER_MAP:
        import importlib
        module_path, class_name = _RUNNER_MAP[args.command]
        mod = importlib.import_module(module_path)
        runner_cls = getattr(mod, class_name)
        cfg = load_hydra_config(overrides=[f"experiment={args.experiment}", f"model={args.model}", *args.overrides])
        task_ids = [item.strip() for item in args.task_ids.split(",") if item.strip()]
        runner = runner_cls()
        summary = runner.run(
            cfg,
            subset=args.subset,
            limit=args.limit,
            task_ids=task_ids or None,
            num_shards=args.num_shards,
            shard_index=args.shard_index,
            base_dir=args.base_dir,
            resume_run_dir=args.resume_run_dir or None,
        )
        print(json.dumps(summary, ensure_ascii=True, indent=2))
        return 0 if summary.get("task_count", 0) > 0 else 1

    if args.command == "case-study":
        overrides = [f"experiment={args.experiment}", *args.overrides]
        if args.model:
            overrides.append(f"model={args.model}")
        cfg = load_hydra_config(overrides=overrides)
        summary = run_case_study_experiment(cfg, base_dir=args.base_dir)
        print(json.dumps(summary, ensure_ascii=True, indent=2))
        return 0 if summary.get("success") else 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
