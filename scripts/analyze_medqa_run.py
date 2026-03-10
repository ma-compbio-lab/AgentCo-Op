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

from dynaforge.experiment_runner import build_medqa_deep_analysis, render_medqa_deep_analysis


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate deep analysis artifacts for a completed MedQA run.")
    parser.add_argument("run_dir", help="Path to a MedQA run directory containing eval/task_results.json")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write summaries/deep_analysis.json and summaries/deep_analysis.md in the run directory.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_dir = Path(args.run_dir).resolve()
    task_results_path = run_dir / "eval" / "task_results.json"
    summary_path = run_dir / "summaries" / "summary.json"
    if not task_results_path.exists():
        raise SystemExit(f"Missing task results: {task_results_path}")

    task_results = json.loads(task_results_path.read_text(encoding="utf-8"))
    summary = (
        json.loads(summary_path.read_text(encoding="utf-8"))
        if summary_path.exists()
        else {
            "subset": "unknown",
            "task_count": len(task_results),
            "accuracy": sum(1 for item in task_results if item.get("correct")) / max(1, len(task_results)),
        }
    )

    deep_analysis = build_medqa_deep_analysis(task_results)
    markdown = render_medqa_deep_analysis(summary, deep_analysis)

    if args.write:
        summaries_dir = run_dir / "summaries"
        summaries_dir.mkdir(parents=True, exist_ok=True)
        (summaries_dir / "deep_analysis.json").write_text(
            json.dumps(deep_analysis, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        (summaries_dir / "deep_analysis.md").write_text(markdown, encoding="utf-8")

    output = {
        "run_dir": str(run_dir),
        "review_task_count": deep_analysis.get("review_task_count", 0),
        "question_types": list(deep_analysis.get("question_type_breakdown", {}).keys()),
        "top_review_failures": deep_analysis.get("top_review_failures", [])[:5],
    }
    print(json.dumps(output, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
