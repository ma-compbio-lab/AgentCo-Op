from __future__ import annotations

import argparse

from core.contracts import TaskSpec
from methods.dispatcher import run_method
from models import get_model_backend
from runtime import build_runtime


def parse_list(value: str) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent-Cop runner.")
    parser.add_argument("--task", required=True, help="User task prompt.")
    parser.add_argument("--constraint", action="append", default=[], help="Constraint (repeatable).")
    parser.add_argument("--success", action="append", default=[], help="Success criterion (repeatable).")
    parser.add_argument("--budget_tokens", type=int, default=8000)
    parser.add_argument("--method", default="orchestrated", choices=["baseline", "sequential", "orchestrated"])
    parser.add_argument("--model_backend", default="mock", choices=["mock", "openai"])
    parser.add_argument("--model_name", default=None, help="Model name for API backends.")
    parser.add_argument("--log_dir", default="logs", help="Directory for traces.")
    parser.add_argument("--input_modalities", default="text")
    parser.add_argument("--output_modalities", default="text")
    args = parser.parse_args()

    task = TaskSpec(
        goal=args.task,
        constraints=args.constraint,
        success_criteria=args.success,
        budget_tokens=args.budget_tokens,
        input_modalities=parse_list(args.input_modalities),
        output_modalities=parse_list(args.output_modalities),
    )

    model = get_model_backend(args.model_backend, model=args.model_name)
    runtime = build_runtime(model, log_dir=args.log_dir)

    result = run_method(args.method, task, runtime)
    print(result.answer)
    if result.judge_report:
        print(f"\n[Judge] ok={result.judge_report.ok} score={result.judge_report.score}")


if __name__ == "__main__":
    main()
