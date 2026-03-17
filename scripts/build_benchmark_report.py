#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "output" / "reports"

MATH_AGGREGATE = PROJECT_ROOT / "runs/aggregated/math/test/math-aggregate/20260316_022906_5d4a6064"
HUMANEVAL_AGGREGATE = PROJECT_ROOT / "runs/aggregated/humaneval/test/humaneval-aggregate/20260316_022906_b6d7c4b9"
MEDQA_AGGREGATE = PROJECT_ROOT / "runs/aggregated/medqa/full/medqa-aggregate/20260316_022906_5ca6c968"
MEDQA_V2_AGGREGATE = PROJECT_ROOT / "runs/aggregated-v2/medqa/full/medqa-aggregate/20260316_161856_f08e5587"
MEDQA_VALIDATION_BASELINE = PROJECT_ROOT / "runs/medqa-validation-baseline/medqa-smoke/20260316_023714_62ffc8d9"
MEDQA_VALIDATION_V2 = PROJECT_ROOT / "runs/medqa-validation-v2/medqa-smoke/20260316_023714_c168ea1b"
MATH_REPAIR = PROJECT_ROOT / "runs/math-repair/math-test/20260316_024451_b51fda75"
MATH_BUDGET_SWEEP = PROJECT_ROOT / "runs/aggregated-budget-sweeps/math/math_budget_sweep_summary.json"
HUMANEVAL_BUDGET_SWEEP = PROJECT_ROOT / "runs/aggregated-budget-sweeps/humaneval/humaneval_budget_sweep_summary.json"
CASE_STUDY_REPORT = PROJECT_ROOT / "output/reports/experiment3_case_study_report.md"
MATH_VALIDATION_BASELINE = PROJECT_ROOT / "runs/aflow-validation-check/math-validation/20260316_174503_4dd2cfcb"
MATH_VALIDATION_V2 = PROJECT_ROOT / "runs/aflow-validation-check/math-validation/20260316_174948_1b0589ae"
HUMANEVAL_VALIDATION_BASELINE = PROJECT_ROOT / "runs/aflow-validation-check/humaneval-validation/20260316_174503_e3196d55"
HUMANEVAL_VALIDATION_V2 = PROJECT_ROOT / "runs/aflow-validation-check/humaneval-validation/20260316_174948_5366cb1d"

FULL_JOB_IDS = ["31103", "31104", "31106", "31107", "31108", "31109"]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_optional_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    return load_json(path)


def short_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def rel(path: Path | str) -> str:
    return str(Path(path).resolve().relative_to(PROJECT_ROOT))


def metric_key_for(benchmark: str) -> str:
    if benchmark == "math":
        return "solve_rate"
    if benchmark == "humaneval":
        return "pass_at_1"
    return "accuracy"


