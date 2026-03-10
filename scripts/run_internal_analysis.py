from __future__ import annotations

import argparse
import json
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _latest_successful_run_dirs(root: Path, *, limit: int = 2) -> list[Path]:
    candidates: list[tuple[str, Path]] = []
    for summary_path in sorted(root.glob("*/summaries/summary.json")):
        summary = _load_json(summary_path)
        if summary.get("success") is True:
            candidates.append((summary_path.parent.parent.name, summary_path.parent.parent))
    candidates.sort(key=lambda item: item[0])
    return [path for _, path in candidates[-limit:]]


def _normalize_generated_summary(summary_payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(summary_payload)
    for key in ("output_dir", "figure_path", "summary_path", "raw_data_path"):
        normalized.pop(key, None)
    return normalized


def _load_case_generated_summary(run_dir: Path) -> dict[str, Any]:
    summary = _load_json(run_dir / "summaries" / "summary.json")
    tool_output = summary.get("tool_output", {})
    tool_summary = tool_output.get("summary", {}) if isinstance(tool_output, dict) else {}
    return _normalize_generated_summary(dict(tool_summary))


def _workflow_nodes(signature: str) -> list[str]:
    return [node for node in signature.split("->") if node]


def _review_rate(task_results: list[dict[str, Any]]) -> float:
    if not task_results:
        return 0.0
    reviewed = sum(1 for item in task_results if "reviewer" in str(item.get("workflow_signature", "")))
    return reviewed / len(task_results)


def _mean_cost(task_results: list[dict[str, Any]], field: str) -> float:
    if not task_results:
        return 0.0
    values = [float(item.get("cost", {}).get(field, 0.0) or 0.0) for item in task_results]
    return sum(values) / len(values)


def _build_workflow_structure_analysis(
    medqa_runs: list[tuple[str, list[dict[str, Any]]]],
    case_study_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    workflow_counter: Counter[str] = Counter()
    node_count_counter: Counter[int] = Counter()
    role_counter: Counter[str] = Counter()
    sandbox_case_counter: Counter[str] = Counter()
    tool_case_counter: Counter[str] = Counter()

    for _, task_results in medqa_runs:
        for item in task_results:
            signature = str(item.get("workflow_signature", ""))
            if not signature:
                continue
            workflow_counter[signature] += 1
            nodes = _workflow_nodes(signature)
            node_count_counter[len(nodes)] += 1
            role_counter.update(nodes)

    for summary in case_study_runs:
        signature = str(summary.get("workflow_signature", ""))
        if signature:
            workflow_counter[signature] += 1
            nodes = _workflow_nodes(signature)
            node_count_counter[len(nodes)] += 1
            role_counter.update(nodes)
            tool_case_counter.update(node for node in nodes if node.endswith("_tool"))
        case_id = str(summary.get("case_id", ""))
        if case_id:
            sandbox_case_counter[case_id] += int(summary.get("cost", {}).get("container_starts", 0) or 0)

    return {
        "workflow_signature_frequency": workflow_counter.most_common(),
        "node_count_distribution": sorted(node_count_counter.items()),
        "role_frequency": role_counter.most_common(),
        "tool_frequency": tool_case_counter.most_common(),
        "sandbox_invocations_by_case": sandbox_case_counter.most_common(),
    }


def _build_failure_taxonomy(
    medqa_runs: list[tuple[str, list[dict[str, Any]]]],
    case_study_run_histories: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    severity = {
        "none": 0,
        "incorrect_answer": 1,
        "low_confidence": 1,
        "output_contract_violation": 2,
        "tool_schema_mismatch": 2,
        "tool_runtime_error": 3,
        "env_missing_dep": 3,
        "env_version_conflict": 3,
        "timeout": 3,
        "budget_exceeded": 3,
        "unknown": 4,
    }
    medqa_counter: Counter[str] = Counter()
    case_counter: Counter[str] = Counter()
    severity_counter: Counter[int] = Counter()

    for _, task_results in medqa_runs:
        for item in task_results:
            failure_type = str(item.get("failure_type", "none") or "none")
            medqa_counter[failure_type] += 1
            severity_counter[severity.get(failure_type, 4)] += 1

    for _, history in case_study_run_histories.items():
        for summary in history:
            failure_type = str(summary.get("failure_type", "none") or "none")
            case_counter[failure_type] += 1
            severity_counter[severity.get(failure_type, 4)] += 1

    return {
        "medqa_failure_frequency": medqa_counter.most_common(),
        "case_study_failure_frequency": case_counter.most_common(),
        "severity_distribution": sorted(severity_counter.items()),
    }


def _build_repair_effectiveness(
    baseline_summary: dict[str, Any],
    baseline_tasks: list[dict[str, Any]],
    repaired_summary: dict[str, Any],
    repaired_tasks: list[dict[str, Any]],
    case_study_run_histories: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    medqa_delta = {
        "baseline_accuracy": float(baseline_summary.get("accuracy", 0.0)),
        "repaired_accuracy": float(repaired_summary.get("accuracy", 0.0)),
        "accuracy_delta": float(repaired_summary.get("accuracy", 0.0)) - float(baseline_summary.get("accuracy", 0.0)),
        "baseline_average_usd": _mean_cost(baseline_tasks, "usd"),
        "repaired_average_usd": _mean_cost(repaired_tasks, "usd"),
        "average_usd_delta": _mean_cost(repaired_tasks, "usd") - _mean_cost(baseline_tasks, "usd"),
        "baseline_review_rate": _review_rate(baseline_tasks),
        "repaired_review_rate": _review_rate(repaired_tasks),
        "review_rate_delta": _review_rate(repaired_tasks) - _review_rate(baseline_tasks),
    }

    case_effects: dict[str, Any] = {}
    for case_name, history in case_study_run_histories.items():
        if not history:
            continue
        history_sorted = sorted(history, key=lambda item: str(item.get("run_dir", "")))
        first = history_sorted[0]
        last = history_sorted[-1]
        case_effects[case_name] = {
            "attempt_count": len(history_sorted),
            "first_failure_type": first.get("failure_type", "none"),
            "first_success": bool(first.get("success", False)),
            "last_failure_type": last.get("failure_type", "none"),
            "last_success": bool(last.get("success", False)),
            "success_improved": bool(last.get("success", False)) and not bool(first.get("success", False)),
        }

    return {
        "medqa_baseline_vs_repaired": medqa_delta,
        "case_study_repair_history": case_effects,
    }


def _build_cost_sanity(
    medqa_runs: list[tuple[str, dict[str, Any], list[dict[str, Any]]]],
    case_study_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    medqa_checks: list[dict[str, Any]] = []
    for run_name, summary, task_results in medqa_runs:
        computed_avg_usd = _mean_cost(task_results, "usd")
        computed_avg_input = _mean_cost(task_results, "input_tokens")
        computed_avg_output = _mean_cost(task_results, "output_tokens")
        medqa_checks.append(
            {
                "run": run_name,
                "reported_average_usd": float(summary.get("average_usd", 0.0)),
                "computed_average_usd": computed_avg_usd,
                "average_usd_delta": computed_avg_usd - float(summary.get("average_usd", 0.0)),
                "reported_average_input_tokens": float(summary.get("average_input_tokens", 0.0)),
                "computed_average_input_tokens": computed_avg_input,
                "reported_average_output_tokens": float(summary.get("average_output_tokens", 0.0)),
                "computed_average_output_tokens": computed_avg_output,
                "task_count_matches": int(summary.get("task_count", 0)) == len(task_results),
            }
        )

    case_checks: list[dict[str, Any]] = []
    for summary in case_study_runs:
        cost = summary.get("cost", {})
        case_checks.append(
            {
                "case_id": summary.get("case_id", ""),
                "run_dir": summary.get("run_dir", ""),
                "usd": float(cost.get("usd", 0.0)),
                "tool_calls": int(cost.get("tool_calls", 0) or 0),
                "container_starts": int(cost.get("container_starts", 0) or 0),
                "hard_checks_all_passed": all(item.get("passed", False) for item in summary.get("hard_checks", [])),
            }
        )
    return {
        "medqa_cost_checks": medqa_checks,
        "case_study_cost_checks": case_checks,
    }


def _build_reproducibility(
    scanpy_successes: list[Path],
    squidpy_successes: list[Path],
) -> dict[str, Any]:
    scanpy_match = False
    if len(scanpy_successes) >= 2:
        scanpy_match = _load_case_generated_summary(scanpy_successes[-1]) == _load_case_generated_summary(scanpy_successes[-2])
    squidpy_match = False
    if len(squidpy_successes) >= 2:
        squidpy_match = _load_case_generated_summary(squidpy_successes[-1]) == _load_case_generated_summary(squidpy_successes[-2])
    return {
        "scanpy_pbmc3k_generated_summary_match": scanpy_match,
        "scanpy_pbmc3k_run_dirs": [str(path) for path in scanpy_successes[-2:]],
        "squidpy_visium_generated_summary_match": squidpy_match,
        "squidpy_visium_run_dirs": [str(path) for path in squidpy_successes[-2:]],
    }


def _render_analysis_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Required Internal Analysis Experiments",
        "",
        "## Workflow structure analysis",
    ]
    for signature, count in payload["workflow_structure"]["workflow_signature_frequency"]:
        lines.append(f"- `{signature}`: {count}")
    lines.extend(
        [
            "",
            "## Failure taxonomy analysis",
        ]
    )
    for failure_type, count in payload["failure_taxonomy"]["medqa_failure_frequency"]:
        lines.append(f"- MedQA `{failure_type}`: {count}")
    for failure_type, count in payload["failure_taxonomy"]["case_study_failure_frequency"]:
        lines.append(f"- Case study `{failure_type}`: {count}")
    lines.extend(
        [
            "",
            "## Repair effectiveness analysis",
            (
                "- MedQA baseline vs repaired: "
                f"accuracy {payload['repair_effectiveness']['medqa_baseline_vs_repaired']['baseline_accuracy']:.4f} -> "
                f"{payload['repair_effectiveness']['medqa_baseline_vs_repaired']['repaired_accuracy']:.4f}, "
                f"avg usd {payload['repair_effectiveness']['medqa_baseline_vs_repaired']['baseline_average_usd']:.6f} -> "
                f"{payload['repair_effectiveness']['medqa_baseline_vs_repaired']['repaired_average_usd']:.6f}, "
                f"review rate {payload['repair_effectiveness']['medqa_baseline_vs_repaired']['baseline_review_rate']:.4f} -> "
                f"{payload['repair_effectiveness']['medqa_baseline_vs_repaired']['repaired_review_rate']:.4f}"
            ),
            "",
            "## Cost accounting sanity check",
        ]
    )
    for item in payload["cost_sanity"]["medqa_cost_checks"]:
        lines.append(
            f"- `{item['run']}`: avg_usd delta={item['average_usd_delta']:.8f}, "
            f"task_count_matches={item['task_count_matches']}"
        )
    lines.extend(
        [
            "",
            "## Reproducibility check",
            (
                f"- Scanpy PBMC3k generated-summary match on last two successful runs: "
                f"{payload['reproducibility']['scanpy_pbmc3k_generated_summary_match']}"
            ),
            (
                f"- Squidpy Visium generated-summary match on last two successful runs: "
                f"{payload['reproducibility']['squidpy_visium_generated_summary_match']}"
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the required internal analysis experiments.")
    parser.add_argument(
        "--baseline-medqa-run",
        default="runs/medqa-aggregate/20260308_064241_944508ed",
    )
    parser.add_argument(
        "--repaired-medqa-run",
        default="runs/medqa-aggregate/20260310_032806_6f57aae7",
    )
    parser.add_argument("--scanpy-run-root", default="runs/scanpy-pbmc3k-case")
    parser.add_argument("--squidpy-run-root", default="runs/squidpy-visium-case")
    parser.add_argument("--base-dir", default="runs")
    args = parser.parse_args(argv)

    baseline_run = Path(args.baseline_medqa_run).resolve()
    repaired_run = Path(args.repaired_medqa_run).resolve()
    scanpy_root = Path(args.scanpy_run_root).resolve()
    squidpy_root = Path(args.squidpy_run_root).resolve()

    baseline_summary = _load_json(baseline_run / "summaries" / "summary.json")
    repaired_summary = _load_json(repaired_run / "summaries" / "summary.json")
    baseline_tasks = _load_json(baseline_run / "eval" / "task_results.json")
    repaired_tasks = _load_json(repaired_run / "eval" / "task_results.json")

    scanpy_history = [_load_json(path) for path in sorted(scanpy_root.glob("*/summaries/summary.json"))]
    squidpy_history = [_load_json(path) for path in sorted(squidpy_root.glob("*/summaries/summary.json"))]
    case_successes = [summary for summary in scanpy_history + squidpy_history if summary.get("success") is True]

    analysis = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "baseline_medqa_run": str(baseline_run),
            "repaired_medqa_run": str(repaired_run),
            "scanpy_run_root": str(scanpy_root),
            "squidpy_run_root": str(squidpy_root),
        },
        "workflow_structure": _build_workflow_structure_analysis(
            medqa_runs=[
                ("medqa_baseline", baseline_tasks),
                ("medqa_repaired", repaired_tasks),
            ],
            case_study_runs=case_successes,
        ),
        "failure_taxonomy": _build_failure_taxonomy(
            medqa_runs=[
                ("medqa_baseline", baseline_tasks),
                ("medqa_repaired", repaired_tasks),
            ],
            case_study_run_histories={
                "scanpy_pbmc3k_umap": scanpy_history,
                "squidpy_visium_hne_spatial": squidpy_history,
            },
        ),
        "repair_effectiveness": _build_repair_effectiveness(
            baseline_summary,
            baseline_tasks,
            repaired_summary,
            repaired_tasks,
            {
                "scanpy_pbmc3k_umap": scanpy_history,
                "squidpy_visium_hne_spatial": squidpy_history,
            },
        ),
        "cost_sanity": _build_cost_sanity(
            medqa_runs=[
                ("medqa_baseline", baseline_summary, baseline_tasks),
                ("medqa_repaired", repaired_summary, repaired_tasks),
            ],
            case_study_runs=case_successes,
        ),
        "reproducibility": _build_reproducibility(
            _latest_successful_run_dirs(scanpy_root, limit=2),
            _latest_successful_run_dirs(squidpy_root, limit=2),
        ),
    }

    run_dir = _ensure_dir(Path(args.base_dir).resolve() / "internal-analysis" / f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}")
    (run_dir / "summaries").mkdir(parents=True, exist_ok=True)
    (run_dir / "summaries" / "internal_analysis.json").write_text(
        json.dumps(analysis, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    (run_dir / "summaries" / "internal_analysis.md").write_text(
        _render_analysis_markdown(analysis),
        encoding="utf-8",
    )
    print(json.dumps({"run_dir": str(run_dir), **analysis["reproducibility"]}, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
