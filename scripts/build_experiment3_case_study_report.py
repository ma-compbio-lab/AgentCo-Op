from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "output" / "reports"


@dataclass(frozen=True)
class CanonicalRun:
    slug: str
    family: str
    run_dir: Path
    label: str
    dataset_id: str | None = None
    dataset_label: str | None = None


CANONICAL_RUNS = [
    CanonicalRun(
        slug="pbmc3k",
        family="smoke",
        label="PBMC3k UMAP reproduction",
        dataset_id="pbmc3k",
        dataset_label="PBMC3k",
        run_dir=PROJECT_ROOT / "runs/scanpy-pbmc3k-case/20260310_061244_f210ee00",
    ),
    CanonicalRun(
        slug="visium_spatial",
        family="smoke",
        label="Visium H&E spatial reproduction",
        dataset_id="visium_hne_crop",
        dataset_label="Visium H&E crop",
        run_dir=PROJECT_ROOT / "runs/squidpy-visium-case/20260310_064504_ed083350",
    ),
    CanonicalRun(
        slug="paul15_trajectory",
        family="trajectory",
        label="Paul15 trajectory reconstruction",
        dataset_id="paul15",
        dataset_label="Paul15",
        run_dir=PROJECT_ROOT / "runs/scanpy-paul15-case/20260311_052516_f66fe04f",
    ),
    CanonicalRun(
        slug="paul15_paper_figure",
        family="trajectory",
        label="Paul15 paper-style figure reproduction",
        run_dir=PROJECT_ROOT / "runs/scanpy-paul15-paper-figure-case/20260311_205017_9f04276f",
    ),
    CanonicalRun(
        slug="moignard15_transfer",
        family="transfer",
        label="Moignard15 method transfer",
        run_dir=PROJECT_ROOT / "runs/scanpy-moignard15-transfer-case/20260311_205017_a59f56c3",
    ),
    CanonicalRun(
        slug="krumsiek11_transfer",
        family="transfer",
        label="Krumsiek11 method transfer",
        run_dir=PROJECT_ROOT / "runs/scanpy-krumsiek11-transfer-case/20260312_012452_3a6b0c31",
    ),
    CanonicalRun(
        slug="visium_interactions",
        family="spatial",
        label="Visium H&E interaction analysis",
        dataset_id="visium_hne_crop",
        dataset_label="Visium H&E crop",
        run_dir=PROJECT_ROOT / "runs/squidpy-visium-interactions-case/20260311_182247_7653a823",
    ),
    CanonicalRun(
        slug="seqfish_transfer",
        family="spatial",
        label="seqFISH method transfer",
        run_dir=PROJECT_ROOT / "runs/squidpy-seqfish-transfer-case/20260312_012349_d8f0f895",
    ),
    CanonicalRun(
        slug="paul15_specialized",
        family="specialized_collaboration",
        label="Paul15 specialized collaboration",
        run_dir=PROJECT_ROOT / "runs/scanpy-paul15-specialized-collaboration-case/20260311_204030_3d0122ec",
    ),
    CanonicalRun(
        slug="moignard15_specialized",
        family="specialized_collaboration",
        label="Moignard15 specialized collaboration",
        run_dir=PROJECT_ROOT / "runs/scanpy-moignard15-specialized-collaboration-case/20260311_204227_7c3aa8f5",
    ),
    CanonicalRun(
        slug="krumsiek11_specialized",
        family="specialized_collaboration",
        label="Krumsiek11 specialized collaboration",
        run_dir=PROJECT_ROOT / "runs/scanpy-krumsiek11-specialized-collaboration-case/20260312_012954_5d7e11e7",
    ),
    CanonicalRun(
        slug="visium_monolith",
        family="multi_agent_baseline",
        label="Visium monolith baseline",
        dataset_id="visium_hne_crop",
        dataset_label="Visium H&E crop",
        run_dir=PROJECT_ROOT / "runs/visium-multi-agent-monolith-case/20260311_191730_24a9b3be",
    ),
    CanonicalRun(
        slug="visium_collaboration",
        family="multi_agent_collaboration",
        label="Visium collaboration",
        dataset_id="visium_hne_crop",
        dataset_label="Visium H&E crop",
        run_dir=PROJECT_ROOT / "runs/visium-multi-agent-collaboration-case/20260311_192054_9c9f3083",
    ),
    CanonicalRun(
        slug="seqfish_monolith",
        family="multi_agent_baseline",
        label="seqFISH monolith baseline",
        run_dir=PROJECT_ROOT / "runs/seqfish-multi-agent-monolith-case/20260312_012551_0391f5fc",
    ),
    CanonicalRun(
        slug="seqfish_collaboration",
        family="multi_agent_collaboration",
        label="seqFISH collaboration",
        run_dir=PROJECT_ROOT / "runs/seqfish-multi-agent-collaboration-case/20260312_012954_cff7430c",
    ),
]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def shorten(text: str, limit: int = 220) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def first_trace_task(report: dict[str, Any]) -> dict[str, Any]:
    for trace in report.get("traces", []):
        task = trace.get("inputs", {}).get("task")
        if isinstance(task, dict):
            return task
    return {}


