#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "output" / "reports"

MATH_AGGREGATE = PROJECT_ROOT / "runs/aggregated/math/test/math-aggregate/20260316_022906_5d4a6064"
HUMANEVAL_AGGREGATE = PROJECT_ROOT / "runs/aggregated/humaneval/test/humaneval-aggregate/20260316_022906_b6d7c4b9"
MEDQA_AGGREGATE = PROJECT_ROOT / "runs/aggregated/medqa/full/medqa-aggregate/20260316_022906_5ca6c968"
MEDQA_VALIDATION_BASELINE = PROJECT_ROOT / "runs/medqa-validation-baseline/medqa-smoke/20260316_023714_62ffc8d9"
MEDQA_VALIDATION_V2 = PROJECT_ROOT / "runs/medqa-validation-v2/medqa-smoke/20260316_023714_c168ea1b"
MATH_REPAIR = PROJECT_ROOT / "runs/math-repair/math-test/20260316_024451_b51fda75"
CASE_STUDY_REPORT = PROJECT_ROOT / "output/reports/experiment3_case_study_report.md"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return load_json(path)


def short_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def workflow_design(benchmark: str) -> dict[str, Any]:
    if benchmark == "math":
        return {
            "experiment": "math",
            "graph": "solver -> [reviewer -> reviser]",
            "base_nodes": ["solver"],
            "gated_subgraph": ["reviewer", "reviser"],
            "gate_rule": "review when solver confidence < configured threshold",
        }
    if benchmark == "humaneval":
        return {
            "experiment": "humaneval",
            "graph": "coder -> [reviewer -> reviser]",
            "base_nodes": ["coder"],
            "gated_subgraph": ["reviewer", "reviser"],
            "gate_rule": "review when coder confidence < configured threshold",
        }
    return {
        "experiment": "medqa",
        "graph": "planner -> responder -> [reviewer -> reviser]",
        "base_nodes": ["planner", "responder"],
        "gated_subgraph": ["reviewer", "reviser"],
        "gate_rule": (
            "review when responder output is uncertain; "
            "in medqa_v2 this also expands to mechanism / next-step-diagnosis "
            "and moderately uncertain next-step-management / prognosis cases"
        ),
    }


def medqa_v2_live_status() -> dict[str, Any]:
    base = PROJECT_ROOT / "runs/benchmark-full-v2"
    shards: list[dict[str, Any]] = []
    for shard_dir in sorted(base.glob("medqa-full-shard*of08")):
        run_dirs = sorted(path for path in shard_dir.iterdir() if path.is_dir())
        if not run_dirs:
            continue
        run_dir = run_dirs[-1]
        task_results_path = run_dir / "eval" / "task_results.json"
        if not task_results_path.exists():
            shards.append(
                {
                    "shard": shard_dir.name,
                    "run_dir": str(run_dir),
                    "task_count": 0,
                    "correct": 0,
                    "status": "initialized",
                }
            )
            continue
        rows = load_json(task_results_path)
        shards.append(
            {
                "shard": shard_dir.name,
                "run_dir": str(run_dir),
                "task_count": len(rows),
                "correct": sum(1 for row in rows if row.get("correct")),
                "status": "running_or_complete",
            }
        )
    return {
        "slurm_job_id": "30749",
        "base_dir": str(base),
        "shards": shards,
        "task_count_observed": sum(item["task_count"] for item in shards),
        "correct_observed": sum(item["correct"] for item in shards),
    }