def workflow_design(benchmark: str, variant: str = "current") -> dict[str, Any]:
    if benchmark == "math":
        if variant == "historical":
            return {
                "graph": "solver -> [reviewer -> reviser]",
                "base_nodes": [
                    {
                        "node_id": "solver",
                        "role": "Solver",
                        "purpose": "Solve directly and emit a final exact answer.",
                    }
                ],
                "gated_subgraph": [
                    {
                        "node_id": "reviewer",
                        "role": "Reviewer",
                        "purpose": "Critique low-confidence solver answers.",
                    },
                    {
                        "node_id": "reviser",
                        "role": "Reviser",
                        "purpose": "Emit the corrected final answer after review.",
                    },
                ],
                "tool_nodes": [],
                "gate_rule": "Trigger review when solver confidence falls below threshold.",
                "input_contract": "task.description contains the math problem statement; metadata includes type and level.",
                "expected_output": "A single exact final answer.",
            }
        return {
            "graph": "solver + programmer -> program_exec -> selector -> [reviewer -> reviser]",
            "base_nodes": [
                {
                    "node_id": "solver",
                    "role": "Solver",
                    "purpose": "Produce a direct reasoning-based answer and short outline.",
                },
                {
                    "node_id": "programmer",
                    "role": "Programmer",
                    "purpose": "Write a compact Python program that computes FINAL_ANSWER.",
                },
                {
                    "node_id": "program_exec",
                    "role": "ProgramExecutor",
                    "purpose": "Run the Python candidate inside an isolated sandbox with SymPy.",
                    "tool": "direct:math_program_exec",
                },
                {
                    "node_id": "selector",
                    "role": "Selector",
                    "purpose": "Arbitrate between the direct solver answer and the executed answer.",
                },
            ],
            "gated_subgraph": [
                {
                    "node_id": "reviewer",
                    "role": "Reviewer",
                    "purpose": "Reconcile solver, program, and selector when execution fails or disagreement persists.",
                },
                {
                    "node_id": "reviser",
                    "role": "Reviser",
                    "purpose": "Emit the final corrected answer after review.",
                },
            ],
            "tool_nodes": ["program_exec"],
            "gate_rule": (
                "Trigger review when selector outputs needs_review=true or selector confidence < 0.78."
            ),
            "input_contract": "task.description is the problem statement; metadata gives source type, level, and source_config.",
            "expected_output": "A single exact final answer plus arbitration notes.",
        }
    if benchmark == "humaneval":
        if variant == "historical":
            return {
                "graph": "coder -> [reviewer -> reviser]",
                "base_nodes": [
                    {
                        "node_id": "coder",
                        "role": "Coder",
                        "purpose": "Generate the final Python function directly.",
                    }
                ],
                "gated_subgraph": [
                    {
                        "node_id": "reviewer",
                        "role": "Reviewer",
                        "purpose": "Critique low-confidence code drafts.",
                    },
                    {
                        "node_id": "reviser",
                        "role": "Reviser",
                        "purpose": "Repair the implementation after review.",
                    },
                ],
                "tool_nodes": [],
                "gate_rule": "Trigger review when coder confidence falls below threshold.",
                "input_contract": "task.description contains the HumanEval function prompt and metadata.entry_point.",
                "expected_output": "A complete Python completion.",
            }
        return {
            "graph": "coder -> public_test_runner -> [reviewer -> reviser -> retest_runner]",
            "base_nodes": [
                {
                    "node_id": "coder",
                    "role": "Coder",
                    "purpose": "Generate the Python implementation.",
                },
                {
                    "node_id": "public_test_runner",
                    "role": "PublicTestRunner",
                    "purpose": "Execute the completion against the AFlow public-test bundle.",
                    "tool": "direct:humaneval_public_test_runner",
                },
            ],
            "gated_subgraph": [
                {
                    "node_id": "reviewer",
                    "role": "Reviewer",
                    "purpose": "Diagnose why the completion failed public tests.",
                },
                {
                    "node_id": "reviser",
                    "role": "Reviser",
                    "purpose": "Rewrite the implementation using the failure signal.",
                },
                {
                    "node_id": "retest_runner",
                    "role": "RetestRunner",
                    "purpose": "Re-run public tests on the revised implementation.",
                    "tool": "direct:humaneval_public_test_runner",
                },
            ],
            "tool_nodes": ["public_test_runner", "retest_runner"],
            "gate_rule": "Trigger repair when public_test_runner.passed == false.",
            "input_contract": "task.description is the HumanEval prompt; metadata.entry_point identifies the target function.",
            "expected_output": "A complete Python function that passes public tests and is scored with pass@1.",
        }
    if variant == "v2":
        return {
            "graph": "planner -> responder -> [reviewer -> reviser]",
            "base_nodes": [
                {
                    "node_id": "planner",
                    "role": "Planner",
                    "purpose": "Classify question type, extract decisive clue, and summarize the exact ask.",
                },
                {
                    "node_id": "responder",
                    "role": "Responder",
                    "purpose": "Answer independently while treating planner guidance as advisory only.",
                },
            ],
            "gated_subgraph": [
                {
                    "node_id": "reviewer",
                    "role": "Reviewer",
                    "purpose": "Re-solve the question independently before comparing with the responder.",
                },
                {
                    "node_id": "reviser",
                    "role": "Reviser",
                    "purpose": "Emit the final corrected option using the review decisive clue.",
                },
            ],
            "tool_nodes": [],
            "gate_rule": (
                "Trigger review for mechanism, next_step_diagnosis, prognosis, and moderately uncertain next_step_management; "
                "also trigger when responder confidence < 0.84."
            ),
            "input_contract": "question stem + multiple-choice options + planner question_type/key_clues.",
            "expected_output": "A single option label and exact option text.",
        }
    return {
        "graph": "planner -> responder -> [reviewer -> reviser]",
        "base_nodes": [
            {
                "node_id": "planner",
                "role": "Planner",
                "purpose": "Classify the question and extract key clues.",
            },
            {
                "node_id": "responder",
                "role": "Responder",
                "purpose": "Choose the final answer option and explain it briefly.",
            },
        ],
        "gated_subgraph": [
            {
                "node_id": "reviewer",
                "role": "Reviewer",
                "purpose": "Audit uncertain answers against the question and options.",
            },
            {
                "node_id": "reviser",
                "role": "Reviser",
                "purpose": "Emit the corrected final answer after review.",
            },
        ],
        "tool_nodes": [],
        "gate_rule": "Historical baseline: trigger review on low confidence and planner-selected high-risk question families.",
        "input_contract": "question stem + options + planner question_type/key clues.",
        "expected_output": "A single option label and exact option text.",
    }


def read_task_results(run_dir: Path) -> list[dict[str, Any]]:
    return load_json(run_dir / "eval" / "task_results.json")


def find_first(rows: list[dict[str, Any]], predicate: Any) -> dict[str, Any] | None:
    for row in rows:
        if predicate(row):
            return row
    return None


