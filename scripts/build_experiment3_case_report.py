from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path("/home/shuaikes/projects/agent/Agent-Cop")
RUNS = ROOT / "runs"
OUTPUT_DIR = ROOT / "output" / "reports"
REPORT_MD = OUTPUT_DIR / "experiment3_case_study_report.md"
REPORT_JSON = OUTPUT_DIR / "experiment3_case_study_report.json"


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    section: str
    title: str
    run_dir: Path
    input_description: str
    dataset_description: str
    expected_outputs: list[str]
    evaluation_target: str
    notes: list[str]
    comparison_run_dir: Path | None = None


CANONICAL_CASES: list[CaseSpec] = [
    CaseSpec(
        case_id="scanpy_pbmc3k_case",
        section="Smoke Baselines",
        title="PBMC3k UMAP reproduction",
        run_dir=RUNS / "case-study-archive/scanpy-pbmc3k-case/20260310_064605_c8a5febc",
        input_description="Public PBMC3k single-cell expression matrix; no task-specific manual preprocessing outside the workflow.",
        dataset_description="PBMC3k: small public scRNA-seq benchmark used as the minimal sandboxed Scanpy reproduction task.",
        expected_outputs=[
            "UMAP figure PNG",
            "summary JSON with cluster and figure metadata",
            "raw/annotated data artifact",
        ],
        evaluation_target="Reproduce the reference UMAP package closely enough to pass the hard check and reviewer sanity check.",
        notes=[
            "This is the simplest end-to-end smoke test for the Scanpy tool path.",
            "It matters because later cases reuse the same sandbox and review machinery under higher task complexity.",
        ],
    ),
    CaseSpec(
        case_id="squidpy_visium_case",
        section="Smoke Baselines",
        title="Visium H&E spatial plot reproduction",
        run_dir=RUNS / "squidpy-visium-case/20260310_064504_ed083350",
        input_description="Public Visium H&E spatial dataset with image-backed spatial coordinates.",
        dataset_description="Visium H&E crop: public spatial transcriptomics data with tissue image metadata.",
        expected_outputs=[
            "spatial plot PNG",
            "summary JSON with spatial image metadata",
            "raw/annotated data artifact",
        ],
        evaluation_target="Reproduce the reference spatial plot while preserving image-backed spatial metadata and passing the hard check.",
        notes=[
            "This is the simplest end-to-end smoke test for the Squidpy tool path.",
            "Later interaction and collaboration cases build on the same spatial-analysis family.",
        ],
    ),
    CaseSpec(
        case_id="scanpy_paul15_case",
        section="Advanced Single-Tool Analyses",
        title="Paul15 trajectory reconstruction",
        run_dir=RUNS / "scanpy-paul15-case/20260311_052516_f66fe04f",
        input_description="Public Paul15 hematopoiesis dataset routed through the Scanpy trajectory workflow.",
        dataset_description="Paul15: public hematopoiesis trajectory dataset used as the source task for later paper-figure and transfer experiments.",
        expected_outputs=[
            "trajectory embedding figure",
            "PAGA lineage graph",
            "summary JSON with cluster and pseudotime metrics",
        ],
        evaluation_target="Recover a valid trajectory package with stable pseudotime, PAGA structure, and reviewer-approved biological plausibility.",
        notes=[
            "The fixed-planner variant is the canonical run because it matched the adaptive path while using less API budget.",
        ],
    ),
    CaseSpec(
        case_id="squidpy_visium_interactions_case",
        section="Advanced Single-Tool Analyses",
        title="Visium spatial-interaction analysis",
        run_dir=RUNS / "squidpy-visium-interactions-case/20260311_182247_7653a823",
        input_description="Public Visium H&E dataset analyzed with Squidpy spatial-neighbor and neighborhood-enrichment statistics.",
        dataset_description="Visium H&E crop with image metadata, neighborhood graph, and enrichment heatmap output.",
        expected_outputs=[
            "spatial plot PNG",
            "neighborhood-enrichment heatmap PNG",
            "interaction summary JSON with top enriched/depleted pairs",
        ],
        evaluation_target="Produce both the spatial rendering and the interaction statistics package while matching the reference summary and passing the hard check.",
        notes=[
            "This case is the stronger single-tool spatial baseline; it requires multiple dependent outputs, not just one figure.",
        ],
    ),
    CaseSpec(
        case_id="scanpy_paul15_paper_figure_case",
        section="Paper-Style Figure Reproduction",
        title="Paul15 paper-figure reproduction",
        run_dir=RUNS / "scanpy-paul15-paper-figure-case/20260311_205017_9f04276f",
        input_description="Paul15 dataset plus a paper-grounded task specification requiring a figure package rather than a single plot.",
        dataset_description="Paul15 again, but with a stricter paper-figure objective and explicit provenance expectations.",
        expected_outputs=[
            "trajectory figure",
            "PAGA figure",
            "dynamic-gene panel figure",
            "summary JSON with root provenance and dynamic-gene panel fields",
        ],
        evaluation_target="Match the paper-style artifact package and preserve the fields required by the paper-figure hard check.",
        notes=[
            "This case shows the jump from single-tool execution to structured artifact packages.",
            "The key method result was that fixed planning still beat richer planning on cost.",
        ],
    ),
    CaseSpec(
        case_id="scanpy_moignard15_transfer_case",
        section="Method Transfer",
        title="Moignard15 method transfer",
        run_dir=RUNS / "scanpy-moignard15-transfer-case/20260311_205017_a59f56c3",
        input_description="Transfer the Paul15-style trajectory workflow onto the public Moignard15 qRT-PCR dataset.",
        dataset_description="Moignard15: non-count, preprocessed developmental trajectory data with `exp_groups` metadata.",
        expected_outputs=[
            "trajectory figure",
            "PAGA figure",
            "dynamic-gene panel",
            "summary JSON preserving dataset-specific stage metadata and root provenance",
        ],
        evaluation_target="Adapt the source workflow without breaking target-dataset semantics; hard check requires target metadata and valid pseudotime outputs.",
        notes=[
            "The repaired run is canonical because earlier attempts exposed non-finite pseudotime and invalid count-style preprocessing assumptions.",
        ],
    ),
    CaseSpec(
        case_id="scanpy_krumsiek11_transfer_case",
        section="Method Transfer",
        title="Krumsiek11 method transfer",
        run_dir=RUNS / "scanpy-krumsiek11-transfer-case/20260312_012452_3a6b0c31",
        comparison_run_dir=RUNS / "scanpy-krumsiek11-transfer-case/20260312_012551_1c881c2e",
        input_description="Transfer the Paul15-style trajectory workflow onto the public Krumsiek11 hematopoiesis dataset.",
        dataset_description="Krumsiek11: low-dimensional, preprocessed non-count trajectory dataset with `cell_type` labels and a dataset-default root.",
        expected_outputs=[
            "trajectory figure",
            "PAGA figure",
            "dynamic-gene panel",
            "summary JSON preserving `cell_type` and root provenance",
        ],
        evaluation_target="Pass the hard check while respecting target-dataset semantics, especially non-count preprocessing and root selection.",
        notes=[
            "The fixed run is canonical because the adaptive rerun produced the same scientific package while adding API cost.",
            "The original failures were concrete data-adaptation bugs: non-string `uns` keys and invalid count-style preprocessing.",
        ],
    ),
    CaseSpec(
        case_id="scanpy_paul15_specialized_collaboration_case",
        section="Specialized-Agent Collaboration",
        title="Paul15 specialized collaboration",
        run_dir=RUNS / "scanpy-paul15-specialized-collaboration-case/20260311_204030_3d0122ec",
        input_description="Paul15 dataset plus a paper-spec task requiring reusable specialist roles rather than one monolithic executor.",
        dataset_description="Paul15 source dataset for the trajectory-family collaboration path.",
        expected_outputs=[
            "paper-spec artifact contract",
            "trajectory figure",
            "PAGA figure",
            "dynamic-gene panel",
            "reviewer verdict over the artifact package",
        ],
        evaluation_target="Show that specialist roles can be composed end to end and still satisfy the same paper-style artifact checks.",
        notes=[
            "This is the source-dataset collaboration template later transferred to Moignard15 and Krumsiek11.",
        ],
    ),
    CaseSpec(
        case_id="scanpy_moignard15_specialized_collaboration_case",
        section="Specialized-Agent Collaboration",
        title="Moignard15 specialized collaboration",
        run_dir=RUNS / "scanpy-moignard15-specialized-collaboration-case/20260311_204227_7c3aa8f5",
        input_description="Moignard15 transfer task executed through reusable paper-spec, planner, trajectory, and reviewer specialists.",
        dataset_description="Moignard15 transfer target inside the specialized-collaboration trajectory family.",
        expected_outputs=[
            "paper-spec artifact contract",
            "trajectory figure",
            "PAGA figure",
            "dynamic-gene panel",
            "reviewer verdict over the artifact package",
        ],
        evaluation_target="Transfer the collaboration scaffold to a second dataset while preserving target-dataset semantics and passing the hard check.",
        notes=[
            "This run proves reuse across datasets, but the gain still comes from dataset adaptation rather than extra planning depth.",
        ],
    ),
    CaseSpec(
        case_id="scanpy_krumsiek11_specialized_collaboration_case",
        section="Specialized-Agent Collaboration",
        title="Krumsiek11 specialized collaboration",
        run_dir=RUNS / "scanpy-krumsiek11-specialized-collaboration-case/20260312_012954_5d7e11e7",
        input_description="Krumsiek11 transfer task executed through reusable paper-spec, planner, trajectory, and reviewer specialists.",
        dataset_description="Krumsiek11 transfer target inside the specialized-collaboration trajectory family.",
        expected_outputs=[
            "paper-spec artifact contract",
            "trajectory figure",
            "PAGA figure",
            "dynamic-gene panel",
            "reviewer verdict over the artifact package",
        ],
        evaluation_target="Demonstrate that the same specialist scaffold can be reused on a third trajectory-family dataset and still pass the hard check.",
        notes=[
            "This run is the cleanest extensibility proof because the trace shows each specialist's contract and handoff clearly.",
        ],
    ),
    CaseSpec(
        case_id="visium_multi_agent_monolith_case",
        section="Monolith vs Collaboration",
        title="Visium monolith baseline",
        run_dir=RUNS / "visium-multi-agent-monolith-case/20260311_191730_24a9b3be",
        input_description="Public Visium dataset executed through a single monolithic tool that performs both clustering and interaction analysis.",
        dataset_description="Visium H&E collaboration baseline with image-backed spatial metadata.",
        expected_outputs=[
            "cluster UMAP",
            "marker heatmap",
            "spatial plot",
            "interaction heatmap",
            "summary JSON merging clustering and interaction statistics",
        ],
        evaluation_target="Serve as the baseline for the Visium collaboration claim while still producing the full artifact package and passing the hard check.",
        notes=[
            "This baseline is intentionally monolithic so the collaboration case can show what role separation changes.",
        ],
    ),
    CaseSpec(
        case_id="visium_multi_agent_collaboration_case",
        section="Monolith vs Collaboration",
        title="Visium multi-specialist collaboration",
        run_dir=RUNS / "visium-multi-agent-collaboration-case/20260311_192054_9c9f3083",
        input_description="The same public Visium dataset, but split across a Scanpy clustering specialist and a Squidpy interaction specialist.",
        dataset_description="Visium H&E collaboration target with image-backed spatial metadata.",
        expected_outputs=[
            "cluster UMAP",
            "marker heatmap",
            "annotated h5ad handoff from Scanpy to Squidpy",
            "spatial plot",
            "interaction heatmap",
            "reviewer verdict over the combined package",
        ],
        evaluation_target="Show that reusable specialists can cooperate on the same spatial task and outperform the monolithic baseline on review quality.",
        notes=[
            "This is the first spatial-family collaboration proof in the retained runs.",
        ],
    ),
    CaseSpec(
        case_id="squidpy_seqfish_transfer_case",
        section="Spatial Method Transfer",
        title="seqFISH spatial-interaction transfer",
        run_dir=RUNS / "squidpy-seqfish-transfer-case/20260312_012349_d8f0f895",
        input_description="Transfer the Visium-style Squidpy interaction workflow to a public seqFISH dataset with coordinates but no tissue image metadata.",
        dataset_description="seqFISH: public spatial transcriptomics dataset without `adata.uns[\"spatial\"]` image metadata.",
        expected_outputs=[
            "coordinate-only spatial plot",
            "neighborhood-enrichment heatmap",
            "interaction summary JSON with enriched/depleted pairs",
        ],
        evaluation_target="Transfer the spatial-interaction workflow successfully despite the missing image-backed plotting path and pass the hard check.",
        notes=[
            "The crucial repair was a coordinate-only plotting fallback; the fix came from logs, not from extra planning.",
        ],
    ),
    CaseSpec(
        case_id="seqfish_multi_agent_monolith_case",
        section="Monolith vs Collaboration",
        title="seqFISH monolith baseline",
        run_dir=RUNS / "seqfish-multi-agent-monolith-case/20260312_012551_0391f5fc",
        input_description="Public seqFISH dataset executed through a monolithic clustering-plus-interaction tool chain.",
        dataset_description="seqFISH collaboration baseline on a non-Visium spatial dataset without tissue-image metadata.",
        expected_outputs=[
            "cluster UMAP",
            "marker heatmap",
            "coordinate-only spatial plot",
            "interaction heatmap",
            "summary JSON merging clustering and interaction statistics",
        ],
        evaluation_target="Serve as the monolithic baseline for the seqFISH collaboration comparison while passing the hard check.",
        notes=[
            "The monolith already works after the coordinate-only plotting fix, so the collaboration comparison is meaningful.",
        ],
    ),
    CaseSpec(
        case_id="seqfish_multi_agent_collaboration_case",
        section="Monolith vs Collaboration",
        title="seqFISH multi-specialist collaboration",
        run_dir=RUNS / "seqfish-multi-agent-collaboration-case/20260312_012954_cff7430c",
        input_description="The same public seqFISH dataset, split across a Scanpy clustering specialist and a Squidpy interaction specialist.",
        dataset_description="seqFISH collaboration target on a non-image-backed spatial dataset.",
        expected_outputs=[
            "cluster UMAP",
            "marker heatmap",
            "annotated h5ad handoff from Scanpy to Squidpy",
            "coordinate-only spatial plot",
            "interaction heatmap",
            "reviewer verdict over the combined package",
        ],
        evaluation_target="Show that role-separated specialists can collaborate on a non-Visium spatial dataset and outperform the monolith baseline on review quality.",
        notes=[
            "This is the strongest current spatial-family extensibility case because it combines transfer, multi-step analysis, and collaboration.",
        ],
    ),
]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def basename_or_value(value: Any) -> Any:
    if isinstance(value, str) and "/" in value:
        return Path(value).name
    return value