def track_payload(run_dir: Path, benchmark: str) -> dict[str, Any]:
    summary = load_json(run_dir / "summaries/summary.json")
    analysis_md = (run_dir / "summaries/analysis.md").read_text(encoding="utf-8")
    task_results_path = run_dir / "eval/task_results.json"
    payload = {
        "benchmark": benchmark,
        "run_dir": str(run_dir),
        "summary": summary,
        "workflow_design": workflow_design(benchmark),
        "analysis_path": str(run_dir / "summaries/analysis.md"),
        "task_results_path": str(task_results_path),
        "analysis_excerpt": "\n".join(analysis_md.splitlines()[:20]),
    }
    if benchmark == "medqa":
        deep_analysis = load_optional_json(run_dir / "summaries/deep_analysis.json")
        payload["deep_analysis_path"] = str(run_dir / "summaries/deep_analysis.json")
        payload["deep_analysis"] = deep_analysis
    return payload


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Benchmark Program Report",
        "",
        "This report summarizes the current Experiment 1 / Experiment 2 benchmark state.",
        "Detailed Experiment 3 case-study logs remain in:",
        f"- `{CASE_STUDY_REPORT}`",
        "",
        "## Canonical Aggregates",
    ]
    for key in ("math", "humaneval", "medqa"):
        track = report["tracks"][key]
        summary = track["summary"]
        metric_key = "solve_rate" if key == "math" else "pass_at_1" if key == "humaneval" else "accuracy"
        lines.extend(
            [
                f"### {key.upper()}",
                f"- canonical run: `{track['run_dir']}`",
                f"- workflow design: `{track['workflow_design']['graph']}`",
                f"- gate rule: {track['workflow_design']['gate_rule']}",
                f"- primary metric: `{metric_key}={summary.get(metric_key, summary.get('accuracy', 0.0)):.4f}`",
                f"- task count: `{summary.get('task_count', 0)}`",
                f"- average USD: `{summary.get('average_usd', 0.0):.6f}`",
                f"- average latency: `{summary.get('average_latency_s', 0.0):.2f}s`",
                f"- workflow patterns: `{short_json(summary.get('workflow_patterns', {}))}`",
                f"- failure breakdown: `{short_json(summary.get('failure_breakdown', {}))}`",
                "",
            ]
        )
        if key == "medqa":
            deep = track.get("deep_analysis") or {}
            lines.append("#### MedQA deep-analysis highlights")
            lines.append(
                f"- question-type breakdown keys: `{', '.join(sorted((deep.get('question_type_breakdown') or {}).keys()))}`"
            )
            lines.append(
                f"- review failure breakdown: `{short_json(deep.get('review_failure_breakdown', {}))}`"
            )
            lines.append(
                f"- role frequency: `{short_json(deep.get('role_frequency', {}))}`"
            )
            lines.append("")

    medqa_repair = report["medqa_repair"]
    lines.extend(
        [
            "## MedQA Repair Loop",
            "",
            f"- baseline validation smoke: `{medqa_repair['baseline_validation']['run_dir']}`",
            f"  accuracy `{medqa_repair['baseline_validation']['summary']['accuracy']:.2f}`",
            f"  review count `{medqa_repair['baseline_validation']['summary']['gate_activations'].get('medqa_low_conf_review', 0)}`",
            f"- repaired validation smoke: `{medqa_repair['v2_validation']['run_dir']}`",
            f"  accuracy `{medqa_repair['v2_validation']['summary']['accuracy']:.2f}`",
            f"  review count `{medqa_repair['v2_validation']['summary']['gate_activations'].get('medqa_low_conf_review', 0)}`",
            f"- validation delta: improved `{len(medqa_repair['validation_flips']['improved_task_ids'])}`"
            f", regressed `{len(medqa_repair['validation_flips']['regressed_task_ids'])}`",
            "",
            "### MedQA v2 design",
            f"- workflow design: `{workflow_design('medqa')['graph']}`",
            "- prompt changes: shorter responder/reviewer prompts, independent reviewer solve, stronger exact-ask / decisive-clue framing",
            "- gating changes: broader review coverage for management/prognosis under moderate uncertainty",
            "",
            "### MedQA v2 live full-run status",
            f"- Slurm job: `{medqa_repair['live_full_status']['slurm_job_id']}`",
            f"- observed completed tasks: `{medqa_repair['live_full_status']['task_count_observed']}`",
            f"- observed correct tasks: `{medqa_repair['live_full_status']['correct_observed']}`",
        ]
    )
    for shard in medqa_repair["live_full_status"]["shards"]:
        lines.append(
            f"- `{shard['shard']}`: tasks={shard['task_count']} correct={shard['correct']} status={shard['status']}"
        )

    lines.extend(
        [
            "",
            "## Reliability Repairs",
            "",
            f"- MATH focused repair run: `{report['math_repair']['run_dir']}`",
            f"  solve_rate `{report['math_repair']['summary']['solve_rate']:.2f}` on task `{report['math_repair']['summary']['requested_task_ids'][0]}`",
            "- runtime fix: retry transient OpenAI-compatible 400 JSON-body parse faults",
            "",
            "## Experiment 2 Launch State",
            "",
            "- `30752`: MATH budget sweeps",
            "- `30753`: HumanEval budget sweeps",
            "- MedQA budget sweep intentionally deferred until the MedQA v2 full rerun stabilizes",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    baseline_validation_summary = load_json(MEDQA_VALIDATION_BASELINE / "summaries/summary.json")
    v2_validation_summary = load_json(MEDQA_VALIDATION_V2 / "summaries/summary.json")
    baseline_validation_results = load_json(MEDQA_VALIDATION_BASELINE / "eval/task_results.json")
    v2_validation_results = load_json(MEDQA_VALIDATION_V2 / "eval/task_results.json")
    baseline_by_id = {row["task_id"]: row for row in baseline_validation_results}
    v2_by_id = {row["task_id"]: row for row in v2_validation_results}
    improved = sorted(task_id for task_id in baseline_by_id if (not baseline_by_id[task_id]["correct"]) and v2_by_id[task_id]["correct"])
    regressed = sorted(task_id for task_id in baseline_by_id if baseline_by_id[task_id]["correct"] and (not v2_by_id[task_id]["correct"]))

    report = {
        "generated_from": {
            "math": str(MATH_AGGREGATE),
            "humaneval": str(HUMANEVAL_AGGREGATE),
            "medqa": str(MEDQA_AGGREGATE),
            "medqa_validation_baseline": str(MEDQA_VALIDATION_BASELINE),
            "medqa_validation_v2": str(MEDQA_VALIDATION_V2),
            "math_repair": str(MATH_REPAIR),
            "case_study_report": str(CASE_STUDY_REPORT),
        },
        "tracks": {
            "math": track_payload(MATH_AGGREGATE, "math"),
            "humaneval": track_payload(HUMANEVAL_AGGREGATE, "humaneval"),
            "medqa": track_payload(MEDQA_AGGREGATE, "medqa"),
        },
        "medqa_repair": {
            "baseline_validation": {
                "run_dir": str(MEDQA_VALIDATION_BASELINE),
                "summary": baseline_validation_summary,
            },
            "v2_validation": {
                "run_dir": str(MEDQA_VALIDATION_V2),
                "summary": v2_validation_summary,
            },
            "validation_flips": {
                "improved_task_ids": improved,
                "regressed_task_ids": regressed,
            },
            "live_full_status": medqa_v2_live_status(),
        },
        "math_repair": {
            "run_dir": str(MATH_REPAIR),
            "summary": load_json(MATH_REPAIR / "summaries/summary.json"),
        },
    }

    json_path = OUTPUT_DIR / "benchmark_program_report.json"
    md_path = OUTPUT_DIR / "benchmark_program_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"report_json": str(json_path), "report_md": str(md_path)}, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
