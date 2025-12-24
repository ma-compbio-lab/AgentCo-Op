from __future__ import annotations

import argparse

from data import load_tasks
from eval.metrics import basic_metrics
from methods.dispatcher import run_method
from models import get_model_backend
from runtime import build_runtime
from utils import append_jsonl


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch evaluation harness.")
    parser.add_argument("--tasks", required=True, help="Path to JSONL tasks file.")
    parser.add_argument("--method", default="orchestrated", choices=["baseline", "sequential", "orchestrated"])
    parser.add_argument("--model_backend", default="mock", choices=["mock", "openai"])
    parser.add_argument("--model_name", default=None, help="Model name for API backends.")
    parser.add_argument("--log_dir", default="logs", help="Directory for traces.")
    parser.add_argument("--max_samples", type=int, default=None)
    args = parser.parse_args()

    model = get_model_backend(args.model_backend, model=args.model_name)
    runtime = build_runtime(model, log_dir=args.log_dir)

    tasks = load_tasks(args.tasks)
    if args.max_samples:
        tasks = tasks[: args.max_samples]

    summary_path = f"{args.log_dir}/eval_summary.jsonl"
    for task in tasks:
        result = run_method(args.method, task, runtime)
        metrics = basic_metrics(result)
        row = {
            "task_id": task.task_id,
            "method": args.method,
            "metrics": metrics,
        }
        append_jsonl(summary_path, row)


if __name__ == "__main__":
    main()