def summarize_task(task: dict[str, Any]) -> dict[str, Any]:
    hints = task.get("hints", {})
    return {
        "task_id": task.get("task_id", ""),
        "title": task.get("title", ""),
        "description": task.get("description", ""),
        "dataset_id": hints.get("dataset_id", ""),
        "dataset_label": hints.get("dataset_label", ""),
        "target_figure_description": hints.get("target_figure_description", ""),
        "candidate_repos": [repo.get("name", "") for repo in hints.get("candidate_repos", [])],
    }


def get_first_task(execution_report: dict[str, Any]) -> dict[str, Any]:
    for trace in execution_report.get("traces", []):
        task = trace.get("inputs", {}).get("task")
        if isinstance(task, dict):
            return task
    return {}


def extract_generated_artifacts(summary: dict[str, Any]) -> list[str]:
    artifacts: list[str] = []
    for key, value in sorted(summary.items()):
        if key.startswith("generated_") and isinstance(value, str):
            artifacts.append(f"{key}: {Path(value).name}")
    return artifacts


def extract_review(summary: dict[str, Any]) -> dict[str, Any]:
    review = summary.get("review_output", {})
    if not isinstance(review, dict):
        return {}
    compact = {}
    for key in [
        "methodology_faithfulness",
        "biological_plausibility",
        "artifact_completeness",
        "overall_verdict",
        "should_refine",
        "summary",
    ]:
        if key in review:
            compact[key] = review[key]
    concerns = review.get("concerns")
    if concerns:
        compact["concerns"] = concerns
    return compact


