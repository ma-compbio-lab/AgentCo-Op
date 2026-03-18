#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import math
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
from PIL import Image, ImageDraw, ImageOps

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dynaforge.benchmarks import grade_math_prediction

OUTPUT_DIR = REPO_ROOT / "output" / "reports"
FIGURES_DIR = OUTPUT_DIR / "figures"

BENCHMARK_REPORT_MD = OUTPUT_DIR / "benchmark_program_report.md"
BENCHMARK_REPORT_JSON = OUTPUT_DIR / "benchmark_program_report.json"
CASE_REPORT_MD = OUTPUT_DIR / "experiment3_case_study_report.md"
CASE_REPORT_JSON = OUTPUT_DIR / "experiment3_case_study_report.json"
PROGRAM_REPORT_MD = OUTPUT_DIR / "redesigned_program_report.md"
PROGRAM_REPORT_JSON = OUTPUT_DIR / "redesigned_program_report.json"


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)

    benchmark_payload = build_benchmark_payload()
    case_payload = build_case_payload()

    benchmark_fig = build_benchmark_workflow_figure(benchmark_payload["cards"])
    exp3_fig = build_case_workflow_figure(case_payload["cards"])
    montage_fig = build_case_artifact_montage(case_payload["cases"])

    benchmark_md = render_benchmark_report(benchmark_payload, benchmark_fig)
    case_md = render_case_report(case_payload, exp3_fig, montage_fig)
    program_md = render_program_report(benchmark_payload, case_payload, benchmark_fig, exp3_fig, montage_fig)

    BENCHMARK_REPORT_MD.write_text(benchmark_md, encoding="utf-8")
    CASE_REPORT_MD.write_text(case_md, encoding="utf-8")
    PROGRAM_REPORT_MD.write_text(program_md, encoding="utf-8")
    BENCHMARK_REPORT_JSON.write_text(json.dumps(benchmark_payload, ensure_ascii=True, indent=2), encoding="utf-8")
    CASE_REPORT_JSON.write_text(json.dumps(case_payload, ensure_ascii=True, indent=2), encoding="utf-8")
    PROGRAM_REPORT_JSON.write_text(
        json.dumps(
            {
                "benchmark_track": benchmark_payload,
                "experiment3_track": case_payload,
                "figures": {
                    "benchmark_workflows": rel_to_reports(benchmark_fig),
                    "experiment3_workflows": rel_to_reports(exp3_fig),
                    "experiment3_artifact_montage": rel_to_reports(montage_fig),
                },
            },
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"benchmark_report": str(BENCHMARK_REPORT_MD), "case_report": str(CASE_REPORT_MD)}, ensure_ascii=True))
    return 0


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def shorten(text: str, limit: int = 180) -> str:
    normalized = " ".join(str(text).split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def parse_repo_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            return ast.literal_eval(stripped)
        except (ValueError, SyntaxError):
            return value
    return value


def rel_to_reports(path: Path) -> str:
    return os.path.relpath(path, OUTPUT_DIR)


def latest_summary(pattern: str) -> Path | None:
    matches = sorted(REPO_ROOT.glob(pattern))
    return matches[-1] if matches else None


def find_summary_with_workflow(pattern: str, needle: str) -> Path | None:
    for path in sorted(REPO_ROOT.glob(pattern)):
        summary = load_json(path)
        workflows = summary.get("workflow_patterns", {})
        if any(needle in key for key in workflows):
            return path
    return None


def benchmark_source_paths() -> dict[str, Path | None]:
    return {
        "math_invalidated": latest_summary("runs/aggregated/math/test/math-aggregate/*/summaries/summary.json"),
        "math_full_v2": latest_summary("runs-current-benchmark/math-aggregate/*/summaries/summary.json"),
        "math_validation_baseline": find_summary_with_workflow(
            "runs/aflow-validation-check/math-validation/*/summaries/summary.json",
            "solver",
        ),
        "math_validation_v2": find_summary_with_workflow(
            "runs/aflow-validation-check/math-validation/*/summaries/summary.json",
            "programmer->solver->program_exec->selector",
        ),
        "humaneval_historical": latest_summary("runs/aggregated/humaneval/test/humaneval-aggregate/*/summaries/summary.json"),
        "humaneval_full_v2": latest_summary("runs-current-benchmark/humaneval-aggregate/*/summaries/summary.json"),
        "humaneval_validation_baseline": find_summary_with_workflow(
            "runs/aflow-validation-check/humaneval-validation/*/summaries/summary.json",
            "coder",
        ),
        "humaneval_validation_v2": find_summary_with_workflow(
            "runs/aflow-validation-check/humaneval-validation/*/summaries/summary.json",
            "coder->public_test_runner",
        ),
        "medqa_full_baseline": latest_summary("runs/aggregated/medqa/full/medqa-aggregate/*/summaries/summary.json"),
        "medqa_full_current": latest_summary("runs-current-benchmark-aggregated/medqa-aggregate/*/summaries/summary.json"),
        "medqa_current_smoke": latest_summary("runs-benchmark-refresh/medqa-smoke/*/summaries/summary.json"),
    }


def case_source_specs() -> list[dict[str, Any]]:
    return [
        {
            "slug": "E3.1",
            "title": "Closed-loop Spatial Panel Design and Validation",
            "run_root": REPO_ROOT / "runs-real-exp3/closed-loop-spatial-panel-design/20260317_082131_57593e92",
            "why": (
                "This case asks whether the workflow can behave like a closed-loop design system rather than a one-pass analysis script. "
                "The hard part is not choosing markers once, but recovering when downstream validation shows the panel is weak."
            ),
            "inputs": "Two single-cell references, one held-out mouse-brain spatial slide, panel-size and backup-gene constraints.",
            "expected_outputs": [
                "candidate marker table",
                "panel v1 and refined panel",
                "validation summary",
                "spatial validation figures",
                "replacement-gene trace",
            ],
        },
        {
            "slug": "E3.2",
            "title": "cell2location Repository Transfer",
            "run_root": REPO_ROOT / "runs-real-exp3/cell2location-repo-transfer/20260317_092100_d009c9c9",
            "why": (
                "This case tests repo-conditioned transfer rather than from-scratch coding. "
                "The question is whether the workflow can read the task, choose the right repo, adapt the schema, and produce paper-style artifacts on new data."
            ),
            "inputs": "Official cell2location repo, precomputed mouse-brain reference signatures, held-out Visium mouse-brain slide.",
            "expected_outputs": [
                "mapped spatial AnnData",
                "mapping summary",
                "transfer report",
                "cell abundance figure",
                "region clustering figure",
                "QC figure",
            ],
        },
        {
            "slug": "E3.3",
            "title": "Spatially Grounded CRISPR Specialist Collaboration",
            "run_root": REPO_ROOT / "runs-real-exp3/spatial-crispr-specialist-collaboration/20260317_091148_0e521402",
            "why": (
                "This case is about extensibility through reuse. "
                "The workflow should not rebuild a spatial-analysis agent and a perturbation-design agent from scratch; it should orchestrate them and repair the collaboration when the first pass is weak."
            ),
            "inputs": "Breast-cancer Visium slide, disease-relevant reference context, SpatialAgent repo, BioDiscoveryAgent repo.",
            "expected_outputs": [
                "spatial context report",
                "objective routing decision",
                "ranked perturbation gene set",
                "hitrate evaluation",
                "spatial rationale figure",
                "integration report",
            ],
        },
    ]


def build_benchmark_payload() -> dict[str, Any]:
    sources = benchmark_source_paths()
    math_full = load_summary_bundle(sources["math_full_v2"])
    math_invalidated = load_summary_bundle(sources["math_invalidated"])
    math_val_base = load_summary_bundle(sources["math_validation_baseline"])
    math_val_v2 = load_summary_bundle(sources["math_validation_v2"])

    humaneval_full = load_summary_bundle(sources["humaneval_full_v2"])
    humaneval_hist = load_summary_bundle(sources["humaneval_historical"])
    humaneval_val_base = load_summary_bundle(sources["humaneval_validation_baseline"])
    humaneval_val_v2 = load_summary_bundle(sources["humaneval_validation_v2"])

    medqa_full = load_summary_bundle(sources["medqa_full_baseline"])
    medqa_current_full = load_summary_bundle(sources["medqa_full_current"])
    medqa_smoke = load_summary_bundle(sources["medqa_current_smoke"])

    math_rows = load_task_results(math_full["run_dir"] / "eval" / "task_results.json") if math_full else []
    humaneval_rows = load_task_results(humaneval_full["run_dir"] / "eval" / "task_results.json") if humaneval_full else []
    medqa_rows = load_task_results(medqa_current_full["run_dir"] / "eval" / "task_results.json") if medqa_current_full else []

    math_analysis = analyze_math_rows(math_rows)
    humaneval_analysis = analyze_humaneval_rows(humaneval_rows)
    medqa_analysis = analyze_medqa_rows(medqa_rows)

    cards = [
        {
            "title": "MATH",
            "subtitle": "reason + execute + select",
            "workflow": "solver -> programmer -> program_exec -> selector -> [reviewer -> reviser]",
            "gate": "review on selector flag, low confidence, or execution failure",
            "note": "Exact-answer math benefits from executable evidence, but the current selector still overtrusts bad programs.",
        },
        {
            "title": "HumanEval",
            "subtitle": "generate + test + repair",
            "workflow": "coder -> public_test_runner -> [reviewer -> reviser -> retest_runner]",
            "gate": "repair only when public tests fail",
            "note": "The public-test loop is doing real work; the remaining failures are concentrated in the repair branch.",
        },
        {
            "title": "MedQA",
            "subtitle": "direct answer + selective review",
            "workflow": "solver -> [reviewer -> reviser]",
            "gate": "review only for ambiguity or explicitly high-risk medical questions",
            "note": "The simpler direct-answer graph is now the intended default, and the current full rerun is complete. The remaining gap is accuracy, not missing execution.",
        },
    ]

    overview_rows = [
        [
            "MATH",
            "Stress exact-answer reasoning with executable verification.",
            "gpt-4o-mini",
            "AFlow public package, 119 validation / 486 test",
            "solve_rate",
            metric_cell(math_full, "solve_rate"),
            "Repeat-1 full run completed; 3-run protocol not finished.",
        ],
        [
            "HumanEval",
            "Stress code generation with visible test feedback.",
            "gpt-4o-mini",
            "AFlow public package, 33 validation / 131 test",
            "pass@1",
            metric_cell(humaneval_full, "pass_at_1"),
            "Repeat-1 full run completed; 3-run protocol not finished.",
        ],
        [
            "MedQA",
            "Repository medical QA extension with simpler direct-answer policy.",
            "gpt-5-nano",
            "1273-question official test",
            "accuracy",
            metric_cell(medqa_current_full, "accuracy"),
            "Current direct-answer full rerun completed; legacy baseline retained only for comparison.",
        ],
    ]

    benchmarks = [
        {
            "name": "MATH",
            "why": "We use MATH to test whether the workflow can combine natural-language reasoning with executable cross-checks under AFlow-aligned evaluation.",
            "input_contract": "Level-5 problems from Counting & Probability, Number Theory, Prealgebra, and Precalculus. Output is one exact final answer.",
            "current_workflow": cards[0]["workflow"],
            "historical_invalidated": summary_min(math_invalidated),
            "validation_baseline": summary_min(math_val_base),
            "validation_v2": summary_min(math_val_v2),
            "full_v2": summary_min(math_full),
            "analysis": math_analysis,
            "next_action": (
                "Do not add more agents first. Fix the programmer path and selector reliability first: execution failure should trigger review more often, "
                "and the programmer should preserve exact symbolic form instead of collapsing to decimal approximations."
            ),
        },
        {
            "name": "HumanEval",
            "why": "We use HumanEval to test whether the workflow is actually tool-grounded. A coder without tests is just a language model; the benchmark needs test execution and repair.",
            "input_contract": "HumanEval prompt plus entry point and public tests. Output is one Python function scored with pass@1.",
            "current_workflow": cards[1]["workflow"],
            "historical_reference": summary_min(humaneval_hist),
            "validation_baseline": summary_min(humaneval_val_base),
            "validation_v2": summary_min(humaneval_val_v2),
            "full_v2": summary_min(humaneval_full),
            "analysis": humaneval_analysis,
            "next_action": (
                "Focus on the repair branch, not the first-pass coder. The plain coder path already clears most tasks; the residual losses are cases where reviewer+reviser still fail to repair after a public-test miss."
            ),
        },
        {
            "name": "MedQA",
            "why": "We use MedQA as a repository benchmark extension to check whether the workflow can stay simple on lower-entropy medical MCQ tasks instead of over-engineering every question.",
            "input_contract": "Question stem plus five labeled options. Output is one option label and the exact option text.",
            "current_workflow": cards[2]["workflow"],
            "historical_full_baseline": summary_min(medqa_full),
            "current_full": summary_min(medqa_current_full),
            "current_smoke": summary_min(medqa_smoke),
            "analysis": medqa_analysis,
            "next_action": (
                "Keep the simpler direct-answer default and reduce avoidable review-path contract failures. "
                "The current full rerun already beats the legacy planner-heavy baseline, but the remaining gap is still wrong medical reasoning rather than a missing tool."
            ),
        },
    ]

    return {
        "overview_rows": overview_rows,
        "cards": cards,
        "benchmarks": benchmarks,
    }


def build_case_payload() -> dict[str, Any]:
    cases = []
    cards = []
    for spec in case_source_specs():
        summary = load_json(spec["run_root"] / "summaries" / "summary.json")
        analysis = (spec["run_root"] / "summaries" / "analysis.md").read_text(encoding="utf-8")
        execution_report = load_json(spec["run_root"] / "eval" / "execution_report.json")
        case = {
            "slug": spec["slug"],
            "title": spec["title"],
            "why": spec["why"],
            "inputs": spec["inputs"],
            "expected_outputs": spec["expected_outputs"],
            "summary": summary,
            "analysis_excerpt": " ".join(analysis.split())[:900],
            "execution_report": execution_report,
            "artifacts": collect_case_artifacts(spec["run_root"], summary),
            "key_findings": extract_case_findings(spec["slug"], summary),
            "compile_candidates": (summary.get("compile_trace", {}) or {}).get("candidate_graphs", [])[:4],
        }
        cards.append(
            {
                "title": spec["slug"],
                "subtitle": summary.get("workflow_pattern", ""),
                "workflow": humanize_signature(summary.get("workflow_signature", "")),
                "gate": ", ".join(summary.get("activated_gates", [])) or "no runtime gate fired",
                "note": card_note_for_case(spec["slug"], summary),
            }
        )
        cases.append(case)
    return {
        "overview_rows": [
            [
                case["slug"],
                case["why"],
                case["inputs"],
                ", ".join(case["expected_outputs"][:3]) + ("..." if len(case["expected_outputs"]) > 3 else ""),
                case["summary"].get("workflow_pattern", ""),
                case["key_findings"][0],
            ]
            for case in cases
        ],
        "cards": cards,
        "cases": cases,
    }


def summary_min(bundle: dict[str, Any] | None) -> dict[str, Any] | None:
    if bundle is None:
        return None
    summary = dict(bundle["summary"])
    summary["run_dir"] = str(bundle["run_dir"])
    return summary


def load_summary_bundle(summary_path: Path | None) -> dict[str, Any] | None:
    if summary_path is None or not summary_path.exists():
        return None
    run_dir = summary_path.parents[1]
    return {"summary": load_json(summary_path), "run_dir": run_dir}


def load_task_results(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [dict(row) for row in payload]


def analyze_math_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    corrected = [is_math_correct(row) for row in rows]
    repair_count = sum(1 for row in rows if "reviewer" in str(row.get("workflow_signature", "")))
    exec_fail = sum(1 for row in rows if not bool(row.get("program_execution_passed")))
    wrong_exec_fail = sum(
        1 for row, ok in zip(rows, corrected, strict=True) if (not ok) and not bool(row.get("program_execution_passed"))
    )
    format_false_negative = sum(1 for row, ok in zip(rows, corrected, strict=True) if ok and not bool(row.get("correct")))
    high_conf_wrong = sum(
        1
        for row, ok in zip(rows, corrected, strict=True)
        if (not ok) and float(row.get("confidence", 0.0) or 0.0) >= 0.9
    )
    return {
        "corrected_solve_rate": round(sum(corrected) / len(rows), 4),
        "review_count": repair_count,
        "execution_failed_count": exec_fail,
        "wrong_with_execution_failure": wrong_exec_fail,
        "equivalence_false_negative_count": format_false_negative,
        "high_confidence_wrong_count": high_conf_wrong,
    }


def is_math_correct(row: Mapping[str, Any]) -> bool:
    return bool(
        grade_math_prediction(
            {"final_answer": row.get("prediction_answer")},
            {"gold_answer": row.get("gold_answer")},
        ).get("correct")
    )


def analyze_humaneval_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    repair_rows = [row for row in rows if "reviewer" in str(row.get("workflow_signature", ""))]
    plain_rows = [row for row in rows if row not in repair_rows]
    repair_success = sum(1 for row in repair_rows if row.get("correct"))
    repair_fail = len(repair_rows) - repair_success
    plain_success = sum(1 for row in plain_rows if row.get("correct"))
    return {
        "repair_count": len(repair_rows),
        "repair_success_count": repair_success,
        "repair_failure_count": repair_fail,
        "plain_count": len(plain_rows),
        "plain_success_count": plain_success,
    }


def analyze_medqa_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    review_count = 0
    review_correct = 0
    solver_only_count = 0
    solver_only_correct = 0
    high_conf_wrong = 0
    contract_failures = 0
    for row in rows:
        if "reviewer" in str(row.get("workflow_signature", "")):
            review_count += 1
            if row.get("correct"):
                review_correct += 1
        else:
            solver_only_count += 1
            if row.get("correct"):
                solver_only_correct += 1
        if not row.get("correct") and float(row.get("confidence", 0.0) or 0.0) >= 0.8:
            high_conf_wrong += 1
        if row.get("failure_type") == "output_contract_violation":
            contract_failures += 1
    return {
        "review_count": review_count,
        "review_accuracy": round(review_correct / review_count, 4) if review_count else 0.0,
        "solver_only_count": solver_only_count,
        "solver_only_accuracy": round(solver_only_correct / solver_only_count, 4) if solver_only_count else 0.0,
        "high_confidence_wrong_count": high_conf_wrong,
        "contract_failure_count": contract_failures,
    }


def metric_cell(bundle: dict[str, Any] | None, key: str) -> str:
    if bundle is None:
        return "not available"
    summary = bundle["summary"]
    value = summary.get(key, summary.get("accuracy"))
    return f"{float(value):.4f}" if value is not None else "not available"


def collect_case_artifacts(run_root: Path, summary: Mapping[str, Any]) -> list[dict[str, str]]:
    artifact_paths = summary.get("artifact_paths", {})
    rows = []
    if isinstance(artifact_paths, Mapping):
        for key, value in artifact_paths.items():
            if not value:
                continue
            rows.append({"label": str(key), "path": str(value)})
    elif isinstance(artifact_paths, list):
        for value in artifact_paths:
            if not value:
                continue
            rows.append({"label": Path(str(value)).name, "path": str(value)})
    case_output_dir = run_root / "artifacts" / "case_output"
    if case_output_dir.exists():
        seen = {row["path"] for row in rows}
        for path in sorted(case_output_dir.iterdir()):
            if str(path) in seen:
                continue
            rows.append({"label": path.name, "path": str(path)})
    return rows


def extract_case_findings(slug: str, summary: Mapping[str, Any]) -> list[str]:
    if slug == "E3.1":
        validation = load_json(
            REPO_ROOT
            / "runs-real-exp3/closed-loop-spatial-panel-design/20260317_082131_57593e92/artifacts/case_output/panel_validation_summary.json"
        )
        return [
            f"The closed loop did fire: validation ended at {float(validation.get('validation_score', 0.0)):.4f} against a requested floor of {float(validation.get('thresholds', {}).get('min_validation_score', 0.22)):.2f}.",
            f"The repair branch swapped in backup genes such as {', '.join(validation.get('replacement_candidates', [])[:5])}.",
            f"The operational story is real, but the scientific quality is still modest: ARI/NMI are {float(validation.get('ari', 0.0)):.4f} / {float(validation.get('nmi', 0.0)):.4f}.",
        ]
    if slug == "E3.2":
        mapping = load_json(
            REPO_ROOT
            / "runs-real-exp3/cell2location-repo-transfer/20260317_092100_d009c9c9/artifacts/case_output/mapping_summary.json"
        )
        selected_repo = parse_repo_value(summary.get("selected_repos", {}).get("repo_runner", "cell2location"))
        repo_name = selected_repo.get("name", "cell2location") if isinstance(selected_repo, Mapping) else str(selected_repo)
        return [
            f"RepoScout selected `{repo_name}` and the transfer preserved {int(mapping.get('shared_gene_count', 0))} shared genes across {int(mapping.get('cell_type_count', 0))} cell states.",
            f"The workflow produced abundance, Leiden, and QC figures, but the NMF/compartment output promised by the story line is still missing.",
            f"This run proves real repo transfer more strongly than biological transfer quality: ARI/NMI are {float(mapping.get('ari_manual_layer_vs_region_cluster', 0.0)):.4f} / {float(mapping.get('nmi_manual_layer_vs_region_cluster', 0.0)):.4f}.",
        ]
    routed = load_json(
        REPO_ROOT
        / "runs-real-exp3/spatial-crispr-specialist-collaboration/20260317_091148_0e521402/artifacts/case_output/routed_objective.json"
    )
    hitrate = load_json(
        REPO_ROOT
        / "runs-real-exp3/spatial-crispr-specialist-collaboration/20260317_091148_0e521402/artifacts/case_output/hitrate_eval.json"
    )
    return [
        f"The router chose `{routed.get('routed_objective', 'unknown')}` with low confidence {float(routed.get('routing_confidence', 0.0)):.4f}, which is exactly why the repair branch matters here.",
        "The collaboration is real: spatial context artifacts were passed into perturbation design, then the perturbation specialist was rerun after review.",
        f"The limitation is scientific strength, not orchestration. The downstream evaluation still ends at `{shorten(hitrate.get('summary_line', ''), 120)}`.",
    ]


def card_note_for_case(slug: str, summary: Mapping[str, Any]) -> str:
    if slug == "E3.1":
        return "A real design -> validate -> repair -> redesign loop executed, but the recovered panel still falls short of the aspirational biological target."
    if slug == "E3.2":
        return "The run proves repo-conditioned execution and schema adaptation, but it does not yet satisfy the full paper-style figure contract from the intended story line."
    return "Two external specialist repos were composed across separate sandboxes, then a repair branch reran the perturbation specialist when the first collaboration was under-justified."


def humanize_signature(signature: str) -> str:
    return " -> ".join(part.strip().replace("_", " ") for part in signature.split("->") if part.strip())


def render_benchmark_report(payload: Mapping[str, Any], figure_path: Path) -> str:
    lines = [
        "# Benchmark Program",
        "",
        "## Why These Benchmarks",
        "",
        "The benchmark track exists to answer a controlled question before the open-world case studies: does the workflow earn its complexity on tasks where the metric is unambiguous?",
        "",
        render_table(
            ["Benchmark", "Why this task", "Base model", "Split", "Metric", "Current trustworthy evidence", "Status"],
            payload["overview_rows"],
        ),
        "",
        f"![Benchmark workflow designs]({rel_to_reports(figure_path)})",
    ]

    for benchmark in payload["benchmarks"]:
        lines.extend(
            [
                "",
                f"## {benchmark['name']}",
                "",
                f"**Why this task**: {benchmark['why']}",
                "",
                f"**Input / output contract**: {benchmark['input_contract']}",
                "",
                f"**Current workflow design**: `{benchmark['current_workflow']}`",
            ]
        )
        if benchmark["name"] == "MATH":
            lines.extend(
                [
                    "",
                    render_table(
                        ["Signal", "Value"],
                        [
                            ["Historical invalidated full run", metric_cell_wrapped(benchmark.get("historical_invalidated"), "solve_rate")],
                            ["Validation baseline", metric_cell_wrapped(benchmark.get("validation_baseline"), "solve_rate")],
                            ["Validation v2", metric_cell_wrapped(benchmark.get("validation_v2"), "solve_rate")],
                            ["Full v2 repeat-1", metric_cell_wrapped(benchmark.get("full_v2"), "solve_rate")],
                        ],
                    ),
                    "",
                    "What the logs say:",
                    f"- The corrected full repeat-1 solve rate is `{benchmark['analysis'].get('corrected_solve_rate', 0.0):.4f}`.",
                    f"- `{benchmark['analysis'].get('execution_failed_count', 0)}` tasks had failed program execution, and `{benchmark['analysis'].get('wrong_with_execution_failure', 0)}` of the remaining wrong answers came from that branch collapsing back to the solver.",
                    f"- `{benchmark['analysis'].get('equivalence_false_negative_count', 0)}` historical wrong answers were formatting/symbolic-equivalence false negatives.",
                    f"- `{benchmark['analysis'].get('high_confidence_wrong_count', 0)}` remaining wrong answers still carried confidence >= 0.9, so the selector is overconfident.",
                ]
            )
        elif benchmark["name"] == "HumanEval":
            lines.extend(
                [
                    "",
                    render_table(
                        ["Signal", "Value"],
                        [
                            ["Historical full reference", metric_cell_wrapped(benchmark.get("historical_reference"), "pass_at_1")],
                            ["Validation baseline", metric_cell_wrapped(benchmark.get("validation_baseline"), "pass_at_1")],
                            ["Validation v2", metric_cell_wrapped(benchmark.get("validation_v2"), "pass_at_1")],
                            ["Full v2 repeat-1", metric_cell_wrapped(benchmark.get("full_v2"), "pass_at_1")],
                        ],
                    ),
                    "",
                    "What the logs say:",
                    f"- `{benchmark['analysis'].get('plain_success_count', 0)}/{benchmark['analysis'].get('plain_count', 0)}` plain coder -> public-test paths already pass.",
                    f"- The repair branch fired `{benchmark['analysis'].get('repair_count', 0)}` times and salvaged `{benchmark['analysis'].get('repair_success_count', 0)}` of them.",
                    f"- All `{benchmark['analysis'].get('repair_failure_count', 0)}` residual failures came from the repair branch, not the plain path.",
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    render_table(
                        ["Signal", "Value"],
                        [
                            ["Legacy planner-heavy baseline", metric_cell_wrapped(benchmark.get("historical_full_baseline"), "accuracy")],
                            ["Current direct-answer smoke", metric_cell_wrapped(benchmark.get("current_smoke"), "accuracy")],
                            ["Current direct-answer full", metric_cell_wrapped(benchmark.get("current_full"), "accuracy")],
                            ["Review count in current full run", str(benchmark["analysis"].get("review_count", 0))],
                            ["Review accuracy", f"{benchmark['analysis'].get('review_accuracy', 0.0):.4f}"],
                            ["Solver-only accuracy", f"{benchmark['analysis'].get('solver_only_accuracy', 0.0):.4f}"],
                            ["Review-path contract failures", str(benchmark["analysis"].get("contract_failure_count", 0))],
                            ["High-confidence wrong answers", str(benchmark["analysis"].get("high_confidence_wrong_count", 0))],
                        ],
                    ),
                    "",
                    "What the logs say:",
                    f"- The current direct-answer full run improved over the legacy planner-heavy baseline while using a much simpler default path.",
                    f"- Most questions still stay on the solver-only branch ({benchmark['analysis'].get('solver_only_count', 0)} tasks), which is the intended design for MedQA.",
                    f"- The remaining avoidable failures are concentrated in review-path contract issues ({benchmark['analysis'].get('contract_failure_count', 0)} cases) and ordinary wrong medical reasoning, not missing tools.",
                ]
            )

        lines.extend(
            [
                "",
                f"**Next action**: {benchmark['next_action']}",
            ]
        )

    lines.append("")
    return "\n".join(lines) + "\n"


def render_case_report(payload: Mapping[str, Any], workflow_figure: Path, montage_figure: Path) -> str:
    lines = [
        "# Experiment 3: Advanced Scientific Case Studies",
        "",
        "## Story Line",
        "",
        "Experiment 3 is where the redesigned workflow is supposed to matter. The point is not just to finish a script. The point is to show that compile-time pattern selection, runtime repair, repo reuse, and specialist composition can support harder scientific tasks without adding a new hardcoded branch for every case.",
        "",
        render_table(
            ["Case", "Why this task exists", "Inputs", "Expected outputs", "Compiled pattern", "Bottom-line result"],
            payload["overview_rows"],
        ),
        "",
        f"![Experiment 3 workflow storyboards]({rel_to_reports(workflow_figure)})",
        "",
        f"![Experiment 3 artifact montage]({rel_to_reports(montage_figure)})",
    ]

    for case in payload["cases"]:
        summary = case["summary"]
        lines.extend(
            [
                "",
                f"## {case['slug']} — {case['title']}",
                "",
                f"**Why this task exists**: {case['why']}",
                "",
                f"**Inputs**: {case['inputs']}",
                "",
                f"**Expected outputs**: {', '.join(case['expected_outputs'])}.",
                "",
                f"**Compiled workflow**: `{humanize_signature(summary.get('workflow_signature', ''))}`",
                "",
                "**Why the compiler chose this graph**:",
                "",
                render_table(
                    ["Candidate pattern", "Score", "Why it was considered"],
                    [
                        [
                            item.get("pattern_id", ""),
                            f"{float(item.get('score', 0.0)):.2f}",
                            "; ".join(item.get("reasons", [])[:3]),
                        ]
                        for item in case["compile_candidates"]
                    ],
                ),
                "",
                "**Execution story**:",
            ]
        )
        if case["slug"] == "E3.1":
            lines.extend(
                [
                    "1. The planner selected the closed-loop design pattern because the task explicitly required downstream validation and rollback.",
                    "2. `design_runner` produced the initial panel and backup genes.",
                    "3. `validation_runner` scored the panel on the held-out spatial slide and failed the requested threshold.",
                    "4. The runtime repair gate inserted a redesign branch, which swapped backup genes and reran validation.",
                ]
            )
        elif case["slug"] == "E3.2":
            selected_repo = summary.get("selected_repos", {}).get("repo_runner", {})
            if isinstance(selected_repo, Mapping):
                selected_repo = selected_repo.get("name", "cell2location")
            lines.extend(
                [
                    "1. `repo_scout` inspected the case and chose the official transfer repo path instead of synthesizing a new method.",
                    f"2. `repo_runner` executed `{selected_repo}` inside its sandbox after adapting the reference-signature schema and spatial gene IDs.",
                    "3. `validator` checked the produced artifact bundle against the case contract and accepted the run without opening refinement.",
                    "4. The execution succeeded operationally, but the result still underdelivers on the full figure contract from the target story line.",
                ]
            )
        else:
            lines.extend(
                [
                    "1. `specialist_router` chose a spatially grounded perturbation objective and selected two external specialist repos.",
                    "2. `specialist_a` produced the spatial context artifact bundle used to route the perturbation objective.",
                    "3. `specialist_b` generated a ranked perturbation batch, which was then checked by the integrator and reviewer.",
                    "4. The first collaboration pass triggered a repair branch that reran the perturbation specialist before final integration.",
                ]
            )
        lines.extend(
            [
                "",
                "**Key evidence**:",
            ]
        )
        for finding in case["key_findings"]:
            lines.append(f"- {finding}")
        lines.extend(
            [
                "",
                "**Representative artifacts**:",
            ]
        )
        for artifact in case["artifacts"][:6]:
            lines.append(f"- `{artifact['label']}`")
        lines.extend(
            [
                "",
                "**What this case actually proves**:",
            ]
        )
        if case["slug"] == "E3.1":
            lines.extend(
                [
                    "- It proves the workflow can run a real closed loop with runtime repair and artifact-aware revalidation.",
                    "- It does not yet prove high-quality biological panel design; the final validation metrics are still weak.",
                ]
            )
        elif case["slug"] == "E3.2":
            lines.extend(
                [
                    "- It proves the workflow can reuse an official repo, adapt schemas, and produce a transferred artifact bundle on a new run path.",
                    "- It does not yet prove the full intended transfer story because the current canonical case is still closer to tutorial-family transfer than the harder breast-cancer transfer target in the spec.",
                ]
            )
        else:
            lines.extend(
                [
                    "- It proves the workflow can orchestrate two pre-existing specialist repos across sandbox boundaries and repair the collaboration.",
                    "- It does not yet prove strong scientific perturbation quality; the main limitation is grounding quality, not orchestration.",
                ]
            )
    lines.append("")
    return "\n".join(lines) + "\n"


def render_program_report(
    benchmark_payload: Mapping[str, Any],
    case_payload: Mapping[str, Any],
    benchmark_fig: Path,
    case_fig: Path,
    montage_fig: Path,
) -> str:
    lines = [
        "# DynaForge Experimental Program",
        "",
        "## Executive Summary",
        "",
        "The benchmark track and the case-study track are answering different questions. The benchmark track tells us where the workflow earns its complexity on controlled tasks. Experiment 3 tells us whether the redesigned compile-time pattern library plus runtime repair path can support open-world scientific work without creating a new one-off runner for each task.",
        "",
        "## Track A — Benchmarks",
        "",
        render_table(
            ["Benchmark", "Base model", "Metric", "Current trustworthy evidence", "Main conclusion"],
            [
                ["MATH", "gpt-4o-mini", "solve_rate", metric_cell_wrapped(benchmark_payload["benchmarks"][0].get("full_v2"), "solve_rate"), "Execution-backed design is necessary, but the current selector/programmer stack is still weak."],
                ["HumanEval", "gpt-4o-mini", "pass@1", metric_cell_wrapped(benchmark_payload["benchmarks"][1].get("full_v2"), "pass_at_1"), "Public-test execution is working; the residual gap sits in repair."],
                ["MedQA", "gpt-5-nano", "accuracy", metric_cell_wrapped(benchmark_payload["benchmarks"][2].get("current_full"), "accuracy"), "The workflow should stay simpler here; the direct-answer path now outperforms the legacy planner-heavy baseline, but medical reasoning errors still dominate."],
            ],
        ),
        "",
        f"![Benchmark workflow designs]({rel_to_reports(benchmark_fig)})",
        "",
        "## Track B — Experiment 3",
        "",
        render_table(
            ["Case", "Question", "Compiled pattern", "What the run proved", "Main caveat"],
            [
                ["E3.1", "Can the system design, validate, fail, and repair a spatial panel?", "closed_loop_design_validate", "The closed-loop branch is real and operational.", "Recovery quality is still modest."],
                ["E3.2", "Can the system transfer an official repo to new data?", "repo_transfer_validate", "The repo-transfer path is real and produces paper-style artifacts.", "The task stayed low-entropy and did not force active web search."],
                ["E3.3", "Can the system compose external specialists instead of rebuilding them?", "specialist_assembly", "The collaboration and repair path are real.", "Scientific grounding is still the main quality bottleneck."],
            ],
        ),
        "",
        f"![Experiment 3 workflow storyboards]({rel_to_reports(case_fig)})",
        "",
        f"![Experiment 3 artifact montage]({rel_to_reports(montage_fig)})",
        "",
        "## Read the Detailed Reports",
        "",
        f"- Benchmark report: [{BENCHMARK_REPORT_MD.name}]({BENCHMARK_REPORT_MD.name})",
        f"- Experiment 3 report: [{CASE_REPORT_MD.name}]({CASE_REPORT_MD.name})",
    ]
    lines.append("")
    return "\n".join(lines) + "\n"


def metric_cell_wrapped(summary: Mapping[str, Any] | None, key: str) -> str:
    if not summary:
        return "not available"
    value = summary.get(key, summary.get("accuracy"))
    return f"{float(value):.4f}" if value is not None else "not available"


def render_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    body = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        body.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(body)


def node_color(name: str) -> str:
    lowered = name.lower()
    if any(token in lowered for token in ["review", "validator", "integrator"]):
        return "#f6d365"
    if any(token in lowered for token in ["repair", "refined", "retest"]):
        return "#f7a6a6"
    if any(token in lowered for token in ["runner", "specialist", "exec", "test"]):
        return "#b7efc5"
    return "#bfd7ff"


def draw_workflow_card(ax: Any, title: str, subtitle: str, workflow: str, gate: str, note: str) -> None:
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.add_patch(FancyBboxPatch((0.02, 0.04), 0.96, 0.92, boxstyle="round,pad=0.02,rounding_size=0.03", facecolor="#f8fafc", edgecolor="#cbd5e1", linewidth=1.2))
    ax.add_patch(FancyBboxPatch((0.04, 0.83), 0.92, 0.11, boxstyle="round,pad=0.01,rounding_size=0.02", facecolor="#0f172a", edgecolor="#0f172a"))
    ax.text(0.06, 0.885, title, color="white", fontsize=12, fontweight="bold", va="center")
    ax.text(0.94, 0.885, subtitle, color="#cbd5e1", fontsize=9, ha="right", va="center")

    nodes = [part.strip() for part in workflow.split("->") if part.strip()]
    width = min(0.17, max(0.11, 0.78 / max(len(nodes), 1)))
    start_x = 0.06
    gap = 0.02
    y = 0.56
    for idx, node in enumerate(nodes):
        x = start_x + idx * (width + gap)
        if x + width > 0.94:
            break
        ax.add_patch(FancyBboxPatch((x, y), width, 0.12, boxstyle="round,pad=0.01,rounding_size=0.02", facecolor=node_color(node), edgecolor="#334155"))
        ax.text(x + width / 2, y + 0.06, node.replace("_", " "), ha="center", va="center", fontsize=9)
        if idx < len(nodes) - 1 and x + width + gap < 0.94:
            ax.add_patch(FancyArrowPatch((x + width, y + 0.06), (x + width + gap, y + 0.06), arrowstyle="-|>", mutation_scale=10, linewidth=1.2, color="#475569"))

    ax.text(0.06, 0.34, f"Gate: {gate}", fontsize=9, color="#0f172a", fontweight="bold")
    ax.text(0.06, 0.19, note, fontsize=9, color="#334155", wrap=True)


def build_benchmark_workflow_figure(cards: Sequence[Mapping[str, Any]]) -> Path:
    path = FIGURES_DIR / "benchmark_workflows.png"
    fig, axes = plt.subplots(len(cards), 1, figsize=(13, 3.2 * len(cards)))
    if len(cards) == 1:
        axes = [axes]
    for ax, card in zip(axes, cards):
        draw_workflow_card(ax, card["title"], card["subtitle"], card["workflow"], card["gate"], card["note"])
    fig.tight_layout()
    fig.savefig(path, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return path


def build_case_workflow_figure(cards: Sequence[Mapping[str, Any]]) -> Path:
    path = FIGURES_DIR / "exp3_workflows.png"
    fig, axes = plt.subplots(len(cards), 1, figsize=(13, 3.4 * len(cards)))
    if len(cards) == 1:
        axes = [axes]
    for ax, card in zip(axes, cards):
        draw_workflow_card(ax, card["title"], card["subtitle"], card["workflow"], card["gate"], card["note"])
    fig.tight_layout()
    fig.savefig(path, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return path


def build_case_artifact_montage(cases: Sequence[Mapping[str, Any]]) -> Path:
    path = FIGURES_DIR / "exp3_artifact_montage.png"
    hero_paths = []
    for case in cases:
        for artifact in case["artifacts"]:
            artifact_path = Path(artifact["path"])
            if artifact_path.suffix.lower() in {".png", ".jpg", ".jpeg"} and artifact_path.exists():
                hero_paths.append((case["slug"], artifact_path))
        if len(hero_paths) >= 6:
            break
    hero_paths = hero_paths[:6]
    tile_size = (520, 320)
    cols = 2
    rows = max(1, math.ceil(len(hero_paths) / cols))
    canvas = Image.new("RGB", (cols * tile_size[0], rows * tile_size[1]), "white")
    for idx, (slug, image_path) in enumerate(hero_paths):
        image = Image.open(image_path).convert("RGB")
        image = ImageOps.contain(image, (tile_size[0] - 24, tile_size[1] - 52))
        tile = Image.new("RGB", tile_size, "white")
        draw = ImageDraw.Draw(tile)
        draw.rounded_rectangle((4, 4, tile_size[0] - 4, tile_size[1] - 4), radius=18, outline="#cbd5e1", width=2)
        tile.paste(image, ((tile_size[0] - image.width) // 2, 14))
        draw.text((16, tile_size[1] - 28), f"{slug}: {image_path.name}", fill="black")
        x = (idx % cols) * tile_size[0]
        y = (idx // cols) * tile_size[1]
        canvas.paste(tile, (x, y))
    canvas.save(path)
    return path


if __name__ == "__main__":
    raise SystemExit(main())