def read_methods_excerpt(task: dict[str, Any]) -> str:
    hints = task.get("hints", {})
    methods_path = hints.get("methods_excerpt_path")
    if methods_path and Path(methods_path).exists():
        lines = [line.strip() for line in Path(methods_path).read_text().splitlines() if line.strip()]
        return " ".join(lines[:4])
    description = task.get("description", "")
    if "Methods excerpt:" in description:
        tail = description.split("Methods excerpt:", 1)[1]
        lines = [line.strip() for line in tail.splitlines() if line.strip()]
        return " ".join(lines[:4])
    return ""


def collect_paths(obj: Any) -> dict[str, str]:
    found: dict[str, str] = {}

    def walk(value: Any, prefix: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                next_prefix = f"{prefix}.{key}" if prefix else key
                walk(child, next_prefix)
        elif isinstance(value, list):
            for idx, child in enumerate(value):
                next_prefix = f"{prefix}[{idx}]"
                walk(child, next_prefix)
        elif isinstance(value, str) and "/" in value:
            found[prefix] = value

    walk(obj, "")
    return found


def maybe_get_nested_summary(outputs: dict[str, Any]) -> dict[str, Any]:
    if isinstance(outputs.get("summary"), dict):
        return outputs["summary"]
    result = outputs.get("result")
    if isinstance(result, dict) and isinstance(result.get("summary"), dict):
        return result["summary"]
    return {}


def important_summary_fields(summary: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "dataset_id",
        "dataset_label",
        "selected_repo",
        "cluster_key",
        "cluster_count",
        "marker_gene_count",
        "dataset_group_key",
        "dataset_group_count",
        "pseudotime_range",
        "paga_edge_count",
        "dynamic_gene_panel_genes",
        "has_spatial_image",
        "nhood_enrichment_summary",
    ]
    return {key: summary[key] for key in keys if key in summary}


def expected_outputs(summary: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    expected: dict[str, Any] = {}
    paper_spec = summary.get("paper_spec_output", {})
    if paper_spec:
        if paper_spec.get("required_artifacts"):
            expected["required_artifacts"] = paper_spec["required_artifacts"]
        if paper_spec.get("required_summary_fields"):
            expected["required_summary_fields"] = paper_spec["required_summary_fields"]
    hints = task.get("hints", {})
    if hints.get("target_figure_description"):
        expected["target_figure_description"] = hints["target_figure_description"]
    elif "Target figure description:" in task.get("description", ""):
        tail = task["description"].split("Target figure description:", 1)[1]
        lines = [line.strip() for line in tail.splitlines() if line.strip()]
        if lines:
            expected["target_figure_description"] = " ".join(lines[:3])
    reference_paths = {
        key: value
        for key, value in hints.items()
        if key.startswith("reference_") and isinstance(value, str)
    }
    if reference_paths:
        expected["reference_artifacts"] = reference_paths
    if "target_figure_description" not in expected:
        expected["target_figure_description"] = shorten(task.get("description", ""), 320)
    return expected


def input_artifacts(task: dict[str, Any], summary: dict[str, Any], run_dir: Path) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for idx, item in enumerate(task.get("inputs", [])):
        if isinstance(item, dict):
            for key, value in collect_paths(item).items():
                artifacts[f"task.inputs[{idx}].{key}"] = value
    hints = task.get("hints", {})
    for key, value in hints.items():
        if isinstance(value, str) and ("raw" in key or key.endswith("_path")) and Path(value).exists():
            if str(run_dir) not in value:
                artifacts[f"hints.{key}"] = value
    planner = summary.get("planner_output", {})
    for key, value in collect_paths(planner).items():
        if "raw_data_path" in key and str(run_dir) in value:
            artifacts[f"planner_output.{key}"] = value
    tool_blocks = [
        summary.get("tool_output"),
        summary.get("scanpy_cluster_tool_output"),
        summary.get("squidpy_interaction_tool_output"),
        summary.get("visium_monolith_tool_output"),
    ]
    for idx, block in enumerate(tool_blocks):
        if not block:
            continue
        for key, value in collect_paths(block).items():
            if "raw_data_path" in key or "input_h5ad_path" in key:
                artifacts[f"tool_block[{idx}].{key}"] = value
    return artifacts


def actual_outputs(summary: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    actual: dict[str, Any] = {
        "workflow_signature": summary.get("workflow_signature"),
        "cost": summary.get("cost"),
        "hard_checks": summary.get("hard_checks"),
    }
    path_fields: dict[str, str] = {}
    for key in [
        "tool_output",
        "scanpy_cluster_tool_output",
        "squidpy_interaction_tool_output",
        "visium_monolith_tool_output",
    ]:
        if summary.get(key):
            for path_key, value in collect_paths(summary[key]).items():
                if str(run_dir) in value:
                    path_fields[f"{key}.{path_key}"] = value
    if path_fields:
        actual["artifact_paths"] = path_fields
    for key in [
        "planner_output",
        "paper_spec_output",
        "bio_reviewer_output",
        "refiner_output",
    ]:
        if summary.get(key):
            actual[key] = summary[key]
    return actual


def trace_digest(report: dict[str, Any], run_dir: Path) -> list[dict[str, Any]]:
    digest: list[dict[str, Any]] = []
    for trace in report.get("traces", []):
        outputs = trace.get("outputs", {})
        nested_summary = maybe_get_nested_summary(outputs)
        entry = {
            "node_id": trace.get("node_id"),
            "role": trace.get("role"),
            "status": trace.get("status"),
            "input_keys": sorted(trace.get("inputs", {}).keys()),
        }
        if nested_summary:
            entry["summary_fields"] = important_summary_fields(nested_summary)
            entry["artifact_paths"] = {
                key: value
                for key, value in collect_paths(nested_summary).items()
                if str(run_dir) in value
            }
        if isinstance(outputs.get("summary"), str):
            entry["summary_text"] = outputs["summary"]
        result = outputs.get("result")
        if isinstance(result, dict):
            if result.get("selected_repo"):
                entry["selected_repo"] = result["selected_repo"]
            if isinstance(result.get("summary"), str):
                entry["result_summary_text"] = result["summary"]
        if outputs.get("selected_repo"):
            entry["selected_repo"] = outputs["selected_repo"]
        if trace.get("node_id") == "bio_reviewer":
            reviewer = outputs if outputs else result if isinstance(result, dict) else {}
            entry["review"] = {
                key: reviewer.get(key)
                for key in [
                    "methodology_faithfulness",
                    "biological_plausibility",
                    "artifact_completeness",
                    "overall_verdict",
                    "should_refine",
                    "summary",
                    "concerns",
                ]
                if key in reviewer
            }
        digest.append(entry)
    return digest


def collaboration_handoff(report: dict[str, Any]) -> dict[str, Any]:
    traces = {trace["node_id"]: trace for trace in report.get("traces", [])}
    handoff: dict[str, Any] = {}

    if {"paper_spec", "planner", "scanpy_trajectory_tool", "bio_reviewer"} <= traces.keys():
        paper_spec_outputs = traces["paper_spec"].get("outputs", {})
        planner_outputs = traces["planner"].get("outputs", {})
        tool_result = traces["scanpy_trajectory_tool"].get("outputs", {}).get("result", {})
        handoff["specialized_trajectory"] = {
            "paper_spec_required_artifacts": paper_spec_outputs.get("required_artifacts"),
            "planner_selected_repo": planner_outputs.get("selected_repo"),
            "planner_plan_focus": planner_outputs.get("plan_focus"),
            "trajectory_selected_repo": tool_result.get("selected_repo"),
            "reviewer_input_keys": sorted(traces["bio_reviewer"].get("inputs", {}).keys()),
        }

    if {"scanpy_cluster_tool", "squidpy_interaction_tool", "bio_reviewer"} <= traces.keys():
        cluster_result = traces["scanpy_cluster_tool"].get("outputs", {}).get("result", {})
        cluster_summary = cluster_result.get("summary", {})
        interaction_inputs = traces["squidpy_interaction_tool"].get("inputs", {})
        interaction_result = traces["squidpy_interaction_tool"].get("outputs", {}).get("result", {})
        interaction_summary = interaction_result.get("summary", {})
        handoff["spatial_multi_agent"] = {
            "cluster_selected_repo": cluster_result.get("selected_repo"),
            "cluster_summary_path": cluster_summary.get("summary_path"),
            "cluster_key": cluster_summary.get("cluster_key"),
            "cluster_count": cluster_summary.get("cluster_count"),
            "marker_gene_count": cluster_summary.get("marker_gene_count"),
            "handoff_input_h5ad_path": interaction_inputs.get("input_h5ad_path"),
            "handoff_cluster_key": interaction_inputs.get("cluster_key"),
            "interaction_selected_repo": interaction_result.get("selected_repo"),
            "interaction_summary_path": interaction_summary.get("summary_path"),
            "interaction_figure_path": interaction_summary.get("interaction_figure_path"),
            "reviewer_input_keys": sorted(traces["bio_reviewer"].get("inputs", {}).keys()),
        }

    return handoff


def reviewer_concerns(case: dict[str, Any]) -> list[str]:
    actual = case.get("actual", {})
    reviewer_output = actual.get("bio_reviewer_output", {})
    if isinstance(reviewer_output, dict):
        concerns = reviewer_output.get("concerns")
        if isinstance(concerns, list):
            return [str(item) for item in concerns if str(item).strip()]
    for trace in case.get("trace_digest", []):
        review = trace.get("review", {})
        if isinstance(review, dict):
            concerns = review.get("concerns")
            if isinstance(concerns, list):
                return [str(item) for item in concerns if str(item).strip()]
    return []


def log_interpretation(case: dict[str, Any]) -> list[str]:
    family = case.get("family")
    actual = case.get("actual", {})
    handoff = case.get("collaboration_handoff", {})
    trace = case.get("trace_digest", [])
    planner = actual.get("planner_output", {}) if isinstance(actual.get("planner_output"), dict) else {}
    bullets: list[str] = []

    if "specialized_trajectory" in handoff:
        payload = handoff["specialized_trajectory"]
        bullets.append(
            "The trace shows a real contract-first collaboration: paper_spec declares the artifact package before execution starts."
        )
        if payload.get("planner_selected_repo"):
            bullets.append(
                f"The planner then locks onto {payload['planner_selected_repo']} instead of searching a new tool family, which makes this a reuse case rather than a from-scratch workflow."
            )
        reviewer_inputs = payload.get("reviewer_input_keys") or []
        if reviewer_inputs:
            bullets.append(
                f"The reviewer is grounded by concrete inputs {reviewer_inputs}, so the final verdict is based on both the paper contract and the generated outputs."
            )
        return bullets

    if "spatial_multi_agent" in handoff:
        payload = handoff["spatial_multi_agent"]
        bullets.append(
            "The collaboration trace is observable at the data-contract level: clustering and spatial interaction are executed by different specialists."
        )
        if payload.get("cluster_key") and payload.get("handoff_input_h5ad_path"):
            bullets.append(
                f"Scanpy writes an annotated handoff file and exposes cluster_key={payload['cluster_key']}; Squidpy consumes both to build the spatial-neighbor analysis."
            )
        reviewer_inputs = payload.get("reviewer_input_keys") or []
        if reviewer_inputs:
            bullets.append(
                f"The reviewer receives {reviewer_inputs}, which means the final judgment covers both the clustering stage and the interaction stage."
            )
        return bullets

    if family in {"transfer", "spatial"}:
        selected_repo = planner.get("selected_repo")
        if selected_repo:
            bullets.append(
                f"The planner stayed on {selected_repo}; the main change for this case came from dataset-aware execution rather than repo switching."
            )
        bullets.append(
            "The key signal in the logs is whether dataset-specific assumptions were preserved or repaired, not whether more nodes were added."
        )
        return bullets

    if family in {"smoke", "trajectory"}:
        selected_repo = planner.get("selected_repo")
        if selected_repo:
            bullets.append(
                f"The planner selected {selected_repo} and the single tool stage produced the expected artifact package without refinement."
            )
        bullets.append(
            "These cases matter because they establish that the sandboxed specialist path can reproduce standard public examples before harder transfer or collaboration tasks are attempted."
        )
        return bullets

    if family == "multi_agent_baseline":
        bullets.append(
            "This monolith baseline is intentionally useful for comparison because it owns clustering and interaction analysis in one sandbox, so there is no handoff trace to inspect."
        )
        return bullets

    if trace:
        bullets.append("The trace records node order, selected repos, and reviewer signals directly from execution_report.json.")
    return bullets


def render_handoff_markdown(case: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    handoff = case.get("collaboration_handoff", {})
    if not handoff:
        return lines

    if "specialized_trajectory" in handoff:
        payload = handoff["specialized_trajectory"]
        lines.append("- Trajectory collaboration contract:")
        if payload.get("paper_spec_required_artifacts"):
            lines.append(
                f"  - paper_spec required artifacts: `{', '.join(payload['paper_spec_required_artifacts'])}`"
            )
        if payload.get("planner_selected_repo"):
            lines.append(f"  - planner selected repo: `{payload['planner_selected_repo']}`")
        if payload.get("planner_plan_focus"):
            lines.append(f"  - planner focus: {payload['planner_plan_focus']}")
        if payload.get("trajectory_selected_repo"):
            lines.append(f"  - execution specialist repo: `{payload['trajectory_selected_repo']}`")
        if payload.get("reviewer_input_keys"):
            lines.append(f"  - reviewer saw inputs: `{payload['reviewer_input_keys']}`")

    if "spatial_multi_agent" in handoff:
        payload = handoff["spatial_multi_agent"]
        lines.append("- Spatial collaboration handoff:")
        if payload.get("cluster_selected_repo"):
            lines.append(f"  - clustering specialist repo: `{payload['cluster_selected_repo']}`")
        if payload.get("cluster_key"):
            lines.append(f"  - emitted cluster_key: `{payload['cluster_key']}`")
        if payload.get("cluster_count") is not None:
            lines.append(f"  - emitted cluster_count: `{payload['cluster_count']}`")
        if payload.get("marker_gene_count") is not None:
            lines.append(f"  - marker_gene_count: `{payload['marker_gene_count']}`")
        if payload.get("handoff_input_h5ad_path"):
            lines.append(f"  - handoff annotated h5ad: `{payload['handoff_input_h5ad_path']}`")
        if payload.get("handoff_cluster_key"):
            lines.append(f"  - Squidpy consumed cluster_key: `{payload['handoff_cluster_key']}`")
        if payload.get("interaction_selected_repo"):
            lines.append(f"  - interaction specialist repo: `{payload['interaction_selected_repo']}`")
        if payload.get("reviewer_input_keys"):
            lines.append(f"  - reviewer saw inputs: `{payload['reviewer_input_keys']}`")
    return lines


def extract_case(run: CanonicalRun) -> dict[str, Any]:
    summary = load_json(run.run_dir / "summaries" / "summary.json")
    report = load_json(run.run_dir / "eval" / "execution_report.json")
    analysis_path = run.run_dir / "summaries" / "analysis.md"
    task = first_trace_task(report)
    hints = task.get("hints", {})
    case = {
        "slug": run.slug,
        "family": run.family,
        "label": run.label,
        "run_dir": str(run.run_dir),
        "case_id": summary.get("case_id"),
        "task_title": task.get("title"),
        "task_description": task.get("description"),
        "dataset_id": hints.get("dataset_id") or run.dataset_id,
        "dataset_label": hints.get("dataset_label") or run.dataset_label,
        "methods_excerpt": read_methods_excerpt(task),
        "input_artifacts": input_artifacts(task, summary, run.run_dir),
        "expected": expected_outputs(summary, task),
        "actual": actual_outputs(summary, run.run_dir),
        "trace_digest": trace_digest(report, run.run_dir),
        "collaboration_handoff": collaboration_handoff(report),
        "analysis_excerpt": shorten(analysis_path.read_text()) if analysis_path.exists() else "",
    }
    return case


def render_case_markdown(case: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"## {case['label']}")
    lines.append("")
    lines.append(f"- `case_id`: `{case['case_id']}`")
    lines.append(f"- Canonical run: `{case['run_dir']}`")
    lines.append(f"- Task title: {case['task_title']}")
    lines.append(f"- Dataset: `{case.get('dataset_id')}` / {case.get('dataset_label')}")
    lines.append("")
    lines.append("### Task Setup")
    lines.append("")
    lines.append(f"- Task description: {shorten(case['task_description'] or '', 500)}")
    if case.get("methods_excerpt"):
        lines.append(f"- Methods excerpt: {shorten(case['methods_excerpt'], 400)}")
    if case.get("input_artifacts"):
        lines.append("- Input artifacts:")
        for key, value in case["input_artifacts"].items():
            lines.append(f"  - `{key}`: `{value}`")
    expected = case["expected"]
    if expected.get("target_figure_description"):
        lines.append(f"- Expected result: {shorten(expected['target_figure_description'], 500)}")
    if expected.get("required_artifacts"):
        lines.append(f"- Expected artifacts: `{', '.join(expected['required_artifacts'])}`")
    if expected.get("required_summary_fields"):
        lines.append(f"- Expected summary fields: `{', '.join(expected['required_summary_fields'])}`")
    if expected.get("reference_artifacts"):
        lines.append("- Reference artifacts:")
        for key, value in expected["reference_artifacts"].items():
            lines.append(f"  - `{key}`: `{value}`")
    lines.append("")
    lines.append("### Actual Output")
    lines.append("")
    actual = case["actual"]
    lines.append(f"- Workflow: `{actual.get('workflow_signature')}`")
    lines.append(f"- Cost: `{json.dumps(actual.get('cost', {}), ensure_ascii=False)}`")
    hard_checks = actual.get("hard_checks", [])
    if hard_checks:
        lines.append(f"- Hard checks: `{json.dumps(hard_checks, ensure_ascii=False)}`")
    artifact_paths = actual.get("artifact_paths", {})
    if artifact_paths:
        lines.append("- Generated artifacts:")
        for key, value in artifact_paths.items():
            lines.append(f"  - `{key}`: `{value}`")
    if actual.get("bio_reviewer_output"):
        lines.append(f"- Reviewer output: `{json.dumps(actual['bio_reviewer_output'], ensure_ascii=False)}`")
    if actual.get("planner_output"):
        lines.append(f"- Planner output: `{json.dumps(actual['planner_output'], ensure_ascii=False)}`")
    if actual.get("paper_spec_output"):
        lines.append(f"- Paper-spec output: `{json.dumps(actual['paper_spec_output'], ensure_ascii=False)}`")
    lines.append("")
    lines.append("### Execution Trace")
    lines.append("")
    for trace in case["trace_digest"]:
        lines.append(
            f"- `{trace['node_id']}` ({trace['role']}): inputs={trace['input_keys']}, status=`{trace['status']}`"
        )
        if trace.get("selected_repo"):
            lines.append(f"  - selected repo: `{trace['selected_repo']}`")
        if trace.get("summary_text"):
            lines.append(f"  - summary: {shorten(trace['summary_text'], 300)}")
        if trace.get("result_summary_text"):
            lines.append(f"  - result summary: {shorten(trace['result_summary_text'], 300)}")
        if trace.get("summary_fields"):
            lines.append(f"  - summary fields: `{json.dumps(trace['summary_fields'], ensure_ascii=False)}`")
        if trace.get("artifact_paths"):
            for key, value in trace["artifact_paths"].items():
                lines.append(f"  - artifact `{key}`: `{value}`")
        if trace.get("review"):
            lines.append(f"  - reviewer: `{json.dumps(trace['review'], ensure_ascii=False)}`")
    concerns = reviewer_concerns(case)
    if concerns:
        lines.append("")
        lines.append("### Reviewer Caveats")
        lines.append("")
        for concern in concerns:
            lines.append(f"- {concern}")
    if case["collaboration_handoff"]:
        lines.append("")
        lines.append("### Collaboration / Handoff Evidence")
        lines.append("")
        lines.extend(render_handoff_markdown(case))
    interpretations = log_interpretation(case)
    if interpretations:
        lines.append("")
        lines.append("### Log Interpretation")
        lines.append("")
        for bullet in interpretations:
            lines.append(f"- {bullet}")
    lines.append("")
    lines.append("### Existing Analysis Excerpt")
    lines.append("")
    lines.append(f"- {case['analysis_excerpt']}")
    lines.append("")
    return "\n".join(lines)


def render_markdown(cases: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append("# Experiment 3 Detailed Case-Study Report")
    lines.append("")
    lines.append("This report is generated from canonical Experiment 3 run artifacts.")
    lines.append("Each case is documented using the same evidence sources:")
    lines.append("- `summaries/summary.json`")
    lines.append("- `summaries/analysis.md`")
    lines.append("- `eval/execution_report.json`")
    lines.append("- artifact paths stored under the canonical run directory")
    lines.append("")
    lines.append("## Case Inventory")
    lines.append("")
    lines.append("| Case | Family | Dataset | Workflow | Canonical run |")
    lines.append("|---|---|---|---|---|")
    for case in cases:
        workflow = case["actual"].get("workflow_signature", "")
        dataset = case.get("dataset_label") or case.get("dataset_id") or "-"
        lines.append(
            f"| {case['label']} | `{case['family']}` | `{dataset}` | `{workflow}` | `{case['run_dir']}` |"
        )
    lines.append("")
    lines.append("## High-Level Reporting Rules")
    lines.append("")
    lines.append("- `Task setup` explains the biological task, dataset, and expected artifact package.")
    lines.append("- `Actual output` captures what the canonical run produced and what the hard check validated.")
    lines.append("- `Execution trace` surfaces planner decisions, selected repos, tool stages, and reviewer outputs.")
    lines.append("- `Collaboration / Handoff evidence` is included when a case uses multiple specialized agents.")
    lines.append("")
    for family in [
        "smoke",
        "trajectory",
        "transfer",
        "spatial",
        "specialized_collaboration",
        "multi_agent_baseline",
        "multi_agent_collaboration",
    ]:
        family_cases = [case for case in cases if case["family"] == family]
        if not family_cases:
            continue
        lines.append(f"# {family.replace('_', ' ').title()}")
        lines.append("")
        for case in family_cases:
            lines.append(render_case_markdown(case))
    return "\n".join(lines)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cases = [extract_case(run) for run in CANONICAL_RUNS]
    markdown = render_markdown(cases)
    json_payload = {"cases": cases}
    (OUTPUT_DIR / "experiment3_case_study_report.md").write_text(markdown)
    (OUTPUT_DIR / "experiment3_case_study_report.json").write_text(
        json.dumps(json_payload, indent=2, ensure_ascii=False)
    )
    print(OUTPUT_DIR / "experiment3_case_study_report.md")
    print(OUTPUT_DIR / "experiment3_case_study_report.json")


if __name__ == "__main__":
    main()