def extract_hard_checks(summary: dict[str, Any]) -> list[str]:
    checks = []
    for check in summary.get("hard_checks", []):
        check_id = check.get("check_id", "")
        passed = check.get("passed", False)
        checks.append(f"{check_id}: {'pass' if passed else 'fail'}")
    return checks


def extract_metrics(summary: dict[str, Any]) -> list[str]:
    metrics: list[str] = []
    if "tool_output" in summary and isinstance(summary["tool_output"], dict):
        tool_summary = summary["tool_output"].get("summary", {})
        if isinstance(tool_summary, dict):
            for key in [
                "cluster_count",
                "pseudotime_range",
                "paga_edge_count",
                "dataset_group_key",
                "dataset_group_count",
                "dynamic_gene_panel_genes",
                "root_strategy",
                "root_group",
                "has_spatial_image",
                "nhood_zscore_max",
                "nhood_zscore_min",
            ]:
                if key in tool_summary:
                    metrics.append(f"{key}: {basename_or_value(tool_summary[key])}")
    if "cluster_tool_output" in summary and isinstance(summary["cluster_tool_output"], dict):
        cluster_summary = summary["cluster_tool_output"].get("summary", {})
        if isinstance(cluster_summary, dict):
            for key in ["cluster_count", "marker_gene_count", "cluster_key", "n_obs", "n_vars"]:
                if key in cluster_summary:
                    metrics.append(f"cluster.{key}: {cluster_summary[key]}")
    if "interaction_tool_output" in summary and isinstance(summary["interaction_tool_output"], dict):
        interaction_summary = summary["interaction_tool_output"].get("summary", {})
        if isinstance(interaction_summary, dict):
            for key in [
                "cluster_key",
                "has_spatial_image",
                "nhood_zscore_max",
                "nhood_zscore_min",
                "nhood_zscore_abs_mean",
            ]:
                if key in interaction_summary:
                    metrics.append(f"interaction.{key}: {interaction_summary[key]}")
            top_pairs = interaction_summary.get("top_enriched_pairs", [])
            if top_pairs:
                top = top_pairs[0]
                metrics.append(
                    "interaction.top_enriched_pair: "
                    f"{top.get('source')}->{top.get('target')} z={top.get('zscore')} count={top.get('count')}"
                )
    return metrics