def task_excerpt(row: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    return {key: row.get(key) for key in keys if key in row}


def validation_compare(
    benchmark: str,
    baseline_dir: Path,
    v2_dir: Path,
) -> dict[str, Any]:
    metric_key = metric_key_for(benchmark)
    baseline_summary = load_json(baseline_dir / "summaries" / "summary.json")
    v2_summary = load_json(v2_dir / "summaries" / "summary.json")
    return {
        "baseline": {
            "run_dir": str(baseline_dir),
            "summary": baseline_summary,
            "workflow_design": workflow_design(benchmark, "historical"),
        },
        "v2": {
            "run_dir": str(v2_dir),
            "summary": v2_summary,
            "workflow_design": workflow_design(benchmark, "current"),
        },
        "delta": {
            metric_key: v2_summary.get(metric_key, 0.0) - baseline_summary.get(metric_key, 0.0),
            "average_usd": v2_summary.get("average_usd", 0.0) - baseline_summary.get("average_usd", 0.0),
            "average_latency_s": v2_summary.get("average_latency_s", 0.0) - baseline_summary.get("average_latency_s", 0.0),
        },
    }


def medqa_validation_flips() -> dict[str, list[str]]:
    baseline_rows = read_task_results(MEDQA_VALIDATION_BASELINE)
    v2_rows = read_task_results(MEDQA_VALIDATION_V2)
    baseline_by_id = {row["task_id"]: row for row in baseline_rows}
    v2_by_id = {row["task_id"]: row for row in v2_rows}
    improved = sorted(
        task_id
        for task_id in baseline_by_id
        if (not baseline_by_id[task_id]["correct"]) and v2_by_id[task_id]["correct"]
    )
    regressed = sorted(
        task_id
        for task_id in baseline_by_id
        if baseline_by_id[task_id]["correct"] and (not v2_by_id[task_id]["correct"])
    )
    return {"improved_task_ids": improved, "regressed_task_ids": regressed}


def medqa_v2_final_status() -> dict[str, Any]:
    baseline = load_json(MEDQA_AGGREGATE / "summaries/summary.json")
    v2 = load_json(MEDQA_V2_AGGREGATE / "summaries/summary.json")
    return {
        "slurm_job_id": "30749",
        "run_dir": str(MEDQA_V2_AGGREGATE),
        "summary": v2,
        "delta_vs_baseline": {
            "accuracy": v2["accuracy"] - baseline["accuracy"],
            "average_usd": v2["average_usd"] - baseline["average_usd"],
            "average_latency_s": v2["average_latency_s"] - baseline["average_latency_s"],
            "review_activation_delta": (
                v2.get("gate_activations", {}).get("medqa_low_conf_review", 0)
                - baseline.get("gate_activations", {}).get("medqa_low_conf_review", 0)
            ),
        },
        "promoted_to_canonical": False,
    }


def load_track_payload(run_dir: Path, benchmark: str, variant: str = "current") -> dict[str, Any]:
    summary = load_json(run_dir / "summaries" / "summary.json")
    analysis_path = run_dir / "summaries" / "analysis.md"
    payload = {
        "run_dir": str(run_dir),
        "summary": summary,
        "analysis_path": str(analysis_path),
        "task_results_path": str(run_dir / "eval" / "task_results.json"),
        "workflow_design": workflow_design(benchmark, variant),
    }
    if analysis_path.exists():
        payload["analysis_excerpt"] = "\n".join(
            analysis_path.read_text(encoding="utf-8").splitlines()[:20]
        )
    if benchmark == "medqa":
        payload["deep_analysis_path"] = str(run_dir / "summaries" / "deep_analysis.json")
        payload["deep_analysis"] = load_optional_json(run_dir / "summaries" / "deep_analysis.json")
    return payload


def extract_math_examples() -> dict[str, Any]:
    validation_rows = read_task_results(MATH_VALIDATION_V2)
    full_rows = read_task_results(PROJECT_ROOT / "runs/aflow-full/math-test-shard00of08/20260316_175331_50e243b9")
    in_progress_review_rows = read_task_results(
        PROJECT_ROOT / "runs/aflow-full/math-test-shard07of08/20260316_191531_aa443fc7"
    )
    selector_override = find_first(
        full_rows,
        lambda row: row.get("correct")
        and row.get("prediction_node") == "selector"
        and row.get("program_execution_passed")
        and row.get("prediction_answer") == row.get("gold_answer")
        and "selector_notes" in row,
    )
    stable_failure = find_first(
        validation_rows,
        lambda row: not row.get("correct"),
    )
    review_failure = find_first(
        in_progress_review_rows,
        lambda row: "reviewer" in row.get("workflow_signature", ""),
    )
    return {
        "selector_override_success": task_excerpt(
            selector_override or {},
            [
                "task_id",
                "question",
                "prediction_node",
                "prediction_answer",
                "gold_answer",
                "selector_notes",
                "program_execution_passed",
                "program_executed_answer",
                "workflow_signature",
                "report_path",
                "cost",
            ],
        ),
        "validation_failure_example": task_excerpt(
            stable_failure or {},
            [
                "task_id",
                "question",
                "prediction_answer",
                "gold_answer",
                "selector_notes",
                "program_execution_passed",
                "program_executed_answer",
                "program_execution_error",
                "workflow_signature",
                "report_path",
                "cost",
            ],
        ),
        "in_progress_review_failure": task_excerpt(
            review_failure or {},
            [
                "task_id",
                "question",
                "prediction_node",
                "prediction_answer",
                "gold_answer",
                "selector_needs_review",
                "selector_notes",
                "program_execution_passed",
                "program_execution_error",
                "workflow_signature",
                "report_path",
                "cost",
            ],
        ),
    }


def extract_humaneval_examples() -> dict[str, Any]:
    rows = read_task_results(HUMANEVAL_VALIDATION_V2)
    direct_pass = find_first(
        rows,
        lambda row: row.get("correct") and row.get("workflow_signature") == "coder->public_test_runner",
    )
    repair_pass = find_first(
        rows,
        lambda row: row.get("correct")
        and row.get("workflow_signature") == "coder->public_test_runner->reviewer->reviser->retest_runner",
    )
    return {
        "direct_pass_example": task_excerpt(
            direct_pass or {},
            [
                "task_id",
                "question",
                "prediction_node",
                "prediction_code",
                "public_test_passed",
                "public_test_result",
                "workflow_signature",
                "report_path",
                "cost",
            ],
        ),
        "repair_pass_example": task_excerpt(
            repair_pass or {},
            [
                "task_id",
                "question",
                "prediction_node",
                "prediction_code",
                "public_test_passed",
                "public_test_result",
                "retest_passed",
                "retest_result",
                "workflow_signature",
                "activated_gates",
                "report_path",
                "cost",
            ],
        ),
    }


def extract_medqa_examples() -> dict[str, Any]:
    baseline_rows = read_task_results(MEDQA_AGGREGATE)
    v2_rows = read_task_results(MEDQA_V2_AGGREGATE)
    reviewed_success = find_first(
        baseline_rows,
        lambda row: row.get("correct")
        and row.get("workflow_signature") == "planner->responder->reviewer->reviser",
    )
    reviewed_failure = find_first(
        baseline_rows,
        lambda row: (not row.get("correct"))
        and row.get("workflow_signature") == "planner->responder->reviewer->reviser",
    )
    v2_regression = find_first(
        v2_rows,
        lambda row: (not row.get("correct"))
        and row.get("workflow_signature") == "planner->responder->reviewer->reviser",
    )
    return {
        "review_success_example": task_excerpt(
            reviewed_success or {},
            [
                "task_id",
                "question_type",
                "question",
                "prediction_node",
                "prediction_label",
                "prediction_text",
                "gold_label",
                "gold_text",
                "workflow_signature",
                "activated_gates",
                "report_path",
                "cost",
            ],
        ),
        "review_failure_example": task_excerpt(
            reviewed_failure or {},
            [
                "task_id",
                "question_type",
                "question",
                "prediction_node",
                "prediction_label",
                "prediction_text",
                "gold_label",
                "gold_text",
                "workflow_signature",
                "activated_gates",
                "report_path",
                "cost",
            ],
        ),
        "v2_failure_example": task_excerpt(
            v2_regression or {},
            [
                "task_id",
                "question_type",
                "question",
                "prediction_node",
                "prediction_label",
                "prediction_text",
                "gold_label",
                "gold_text",
                "workflow_signature",
                "activated_gates",
                "report_path",
                "cost",
            ],
        ),
    }


def collect_partial_repeat_status(run_family: str, benchmark: str) -> dict[str, Any]:
    root = PROJECT_ROOT / "runs" / run_family
    shard_dirs = sorted(root.glob(f"{benchmark}-test-shard*"))
    shards: list[dict[str, Any]] = []
    for shard_dir in shard_dirs:
        candidate_runs = sorted(path for path in shard_dir.iterdir() if path.is_dir())
        if not candidate_runs:
            continue
        latest = candidate_runs[-1]
        task_results_path = latest / "eval" / "task_results.json"
        if not task_results_path.exists():
            continue
        rows = load_json(task_results_path)
        correct_count = sum(1 for row in rows if row.get("correct"))
        review_count = sum(1 for row in rows if "reviewer" in row.get("workflow_signature", ""))
        shards.append(
            {
                "shard": shard_dir.name,
                "run_dir": str(latest),
                "completed_tasks": len(rows),
                "correct_tasks": correct_count,
                "accuracy": (correct_count / len(rows)) if rows else 0.0,
                "reviewed_tasks": review_count,
            }
        )
    total_completed = sum(item["completed_tasks"] for item in shards)
    total_correct = sum(item["correct_tasks"] for item in shards)
    total_reviewed = sum(item["reviewed_tasks"] for item in shards)
    expected_total = 486 if benchmark == "math" else 131
    return {
        "run_family": run_family,
        "benchmark": benchmark,
        "expected_task_count": expected_total,
        "completed_task_count": total_completed,
        "correct_task_count": total_correct,
        "partial_accuracy": (total_correct / total_completed) if total_completed else None,
        "reviewed_task_count": total_reviewed,
        "shards": shards,
    }


def queue_snapshot() -> list[dict[str, str]]:
    cmd = [
        "squeue",
        "-u",
        subprocess.check_output(["bash", "-lc", "printf %s \"$USER\""], text=True).strip(),
        "-o",
        "%.18i|%.20j|%.8T|%.10M|%.9P|%.20R",
    ]
    try:
        completed = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except Exception:
        return []
    lines = completed.stdout.strip().splitlines()
    if len(lines) <= 1:
        return []
    rows = []
    for line in lines[1:]:
        job_id, name, state, elapsed, partition, reason = line.split("|", 5)
        rows.append(
            {
                "job_id": job_id.strip(),
                "name": name.strip(),
                "state": state.strip(),
                "elapsed": elapsed.strip(),
                "partition": partition.strip(),
                "reason": reason.strip(),
            }
        )
    return rows


def dataset_and_metric_notes() -> dict[str, Any]:
    return {
        "math": {
            "dataset_id": "EleutherAI/hendrycks_math",
            "paper_target": "617 level-5 problems from four types as stated in AFlow paper text",
            "public_package_reality": "AFlow public package available in this workspace resolves to 605 tasks = 119 validation + 486 test",
            "metric": "solve_rate",
            "base_model": "gpt-4o-mini",
            "status": "Aligned to the released public package, metric, and 3-run protocol. Not literally the 617-paper subset because that exact slice is not in the public package.",
        },
        "humaneval": {
            "dataset_id": "openai/openai_humaneval",
            "paper_target": "full public HumanEval set with pass@1",
            "public_package_reality": "AFlow public package resolves to 164 tasks = 33 validation + 131 test",
            "metric": "pass@1",
            "base_model": "gpt-4o-mini",
            "status": "Aligned to the released public package, metric, and 3-run protocol.",
        },
        "medqa": {
            "dataset_id": "bigbio/med_qa",
            "paper_target": "not an AFlow public benchmark; internal consistency benchmark",
            "public_package_reality": "1273-task official test split under med_qa_en_bigbio_qa",
            "metric": "accuracy",
            "base_model": "gpt-4o-mini",
            "status": "Internal benchmark kept on GPT-4o-mini for consistency with the benchmark execution policy.",
        },
    }


def historical_budget_sweep_notes() -> dict[str, Any]:
    return {
        "math": {
            "path": str(MATH_BUDGET_SWEEP),
            "summary": load_json(MATH_BUDGET_SWEEP),
            "status": "Historical only. Sweep ran before the MATH answer-leak fix, so it should not be used as the final aligned result.",
        },
        "humaneval": {
            "path": str(HUMANEVAL_BUDGET_SWEEP),
            "summary": load_json(HUMANEVAL_BUDGET_SWEEP),
            "status": "Historical only. Sweep used the pre-v2 workflow and should be rerun after the public-test-loop alignment is complete.",
        },
    }


def build_report() -> dict[str, Any]:
    medqa_deep_analysis = load_optional_json(MEDQA_AGGREGATE / "summaries" / "deep_analysis.json") or {}
    medqa_v2_summary = load_json(MEDQA_V2_AGGREGATE / "summaries" / "summary.json")
    medqa_summary = load_json(MEDQA_AGGREGATE / "summaries/summary.json")
    math_historical = load_track_payload(MATH_AGGREGATE, "math", "historical")
    humaneval_historical = load_track_payload(HUMANEVAL_AGGREGATE, "humaneval", "historical")
    medqa_current = load_track_payload(MEDQA_AGGREGATE, "medqa", "current")

    report = {
        "generated_from": {
            "math_historical": str(MATH_AGGREGATE),
            "humaneval_historical": str(HUMANEVAL_AGGREGATE),
            "medqa_current": str(MEDQA_AGGREGATE),
            "medqa_v2": str(MEDQA_V2_AGGREGATE),
            "medqa_validation_baseline": str(MEDQA_VALIDATION_BASELINE),
            "medqa_validation_v2": str(MEDQA_VALIDATION_V2),
            "math_repair": str(MATH_REPAIR),
            "math_budget_sweep": str(MATH_BUDGET_SWEEP),
            "humaneval_budget_sweep": str(HUMANEVAL_BUDGET_SWEEP),
            "case_study_report": str(CASE_STUDY_REPORT),
            "math_validation_baseline": str(MATH_VALIDATION_BASELINE),
            "math_validation_v2": str(MATH_VALIDATION_V2),
            "humaneval_validation_baseline": str(HUMANEVAL_VALIDATION_BASELINE),
            "humaneval_validation_v2": str(HUMANEVAL_VALIDATION_V2),
        },
        "queue_snapshot": queue_snapshot(),
        "dataset_and_metric_notes": dataset_and_metric_notes(),
        "aflow_realignment": {
            "math_invalidated_historical_full": {
                "run_dir": str(MATH_AGGREGATE),
                "summary": load_json(MATH_AGGREGATE / "summaries" / "summary.json"),
                "invalid_reason": "gold_answer leaked through task.hints into runtime node inputs",
            },
            "math_validation_compare": validation_compare("math", MATH_VALIDATION_BASELINE, MATH_VALIDATION_V2),
            "humaneval_validation_compare": validation_compare(
                "humaneval",
                HUMANEVAL_VALIDATION_BASELINE,
                HUMANEVAL_VALIDATION_V2,
            ),
            "full_job_ids": FULL_JOB_IDS,
            "math_repeat1_partial": collect_partial_repeat_status("aflow-full", "math"),
            "math_repeat2_partial": collect_partial_repeat_status("aflow-repeat2", "math"),
            "math_repeat3_partial": collect_partial_repeat_status("aflow-repeat3", "math"),
            "humaneval_repeat1_partial": collect_partial_repeat_status("aflow-full", "humaneval"),
            "humaneval_repeat2_partial": collect_partial_repeat_status("aflow-repeat2", "humaneval"),
            "humaneval_repeat3_partial": collect_partial_repeat_status("aflow-repeat3", "humaneval"),
        },
        "benchmarks": {
            "math": {
                "current_status": "in_progress",
                "current_workflow": workflow_design("math", "current"),
                "historical_workflow": workflow_design("math", "historical"),
                "historical_invalidated_run": math_historical,
                "validation_compare": validation_compare("math", MATH_VALIDATION_BASELINE, MATH_VALIDATION_V2),
                "representative_examples": extract_math_examples(),
                "current_takeaways": [
                    "The old full MATH number is invalid and should not be reported.",
                    "Execution-backed selection materially improves validation over the direct-solver baseline.",
                    "Some failures remain high-confidence execution errors where SymPy or the drafted program follows the wrong formalization.",
                ],
            },
            "humaneval": {
                "current_status": "in_progress",
                "current_workflow": workflow_design("humaneval", "current"),
                "historical_workflow": workflow_design("humaneval", "historical"),
                "historical_full_run": humaneval_historical,
                "validation_compare": validation_compare(
                    "humaneval",
                    HUMANEVAL_VALIDATION_BASELINE,
                    HUMANEVAL_VALIDATION_V2,
                ),
                "representative_examples": extract_humaneval_examples(),
                "current_takeaways": [
                    "The public-test loop adds real tool-grounded evidence instead of pure self-critique.",
                    "Most tasks exit after coder->public_test_runner; only a small subset activates reviewer/reviser/retest.",
                    "The aligned full repeats have not finished yet, so the historical pre-v2 aggregate remains only a reference point, not the target final number.",
                ],
            },
            "medqa": {
                "current_status": "completed",
                "current_workflow": workflow_design("medqa", "current"),
                "v2_workflow": workflow_design("medqa", "v2"),
                "current_full_run": medqa_current,
                "v2_full_run": load_track_payload(MEDQA_V2_AGGREGATE, "medqa", "v2"),
                "validation_flips": medqa_validation_flips(),
                "repair_loop": {
                    "baseline_validation_summary": load_json(MEDQA_VALIDATION_BASELINE / "summaries/summary.json"),
                    "v2_validation_summary": load_json(MEDQA_VALIDATION_V2 / "summaries/summary.json"),
                    "v2_full_status": medqa_v2_final_status(),
                },
                "deep_analysis": medqa_deep_analysis,
                "representative_examples": extract_medqa_examples(),
                "current_takeaways": [
                    "The official-test baseline remains the canonical MedQA result.",
                    "The v2 prompt and routing changes improved the 20-task smoke but failed to improve the full 1273-task test.",
                    "Review-heavy policies shift error clusters rather than raising net accuracy.",
                ],
                "delta_v2_vs_baseline": {
                    "accuracy": medqa_v2_summary["accuracy"] - medqa_summary["accuracy"],
                    "average_usd": medqa_v2_summary["average_usd"] - medqa_summary["average_usd"],
                    "review_count_delta": (
                        medqa_v2_summary.get("gate_activations", {}).get("medqa_low_conf_review", 0)
                        - medqa_summary.get("gate_activations", {}).get("medqa_low_conf_review", 0)
                    ),
                },
            },
        },
        "historical_budget_sweeps": historical_budget_sweep_notes(),
        "math_repair": {
            "run_dir": str(MATH_REPAIR),
            "summary": load_json(MATH_REPAIR / "summaries/summary.json"),
        },
        "overall_takeaways": [
            "The only completed trustworthy benchmark track right now is MedQA baseline; MedQA v2 is a failed repair and is not promoted.",
            "HumanEval and MATH have been realigned to AFlow-style workflows, but final 3-run full results are still pending.",
            "MATH now has the strongest structural improvement: execution-backed answer selection replaces pure confidence-based review.",
            "HumanEval now has a genuine tool-backed repair loop through public tests rather than prompt-only critique.",
        ],
    }
    return report


def format_workflow_md(title: str, spec: dict[str, Any]) -> list[str]:
    lines = [
        f"#### {title}",
        f"- graph: `{spec['graph']}`",
        f"- gate rule: {spec['gate_rule']}",
        f"- input contract: {spec['input_contract']}",
        f"- expected output: {spec['expected_output']}",
        "- nodes:",
    ]
    for node in spec["base_nodes"]:
        suffix = f" (`{node['tool']}`)" if "tool" in node else ""
        lines.append(f"  - `{node['node_id']}` / {node['role']}: {node['purpose']}{suffix}")
    if spec["gated_subgraph"]:
        lines.append("- gated nodes:")
        for node in spec["gated_subgraph"]:
            suffix = f" (`{node['tool']}`)" if "tool" in node else ""
            lines.append(f"  - `{node['node_id']}` / {node['role']}: {node['purpose']}{suffix}")
    return lines


def render_example(title: str, example: dict[str, Any]) -> list[str]:
    if not example:
        return [f"- {title}: unavailable"]
    compact = short_json(example)
    if len(compact) > 1800:
        compact = compact[:1797] + "..."
    return [f"- {title}: `{compact}`"]


def render_markdown(report: dict[str, Any]) -> str:
    math_validation = report["aflow_realignment"]["math_validation_compare"]
    humaneval_validation = report["aflow_realignment"]["humaneval_validation_compare"]
    medqa = report["benchmarks"]["medqa"]
    medqa_deep = medqa.get("deep_analysis") or {}
    lines = [
        "# Detailed Benchmark Report",
        "",
        "This report summarizes the current benchmark state after the AFlow realignment work.",
        "It separates historical results, currently trustworthy results, and still-running repeats.",
        "",
        "## Queue Snapshot",
    ]
    if report["queue_snapshot"]:
        for row in report["queue_snapshot"]:
            lines.append(
                f"- job `{row['job_id']}` `{row['name']}` state=`{row['state']}` elapsed=`{row['elapsed']}` partition=`{row['partition']}` reason=`{row['reason']}`"
            )
    else:
        lines.append("- no visible Slurm jobs")
    lines.extend(
        [
            "",
            "## Dataset And Metric Alignment",
        ]
    )
    for key in ("math", "humaneval", "medqa"):
        note = report["dataset_and_metric_notes"][key]
        lines.extend(
            [
                f"### {key.upper()}",
                f"- dataset: `{note['dataset_id']}`",
                f"- target paper setting: {note['paper_target']}",
                f"- current reproducible slice: {note['public_package_reality']}",
                f"- metric: `{note['metric']}`",
                f"- base model: `{note['base_model']}`",
                f"- status: {note['status']}",
                "",
            ]
        )

    lines.extend(
        [
            "## AFlow Realignment Status",
            "",
            "### MATH",
            f"- invalidated historical run: `{rel(report['aflow_realignment']['math_invalidated_historical_full']['run_dir'])}`",
            f"- invalid reason: `{report['aflow_realignment']['math_invalidated_historical_full']['invalid_reason']}`",
            f"- validation baseline solve_rate: `{math_validation['baseline']['summary']['solve_rate']:.4f}`",
            f"- validation v2 solve_rate: `{math_validation['v2']['summary']['solve_rate']:.4f}`",
            f"- validation delta: `{math_validation['delta']['solve_rate']:+.4f}`",
            f"- currently running full job ids: `{short_json(report['aflow_realignment']['full_job_ids'])}`",
            f"- repeat1 partial status: `{short_json(report['aflow_realignment']['math_repeat1_partial'])}`",
            "",
            "### HumanEval",
            f"- historical pre-v2 full run: `{rel(report['benchmarks']['humaneval']['historical_full_run']['run_dir'])}`",
            f"- validation baseline pass@1: `{humaneval_validation['baseline']['summary']['pass_at_1']:.4f}`",
            f"- validation v2 pass@1: `{humaneval_validation['v2']['summary']['pass_at_1']:.4f}`",
            f"- validation delta: `{humaneval_validation['delta']['pass_at_1']:+.4f}`",
            f"- repeat1 partial status: `{short_json(report['aflow_realignment']['humaneval_repeat1_partial'])}`",
            "",
        ]
    )

    math = report["benchmarks"]["math"]
    lines.extend(
        [
            "## Benchmark: MATH",
            "",
            "### Status",
            "- current status: `in_progress`",
            "- historical full aggregate is invalid and must not be reported as a final benchmark number",
            f"- validation pilot run: `{rel(math_validation['v2']['run_dir'])}`",
            f"- validation task count: `{math_validation['v2']['summary']['task_count']}`",
            f"- validation solve_rate: `{math_validation['v2']['summary']['solve_rate']:.4f}`",
            "",
            "### Workflow Design",
            *format_workflow_md("Historical Pre-Realignment Workflow", math["historical_workflow"]),
            *format_workflow_md("Current AFlow-Aligned Workflow", math["current_workflow"]),
            "",
            "### Representative Results",
            *render_example("selector override success", math["representative_examples"]["selector_override_success"]),
            *render_example("validation failure example", math["representative_examples"]["validation_failure_example"]),
            *render_example("in-progress reviewed failure", math["representative_examples"]["in_progress_review_failure"]),
            "",
            "### Interpretation",
        ]
    )
    for item in math["current_takeaways"]:
        lines.append(f"- {item}")

    humaneval = report["benchmarks"]["humaneval"]
    lines.extend(
        [
            "",
            "## Benchmark: HumanEval",
            "",
            "### Status",
            "- current status: `in_progress`",
            f"- historical pre-v2 full run: `{rel(humaneval['historical_full_run']['run_dir'])}`",
            f"- validation pilot run: `{rel(humaneval_validation['v2']['run_dir'])}`",
            f"- validation pass@1: `{humaneval_validation['v2']['summary']['pass_at_1']:.4f}`",
            "",
            "### Workflow Design",
            *format_workflow_md("Historical Pre-Realignment Workflow", humaneval["historical_workflow"]),
            *format_workflow_md("Current AFlow-Aligned Workflow", humaneval["current_workflow"]),
            "",
            "### Representative Results",
            *render_example("direct pass example", humaneval["representative_examples"]["direct_pass_example"]),
            *render_example("repair pass example", humaneval["representative_examples"]["repair_pass_example"]),
            "",
            "### Interpretation",
        ]
    )
    for item in humaneval["current_takeaways"]:
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## Benchmark: MedQA",
            "",
            "### Status",
            f"- canonical full run: `{rel(medqa['current_full_run']['run_dir'])}`",
            f"- canonical accuracy: `{medqa['current_full_run']['summary']['accuracy']:.4f}`",
            f"- v2 full rerun: `{rel(medqa['v2_full_run']['run_dir'])}`",
            f"- v2 accuracy: `{medqa['v2_full_run']['summary']['accuracy']:.4f}`",
            f"- delta accuracy: `{medqa['delta_v2_vs_baseline']['accuracy']:+.4f}`",
            f"- delta average_usd: `{medqa['delta_v2_vs_baseline']['average_usd']:+.6f}`",
            f"- delta review count: `{medqa['delta_v2_vs_baseline']['review_count_delta']:+d}`",
            "",
            "### Workflow Design",
            *format_workflow_md("Canonical Baseline Workflow", medqa["current_workflow"]),
            *format_workflow_md("V2 Repair Workflow", medqa["v2_workflow"]),
            "",
            "### Deep Analysis Highlights",
            f"- canonical workflow patterns: `{short_json(medqa['current_full_run']['summary']['workflow_patterns'])}`",
            f"- review failure breakdown: `{short_json(medqa_deep.get('review_failure_breakdown', {}))}`",
            f"- question types present: `{', '.join(sorted((medqa_deep.get('question_type_breakdown') or {}).keys()))}`",
            f"- validation improved task ids: `{short_json(medqa['validation_flips']['improved_task_ids'])}`",
            f"- validation regressed task ids: `{short_json(medqa['validation_flips']['regressed_task_ids'])}`",
            "",
            "### Representative Results",
            *render_example("review success example", medqa["representative_examples"]["review_success_example"]),
            *render_example("review failure example", medqa["representative_examples"]["review_failure_example"]),
            *render_example("v2 failure example", medqa["representative_examples"]["v2_failure_example"]),
            "",
            "### Interpretation",
        ]
    )
    for item in medqa["current_takeaways"]:
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## Historical Budget Sweeps",
            "",
            f"- MATH: `{report['historical_budget_sweeps']['math']['status']}`",
            f"- HumanEval: `{report['historical_budget_sweeps']['humaneval']['status']}`",
            "",
            "## Overall Takeaways",
        ]
    )
    for item in report["overall_takeaways"]:
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## Related Artifacts",
            f"- summary report markdown: `{rel(OUTPUT_DIR / 'benchmark_program_report.md')}`",
            f"- summary report json: `{rel(OUTPUT_DIR / 'benchmark_program_report.json')}`",
            f"- detailed report markdown: `{rel(OUTPUT_DIR / 'benchmark_detailed_report.md')}`",
            f"- detailed report json: `{rel(OUTPUT_DIR / 'benchmark_detailed_report.json')}`",
            f"- Experiment 3 detailed report: `{rel(CASE_STUDY_REPORT)}`",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def write_report_files(report: dict[str, Any], markdown: str) -> dict[str, str]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    outputs = {
        "report_json": OUTPUT_DIR / "benchmark_program_report.json",
        "report_md": OUTPUT_DIR / "benchmark_program_report.md",
        "detailed_report_json": OUTPUT_DIR / "benchmark_detailed_report.json",
        "detailed_report_md": OUTPUT_DIR / "benchmark_detailed_report.md",
    }
    json_text = json.dumps(report, ensure_ascii=True, indent=2)
    for key, path in outputs.items():
        if key.endswith("_json"):
            path.write_text(json_text, encoding="utf-8")
        else:
            path.write_text(markdown, encoding="utf-8")
    return {key: str(path) for key, path in outputs.items()}


def main() -> int:
    report = build_report()
    markdown = render_markdown(report)
    written = write_report_files(report, markdown)
    print(json.dumps(written, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