def highlight_for_trace(node: dict[str, Any]) -> list[str]:
    node_id = node.get("node_id", "")
    outputs = node.get("outputs", {})
    highlights: list[str] = []
    if node_id == "paper_spec":
        required = outputs.get("required_artifacts", [])
        if required:
            highlights.append("required_artifacts=" + ", ".join(required))
    elif node_id == "planner":
        if "selected_repo" in outputs:
            highlights.append(f"selected_repo={outputs['selected_repo']}")
        if "scanpy_repo" in outputs or "squidpy_repo" in outputs:
            scanpy_repo = outputs.get("scanpy_repo", "")
            squidpy_repo = outputs.get("squidpy_repo", "")
            highlights.append(f"scanpy_repo={scanpy_repo} squidpy_repo={squidpy_repo}".strip())
    elif node_id in {"scanpy_trajectory_tool", "scanpy_tool", "squidpy_tool", "visium_monolith_tool"}:
        result = outputs.get("result", {})
        summary = result.get("summary", {}) if isinstance(result, dict) else {}
        if isinstance(summary, dict):
            for key in ["selected_repo", "cluster_count", "pseudotime_range", "paga_edge_count", "cluster_key"]:
                if key in summary:
                    highlights.append(f"{key}={basename_or_value(summary[key])}")
    elif node_id == "scanpy_cluster_tool":
        result = outputs.get("result", {})
        summary = result.get("summary", {}) if isinstance(result, dict) else {}
        if isinstance(summary, dict):
            for key in ["selected_repo", "cluster_key", "cluster_count", "marker_gene_count", "annotated_data_path"]:
                if key in summary:
                    highlights.append(f"{key}={basename_or_value(summary[key])}")
    elif node_id == "squidpy_interaction_tool":
        result = outputs.get("result", {})
        summary = result.get("summary", {}) if isinstance(result, dict) else {}
        if isinstance(summary, dict):
            for key in ["selected_repo", "cluster_key", "has_spatial_image"]:
                if key in summary:
                    highlights.append(f"{key}={basename_or_value(summary[key])}")
            pairs = summary.get("top_enriched_pairs", [])
            if pairs:
                top = pairs[0]
                highlights.append(
                    "top_enriched_pair="
                    f"{top.get('source')}->{top.get('target')} z={top.get('zscore')} count={top.get('count')}"
                )
    elif "reviewer" in node_id:
        for key in [
            "methodology_faithfulness",
            "biological_plausibility",
            "artifact_completeness",
            "overall_verdict",
            "should_refine",
        ]:
            if key in outputs:
                highlights.append(f"{key}={outputs[key]}")
    return highlights


def summarize_trace(trace: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_id": trace.get("node_id", ""),
        "role": trace.get("role", ""),
        "status": trace.get("status", ""),
        "input_keys": sorted(trace.get("inputs", {}).keys()),
        "output_keys": sorted(trace.get("outputs", {}).keys()) if isinstance(trace.get("outputs"), dict) else [],
        "highlights": highlight_for_trace(trace),
        "cost": trace.get("cost", {}),
    }


def comparison_note(spec: CaseSpec) -> str | None:
    if spec.comparison_run_dir is None:
        return None
    primary = load_json(spec.run_dir / "summaries/summary.json")
    other = load_json(spec.comparison_run_dir / "summaries/summary.json")
    primary_cost = primary.get("cost", {}).get("usd", 0.0)
    other_cost = other.get("cost", {}).get("usd", 0.0)
    return (
        f"Comparison run: {spec.comparison_run_dir}. "
        f"Canonical fixed run cost={primary_cost}, comparison run cost={other_cost}; "
        "the comparison run preserved the same scientific package but added budget."
    )


def build_case_record(spec: CaseSpec) -> dict[str, Any]:
    summary = load_json(spec.run_dir / "summaries/summary.json")
    execution_report = load_json(spec.run_dir / "eval/execution_report.json")
    task = summarize_task(get_first_task(execution_report))
    narrative_path = spec.run_dir / "summaries/case_narrative.md"
    narrative = narrative_path.read_text().strip() if narrative_path.exists() else ""
    record = {
        "case_id": spec.case_id,
        "section": spec.section,
        "title": spec.title,
        "run_dir": str(spec.run_dir),
        "task": task,
        "workflow_signature": summary.get("workflow_signature", ""),
        "input_description": spec.input_description,
        "dataset_description": spec.dataset_description,
        "expected_outputs": spec.expected_outputs,
        "evaluation_target": spec.evaluation_target,
        "actual_outputs": extract_generated_artifacts(summary),
        "hard_checks": extract_hard_checks(summary),
        "review": extract_review(summary),
        "metrics": extract_metrics(summary),
        "cost": summary.get("cost", {}),
        "notes": spec.notes,
        "comparison_note": comparison_note(spec),
        "trace": [summarize_trace(trace) for trace in execution_report.get("traces", [])],
        "narrative_path": str(narrative_path),
        "narrative_excerpt": narrative,
    }
    return record


def render_markdown(records: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append("# Experiment 3 Case Study Report")
    lines.append("")
    lines.append("This report is grounded in canonical retained run artifacts under `runs/`.")
    lines.append("Each case below documents the task definition, dataset, expected outputs, actual outputs, hard-check target, and execution trace.")
    lines.append("")
    lines.append("## Coverage")
    lines.append("")
    lines.append("| Section | Case | Workflow | Run dir |")
    lines.append("|---|---|---|---|")
    for record in records:
        lines.append(
            f"| {record['section']} | {record['title']} | `{record['workflow_signature']}` | `{record['run_dir']}` |"
        )
    lines.append("")
    current_section = None
    for record in records:
        if record["section"] != current_section:
            current_section = record["section"]
            lines.append(f"## {current_section}")
            lines.append("")
        lines.append(f"### {record['title']}")
        lines.append("")
        lines.append(f"- `case_id`: `{record['case_id']}`")
        lines.append(f"- Canonical run: `{record['run_dir']}`")
        if record["task"]["title"]:
            lines.append(f"- Task title: {record['task']['title']}")
        if record["task"]["description"]:
            lines.append(f"- Task definition: {record['task']['description'].splitlines()[0]}")
        dataset_label = record["task"].get("dataset_label") or ""
        if dataset_label:
            lines.append(f"- Dataset label: `{dataset_label}`")
        if record["task"].get("candidate_repos"):
            repos = ", ".join(f"`{name}`" for name in record["task"]["candidate_repos"])
            lines.append(f"- Candidate repos: {repos}")
        lines.append(f"- Input: {record['input_description']}")
        lines.append(f"- Dataset: {record['dataset_description']}")
        lines.append("- Expected outputs:")
        for item in record["expected_outputs"]:
            lines.append(f"  - {item}")
        lines.append(f"- Evaluation target: {record['evaluation_target']}")
        lines.append("- Actual outputs:")
        for item in record["actual_outputs"]:
            lines.append(f"  - {item}")
        lines.append("- Hard checks:")
        for item in record["hard_checks"]:
            lines.append(f"  - {item}")
        if record["metrics"]:
            lines.append("- Key metrics:")
            for item in record["metrics"]:
                lines.append(f"  - {item}")
        if record["review"]:
            lines.append("- Reviewer summary:")
            for key, value in record["review"].items():
                if key == "concerns":
                    lines.append("  - concerns:")
                    for concern in value:
                        lines.append(f"    - {concern}")
                else:
                    lines.append(f"  - {key}: {value}")
        lines.append(
            "- Cost: "
            f"usd={record['cost'].get('usd', 0.0)}, "
            f"tool_calls={record['cost'].get('tool_calls', 0)}, "
            f"container_starts={record['cost'].get('container_starts', 0)}"
        )
        lines.append("- Execution trace:")
        for idx, trace in enumerate(record["trace"], start=1):
            lines.append(
                f"  {idx}. `{trace['node_id']}` ({trace['role']}): "
                f"inputs={trace['input_keys']} outputs={trace['output_keys']}"
            )
            if trace["highlights"]:
                for highlight in trace["highlights"]:
                    lines.append(f"     - {highlight}")
            cost = trace["cost"]
            if cost:
                lines.append(
                    "     - "
                    f"cost: usd={cost.get('usd', 0.0)}, "
                    f"tool_calls={cost.get('tool_calls', 0)}, "
                    f"container_starts={cost.get('container_starts', 0)}"
                )
        for note in record["notes"]:
            lines.append(f"- Interpretation: {note}")
        if record["comparison_note"]:
            lines.append(f"- Comparison note: {record['comparison_note']}")
        lines.append(f"- Narrative source: `{record['narrative_path']}`")
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    records = [build_case_record(spec) for spec in CANONICAL_CASES]
    REPORT_JSON.write_text(json.dumps(records, indent=2))
    REPORT_MD.write_text(render_markdown(records))
    print(f"Wrote {REPORT_MD}")
    print(f"Wrote {REPORT_JSON}")


if __name__ == "__main__":
    main()
