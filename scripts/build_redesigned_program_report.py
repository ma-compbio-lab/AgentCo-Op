from __future__ import annotations

import ast
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, Rectangle
from PIL import Image, ImageOps, ImageDraw


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "output" / "reports"
FIGURES_DIR = OUTPUT_DIR / "figures"

COMBINED_REPORT_MD = OUTPUT_DIR / "redesigned_program_report.md"
COMBINED_REPORT_JSON = OUTPUT_DIR / "redesigned_program_report.json"
BENCHMARK_REPORT_MD = OUTPUT_DIR / "benchmark_program_report.md"
BENCHMARK_REPORT_JSON = OUTPUT_DIR / "benchmark_program_report.json"
CASE_STUDY_REPORT_MD = OUTPUT_DIR / "experiment3_case_study_report.md"
CASE_STUDY_REPORT_JSON = OUTPUT_DIR / "experiment3_case_study_report.json"

BENCHMARK_DETAIL_JSON = OUTPUT_DIR / "benchmark_detailed_report.json"


@dataclass(frozen=True)
class Exp3CaseSpec:
    case_slug: str
    label: str
    run_dir: Path
    metric_paths: dict[str, Path]
    hero_images: list[Path]


EXP3_CASES = [
    Exp3CaseSpec(
        case_slug="experiment3_1",
        label="Experiment 3.1: Closed-loop Spatial Panel Design and Validation",
        run_dir=PROJECT_ROOT / "runs-real-exp3/closed-loop-spatial-panel-design/20260317_082131_57593e92",
        metric_paths={
            "panel_design_summary": PROJECT_ROOT
            / "runs-real-exp3/closed-loop-spatial-panel-design/20260317_082131_57593e92/artifacts/case_output/panel_design_summary.json",
            "panel_validation_summary": PROJECT_ROOT
            / "runs-real-exp3/closed-loop-spatial-panel-design/20260317_082131_57593e92/artifacts/case_output/panel_validation_summary.json",
        },
        hero_images=[
            PROJECT_ROOT
            / "runs-real-exp3/closed-loop-spatial-panel-design/20260317_082131_57593e92/artifacts/case_output/panel_marker_scores.png",
            PROJECT_ROOT
            / "runs-real-exp3/closed-loop-spatial-panel-design/20260317_082131_57593e92/artifacts/case_output/panel_spatial_clusters.png",
        ],
    ),
    Exp3CaseSpec(
        case_slug="experiment3_2",
        label="Experiment 3.2: cell2location Repository Transfer",
        run_dir=PROJECT_ROOT / "runs-real-exp3/cell2location-repo-transfer/20260317_092100_d009c9c9",
        metric_paths={
            "mapping_summary": PROJECT_ROOT
            / "runs-real-exp3/cell2location-repo-transfer/20260317_092100_d009c9c9/artifacts/case_output/mapping_summary.json",
        },
        hero_images=[
            PROJECT_ROOT
            / "runs-real-exp3/cell2location-repo-transfer/20260317_092100_d009c9c9/artifacts/case_output/cell_abundance_panel.png",
            PROJECT_ROOT
            / "runs-real-exp3/cell2location-repo-transfer/20260317_092100_d009c9c9/artifacts/case_output/leiden_regions.png",
        ],
    ),
    Exp3CaseSpec(
        case_slug="experiment3_3",
        label="Experiment 3.3: Spatially Grounded CRISPR Specialist Collaboration",
        run_dir=PROJECT_ROOT / "runs-real-exp3/spatial-crispr-specialist-collaboration/20260317_091148_0e521402",
        metric_paths={
            "spatial_context_report": PROJECT_ROOT
            / "runs-real-exp3/spatial-crispr-specialist-collaboration/20260317_091148_0e521402/artifacts/case_output/spatial_context_report.json",
            "routed_objective": PROJECT_ROOT
            / "runs-real-exp3/spatial-crispr-specialist-collaboration/20260317_091148_0e521402/artifacts/case_output/routed_objective.json",
            "hitrate_eval": PROJECT_ROOT
            / "runs-real-exp3/spatial-crispr-specialist-collaboration/20260317_091148_0e521402/artifacts/case_output/hitrate_eval.json",
        },
        hero_images=[
            PROJECT_ROOT
            / "runs-real-exp3/spatial-crispr-specialist-collaboration/20260317_091148_0e521402/artifacts/case_output/suppressive_niche_map.png",
            PROJECT_ROOT
            / "runs-real-exp3/spatial-crispr-specialist-collaboration/20260317_091148_0e521402/artifacts/case_output/objective_rationale_figure.png",
        ],
    ),
]


BENCHMARK_POLICY = {
    "math": {
        "planned_base_model": "gpt-4o-mini",
        "metric": "solve_rate",
        "split": "AFlow released package: 119 validation / 486 test",
        "workflow_family": "reason_execute_select",
    },
    "humaneval": {
        "planned_base_model": "gpt-4o-mini",
        "metric": "pass@1",
        "split": "AFlow released package: 33 validation / 131 test",
        "workflow_family": "code_generate_test_repair",
    },
    "medqa": {
        "planned_base_model": "gpt-5-nano",
        "metric": "accuracy",
        "split": "full test: 1273",
        "workflow_family": "direct_answer",
    },
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def rel(path: Path) -> str:
    return os.path.relpath(path, OUTPUT_DIR)


def ensure_dirs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)


def pretty_float(value: float | int | None, digits: int = 4) -> str:
    if value is None:
        return "-"
    return f"{float(value):.{digits}f}"


def shorten(text: str, limit: int = 180) -> str:
    normalized = " ".join(str(text).split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def parse_repo_value(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                return ast.literal_eval(stripped)
            except (ValueError, SyntaxError):
                return value
    return value


def render_markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    normalized_rows = [[str(cell) for cell in row] for row in rows]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in normalized_rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def draw_workflow_signature(ax: Any, title: str, signature: str, pattern: str, gates: list[str]) -> None:
    nodes = [item.strip() for item in signature.split("->") if item.strip()]
    ax.set_axis_off()
    if not nodes:
        ax.text(0.5, 0.5, "No workflow signature", ha="center", va="center")
        return
    width = 0.12 if len(nodes) > 6 else 0.16
    total_width = len(nodes) * width + max(0, len(nodes) - 1) * 0.03
    start_x = max(0.02, (1.0 - total_width) / 2.0)
    y = 0.53
    for idx, node in enumerate(nodes):
        x = start_x + idx * (width + 0.03)
        lowered = node.lower()
        facecolor = "#dbeafe"
        if "review" in lowered or "validator" in lowered:
            facecolor = "#fde68a"
        elif "repair" in lowered or "refined" in lowered:
            facecolor = "#fecaca"
        elif "runner" in lowered or "specialist" in lowered:
            facecolor = "#c7f9cc"
        rect = Rectangle((x, y - 0.09), width, 0.18, facecolor=facecolor, edgecolor="#334155", linewidth=1.0)
        ax.add_patch(rect)
        ax.text(x + width / 2.0, y, node, ha="center", va="center", fontsize=8, wrap=True)
        if idx < len(nodes) - 1:
            arrow = FancyArrowPatch(
                (x + width, y),
                (x + width + 0.03, y),
                arrowstyle="-|>",
                mutation_scale=10,
                linewidth=1.0,
                color="#475569",
            )
            ax.add_patch(arrow)
    ax.text(0.02, 0.95, title, ha="left", va="top", fontsize=10, fontweight="bold")
    ax.text(0.02, 0.12, f"pattern={pattern}", ha="left", va="bottom", fontsize=8, color="#334155")
    gate_text = ", ".join(gates) if gates else "none"
    ax.text(0.02, 0.03, f"activated_gates={gate_text}", ha="left", va="bottom", fontsize=8, color="#475569")


def build_exp3_workflow_figure(exp3_cases: list[dict[str, Any]]) -> Path:
    figure_path = FIGURES_DIR / "exp3_workflows.png"
    fig, axes = plt.subplots(len(exp3_cases), 1, figsize=(14, 3.0 * len(exp3_cases)))
    if len(exp3_cases) == 1:
        axes = [axes]
    for ax, case in zip(axes, exp3_cases):
        draw_workflow_signature(
            ax,
            case["label"],
            str(case["summary"].get("workflow_signature", "")),
            str(case["summary"].get("workflow_pattern", "")),
            list(case["summary"].get("activated_gates", [])),
        )
    fig.tight_layout()
    fig.savefig(figure_path, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return figure_path


def build_exp3_metrics_figure(exp3_cases: list[dict[str, Any]]) -> Path:
    figure_path = FIGURES_DIR / "exp3_metrics.png"
    labels = [case["short_label"] for case in exp3_cases]
    usd = [float(case["summary"].get("cost", {}).get("usd", 0.0)) for case in exp3_cases]
    wall = [float(case["summary"].get("cost", {}).get("wall_time_s", 0.0)) for case in exp3_cases]
    tools = [float(case["summary"].get("cost", {}).get("tool_calls", 0.0)) for case in exp3_cases]
    fig, axes = plt.subplots(1, 3, figsize=(14, 4))
    metric_specs = [
        ("Estimated USD", usd, "#2563eb"),
        ("Wall Time (s)", wall, "#dc2626"),
        ("Tool Calls", tools, "#059669"),
    ]
    for ax, (title, values, color) in zip(axes, metric_specs):
        ax.bar(labels, values, color=color, alpha=0.85)
        ax.set_title(title)
        ax.tick_params(axis="x", rotation=15)
        for idx, value in enumerate(values):
            ax.text(idx, value, pretty_float(value, 3), ha="center", va="bottom", fontsize=8)
    fig.tight_layout()
    fig.savefig(figure_path, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return figure_path


def build_budget_frontier_figure(benchmark_report: dict[str, Any]) -> Path:
    figure_path = FIGURES_DIR / "benchmark_budget_frontiers.png"
    historical = benchmark_report.get("historical_budget_sweeps", {})
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.2))
    for ax, benchmark_name in zip(axes, ["math", "humaneval"]):
        rows = historical.get(benchmark_name, {}).get("summary", {}).get("rows", [])
        metric_key = "solve_rate" if benchmark_name == "math" else "pass_at_1"
        for row in rows:
            ax.scatter(float(row.get("average_usd", 0.0)), float(row.get(metric_key, 0.0)), s=45)
            ax.annotate(
                f"{row.get('model', '').replace('openai_', '')}:{row.get('budget_tier', '')}",
                (float(row.get("average_usd", 0.0)), float(row.get(metric_key, 0.0))),
                fontsize=7,
                xytext=(4, 4),
                textcoords="offset points",
            )
        ax.set_title(f"{benchmark_name.capitalize()} Experiment 2 frontier")
        ax.set_xlabel("Average USD")
        ax.set_ylabel(metric_key)
        ax.grid(alpha=0.25, linewidth=0.4)
    fig.tight_layout()
    fig.savefig(figure_path, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return figure_path


def _fit_with_caption(path: Path, size: tuple[int, int], caption: str) -> Image.Image:
    image = Image.open(path).convert("RGB")
    image = ImageOps.contain(image, (size[0], size[1] - 36))
    canvas = Image.new("RGB", size, "white")
    offset_x = (size[0] - image.width) // 2
    offset_y = 8
    canvas.paste(image, (offset_x, offset_y))
    draw = ImageDraw.Draw(canvas)
    draw.text((10, size[1] - 24), caption, fill="black")
    return canvas


def build_exp3_artifact_montage(exp3_cases: list[dict[str, Any]]) -> Path:
    figure_path = FIGURES_DIR / "exp3_artifact_montage.png"
    cell_size = (520, 340)
    cols = 2
    rows = math.ceil(sum(len(case["hero_images"]) for case in exp3_cases) / cols)
    canvas = Image.new("RGB", (cols * cell_size[0], rows * cell_size[1]), "white")
    idx = 0
    for case in exp3_cases:
        for image_path in case["hero_images"]:
            tile = _fit_with_caption(image_path, cell_size, f"{case['short_label']}: {image_path.name}")
            x = (idx % cols) * cell_size[0]
            y = (idx // cols) * cell_size[1]
            canvas.paste(tile, (x, y))
            idx += 1
    canvas.save(figure_path)
    return figure_path


def normalize_exp3_case(spec: Exp3CaseSpec) -> dict[str, Any]:
    summary = load_json(spec.run_dir / "summaries" / "summary.json")
    analysis = (spec.run_dir / "summaries" / "analysis.md").read_text(encoding="utf-8")
    execution_report = load_json(spec.run_dir / "eval" / "execution_report.json")
    metrics = {name: load_json(path) for name, path in spec.metric_paths.items()}
    selected_repos = {
        key: parse_repo_value(value) for key, value in dict(summary.get("selected_repos", {})).items()
    }
    selected_specialists = {
        key: value for key, value in dict(summary.get("selected_specialists", {})).items()
    }
    return {
        "case_slug": spec.case_slug,
        "short_label": spec.case_slug.replace("experiment3_", "E3.").replace("_", "-"),
        "label": spec.label,
        "run_dir": spec.run_dir,
        "summary": summary,
        "analysis": analysis,
        "execution_report": execution_report,
        "node_results": execution_report.get("node_results", {}),
        "metrics": metrics,
        "selected_repos": selected_repos,
        "selected_specialists": selected_specialists,
        "hero_images": spec.hero_images,
    }


def collect_benchmark_program(benchmark_report: dict[str, Any]) -> dict[str, Any]:
    benchmarks = benchmark_report.get("benchmarks", {})
    rows = []
    details = {}
    for benchmark_name in ["math", "humaneval", "medqa"]:
        policy = BENCHMARK_POLICY[benchmark_name]
        entry = benchmarks.get(benchmark_name, {})
        current_status = str(entry.get("current_status", "unknown"))
        if benchmark_name == "medqa":
            latest = entry.get("current_full_run", {}).get("summary", {})
            headline = f"accuracy={pretty_float(latest.get('accuracy'))}" if latest else "no completed full run"
            status_note = "current full baseline available"
        elif benchmark_name == "humaneval":
            latest = entry.get("historical_full_run", {}).get("summary", {})
            validation_v2 = entry.get("validation_compare", {}).get("v2", {}).get("summary", {})
            headline = (
                f"historical full pass@1={pretty_float(latest.get('pass_at_1'))}; "
                f"v2 validation pass@1={pretty_float(validation_v2.get('pass_at_1'))}"
            )
            status_note = "historical full run available; v2 full repeat was not completed after redesign stop"
        else:
            latest = entry.get("historical_invalidated_run", {}).get("summary", {})
            validation_v2 = entry.get("validation_compare", {}).get("v2", {}).get("summary", {})
            headline = (
                f"invalidated historical solve_rate={pretty_float(latest.get('solve_rate'))}; "
                f"v2 validation solve_rate={pretty_float(validation_v2.get('solve_rate'))}"
            )
            status_note = "historical full run invalidated by answer leakage; only v2 validation is currently trustworthy"
        workflow_graph = str(entry.get("current_workflow", {}).get("graph", "-"))
        rows.append(
            {
                "benchmark": benchmark_name,
                "planned_base_model": policy["planned_base_model"],
                "split": policy["split"],
                "metric": policy["metric"],
                "workflow_graph": workflow_graph,
                "status": current_status,
                "headline": headline,
                "note": status_note,
            }
        )
        details[benchmark_name] = {
            "policy": policy,
            "entry": entry,
        }
    return {
        "rows": rows,
        "details": details,
    }


def render_benchmark_markdown(benchmark_program: dict[str, Any], budget_frontier_path: Path) -> str:
    table_rows = [
        [
            row["benchmark"],
            row["planned_base_model"],
            row["split"],
            row["metric"],
            row["workflow_graph"],
            row["status"],
            row["headline"],
            row["note"],
        ]
        for row in benchmark_program["rows"]
    ]
    lines = [
        "# Benchmark Program Report",
        "",
        "## Overview",
        render_markdown_table(
            [
                "Benchmark",
                "Planned base model",
                "Dataset split",
                "Metric",
                "Current workflow",
                "Status",
                "Latest evidence",
                "Interpretation",
            ],
            table_rows,
        ),
        "",
        "## Experiment 2 Budget Visualization",
        f"![Benchmark budget frontiers]({rel(budget_frontier_path)})",
        "",
    ]
    for benchmark_name in ["math", "humaneval", "medqa"]:
        entry = benchmark_program["details"][benchmark_name]["entry"]
        current_workflow = entry.get("current_workflow", {})
        lines.extend(
            [
                f"## {benchmark_name.upper()}",
                f"- Workflow graph: `{current_workflow.get('graph', '-')}`",
                f"- Gate rule: {current_workflow.get('gate_rule', '-')}",
                f"- Input contract: {current_workflow.get('input_contract', '-')}",
                f"- Expected output: {current_workflow.get('expected_output', '-')}",
            ]
        )
        takeaways = entry.get("current_takeaways", [])
        if takeaways:
            lines.append("- Takeaways:")
            for takeaway in takeaways:
                lines.append(f"  - {takeaway}")
        if benchmark_name == "math":
            delta = entry.get("validation_compare", {}).get("delta", {})
            lines.append(
                f"- Validation delta after redesign: solve_rate {pretty_float(delta.get('solve_rate'))}, "
                f"average_usd {pretty_float(delta.get('average_usd'), 6)}, "
                f"latency_s {pretty_float(delta.get('average_latency_s'))}"
            )
        if benchmark_name == "humaneval":
            delta = entry.get("validation_compare", {}).get("delta", {})
            lines.append(
                f"- Validation delta after redesign: pass@1 {pretty_float(delta.get('pass_at_1'))}, "
                f"average_usd {pretty_float(delta.get('average_usd'), 6)}, "
                f"latency_s {pretty_float(delta.get('average_latency_s'))}"
            )
        if benchmark_name == "medqa":
            delta = entry.get("delta_v2_vs_baseline", {})
            lines.append(
                f"- v2 vs baseline full-run delta: accuracy {pretty_float(delta.get('accuracy'))}, "
                f"average_usd {pretty_float(delta.get('average_usd'), 6)}, "
                f"review_count {delta.get('review_count_delta', '-')}"
            )
        lines.append("")
    return "\n".join(lines).replace("\n  -", "\n-")


def render_case_detail(case: dict[str, Any]) -> list[str]:
    summary = case["summary"]
    compile_trace = summary.get("compile_trace", {})
    input_assets = summary.get("input_assets", {})
    graph_design = summary.get("graph_design", {})
    lines = [
        f"## {case['label']}",
        f"- Run dir: `{case['run_dir']}`",
        f"- Success: `{summary.get('success')}`",
        f"- Workflow signature: `{summary.get('workflow_signature', '')}`",
        f"- Workflow pattern: `{summary.get('workflow_pattern', '')}`",
        f"- Cost: `{json.dumps(summary.get('cost', {}), ensure_ascii=True)}`",
        f"- Activated gates: `{json.dumps(summary.get('activated_gates', []), ensure_ascii=True)}`",
        f"- Active subgraphs: `{json.dumps(summary.get('active_subgraphs', []), ensure_ascii=True)}`",
        f"- Input assets: `{shorten(json.dumps(input_assets, ensure_ascii=True), 420)}`",
        f"- Expected outputs: `{json.dumps(summary.get('expected_outputs', []), ensure_ascii=True)}`",
        f"- Graph design: `{shorten(json.dumps(graph_design, ensure_ascii=True), 500)}`",
        f"- Candidate graphs: `{shorten(json.dumps(compile_trace.get('candidate_graphs', []), ensure_ascii=True), 420)}`",
        f"- Selected repos: `{shorten(json.dumps(case['selected_repos'], ensure_ascii=True), 420)}`",
        f"- Selected specialists: `{shorten(json.dumps(case['selected_specialists'], ensure_ascii=True), 420)}`",
    ]
    if case["case_slug"] == "experiment3_1":
        design = case["metrics"]["panel_design_summary"]
        validation = case["metrics"]["panel_validation_summary"]
        lines.extend(
            [
                f"- Panel size requested/actual: `{design.get('panel_size_requested')}/{design.get('panel_size_actual')}`",
                f"- Backup genes requested/actual: `{design.get('backup_gene_count_requested')}/{design.get('backup_gene_count')}`",
                f"- Label coverage: `{design.get('label_count')}` labels",
                f"- Validation score: `{pretty_float(validation.get('validation_score'))}`",
                f"- ARI / NMI: `{pretty_float(validation.get('ari'))}` / `{pretty_float(validation.get('nmi'))}`",
                f"- Replan triggered: `{validation.get('needs_replan')}` with replacements `{validation.get('replacement_candidates', [])[:5]}`",
            ]
        )
    elif case["case_slug"] == "experiment3_2":
        mapping = case["metrics"]["mapping_summary"]
        repo_scout_trace = case["node_results"].get("repo_scout", {}).get("trace", {})
        tool_discovery = repo_scout_trace.get("tool_discovery", {})
        lines.extend(
            [
                f"- Shared genes: `{mapping.get('shared_gene_count')}`",
                f"- Cell-type count: `{mapping.get('cell_type_count')}`",
                f"- Held-out slide: `{mapping.get('heldout_slide')}`",
                f"- N_cells_per_location / detection_alpha: `{mapping.get('N_cells_per_location')}` / `{mapping.get('detection_alpha')}`",
                f"- ARI / NMI vs manual layer regions: `{pretty_float(mapping.get('ari_manual_layer_vs_region_cluster'))}` / `{pretty_float(mapping.get('nmi_manual_layer_vs_region_cluster'))}`",
                f"- RepoScout tool discovery candidates: `{json.dumps(tool_discovery.get('selected_candidates', []), ensure_ascii=True)}`",
                f"- RepoScout executed tool loop: `{json.dumps(repo_scout_trace.get('tool_loop', []), ensure_ascii=True)}`",
            ]
        )
    elif case["case_slug"] == "experiment3_3":
        routed = case["metrics"]["routed_objective"]
        hitrate = case["metrics"]["hitrate_eval"]
        router_trace = case["node_results"].get("specialist_router", {}).get("trace", {})
        top_genes = []
        ranked_path = case["run_dir"] / "artifacts" / "case_output" / "ranked_gene_batch.tsv"
        if ranked_path.exists():
            lines_raw = ranked_path.read_text(encoding="utf-8").splitlines()
            top_genes = [line.split("\t")[1] for line in lines_raw[1:11] if len(line.split("\t")) >= 2]
        lines.extend(
            [
                f"- Routed objective: `{routed.get('routed_objective')}`",
                f"- Routing confidence: `{pretty_float(routed.get('routing_confidence'))}`",
                f"- Objective scores: `{json.dumps(routed.get('objective_scores', {}), ensure_ascii=True)}`",
                f"- Specialist handoff contract: `{shorten(json.dumps(router_trace.get('handoff_contract', {}), ensure_ascii=True), 420)}`",
                f"- Hitrate summary: `{hitrate.get('summary_line', '')}`",
                f"- Top ranked genes after refinement: `{top_genes}`",
                "- Specialist integration caveat: current wrappers execute repo-native specialist code in isolated sandboxes, but they do not yet expose the full internal tool/skill-retrieval stack of those repos.",
            ]
        )
    lines.append(f"- Analysis note: `{shorten(case['analysis'], 550)}`")
    return lines


def render_experiment3_markdown(
    exp3_cases: list[dict[str, Any]],
    workflow_path: Path,
    metrics_path: Path,
    montage_path: Path,
) -> str:
    table_rows = []
    for case in exp3_cases:
        summary = case["summary"]
        table_rows.append(
            [
                case["short_label"],
                summary.get("workflow_pattern", ""),
                summary.get("workflow_signature", ""),
                "yes" if summary.get("success") else "no",
                pretty_float(summary.get("cost", {}).get("usd"), 4),
                pretty_float(summary.get("cost", {}).get("wall_time_s"), 2),
                ", ".join(summary.get("activated_gates", [])) or "none",
                ", ".join(Path(path).name for path in summary.get("artifact_paths", [])[:4]),
            ]
        )
    lines = [
        "# Experiment 3 Case Study Report",
        "",
        "## Overview Table",
        render_markdown_table(
            [
                "Case",
                "Pattern",
                "Active workflow",
                "Success",
                "USD",
                "Wall time (s)",
                "Activated gates",
                "Representative artifacts",
            ],
            table_rows,
        ),
        "",
        "## Workflow Visualizations",
        f"![Experiment 3 workflow graphs]({rel(workflow_path)})",
        "",
        "## Runtime / Cost Comparison",
        f"![Experiment 3 runtime metrics]({rel(metrics_path)})",
        "",
        "## Artifact Montage",
        f"![Experiment 3 artifact montage]({rel(montage_path)})",
        "",
    ]
    for case in exp3_cases:
        lines.extend(render_case_detail(case))
        lines.append("")
        for image_path in case["hero_images"]:
            lines.append(f"![{case['short_label']} - {image_path.name}]({rel(image_path)})")
        lines.append("")
    return "\n".join(lines)


def render_combined_markdown(
    benchmark_markdown: str,
    experiment3_markdown: str,
    exp3_cases: list[dict[str, Any]],
) -> str:
    rows = []
    for case in exp3_cases:
        summary = case["summary"]
        rows.append(
            [
                case["short_label"],
                case["label"],
                summary.get("workflow_pattern", ""),
                summary.get("workflow_signature", ""),
                pretty_float(summary.get("cost", {}).get("usd"), 4),
                pretty_float(summary.get("cost", {}).get("wall_time_s"), 2),
                str(case["run_dir"]),
            ]
        )
    header = [
        "# Redesigned Experimental Program Report",
        "",
        "## Experiment 3 Canonical Runs",
        render_markdown_table(
            [
                "Slug",
                "Title",
                "Pattern",
                "Workflow",
                "USD",
                "Wall time (s)",
                "Run dir",
            ],
            rows,
        ),
        "",
    ]
    return "\n".join(header) + benchmark_markdown + "\n\n" + experiment3_markdown


def main() -> None:
    ensure_dirs()
    benchmark_report = load_json(BENCHMARK_DETAIL_JSON)
    benchmark_program = collect_benchmark_program(benchmark_report)
    exp3_cases = [normalize_exp3_case(spec) for spec in EXP3_CASES]

    workflow_path = build_exp3_workflow_figure(exp3_cases)
    metrics_path = build_exp3_metrics_figure(exp3_cases)
    montage_path = build_exp3_artifact_montage(exp3_cases)
    budget_frontier_path = build_budget_frontier_figure(benchmark_report)

    benchmark_markdown = render_benchmark_markdown(benchmark_program, budget_frontier_path)
    experiment3_markdown = render_experiment3_markdown(exp3_cases, workflow_path, metrics_path, montage_path)
    combined_markdown = render_combined_markdown(benchmark_markdown, experiment3_markdown, exp3_cases)

    combined_json = {
        "benchmark_program": benchmark_program,
        "experiment3_cases": [
            {
                "case_slug": case["case_slug"],
                "label": case["label"],
                "run_dir": str(case["run_dir"]),
                "summary": case["summary"],
                "metrics": case["metrics"],
                "selected_repos": case["selected_repos"],
                "selected_specialists": case["selected_specialists"],
                "hero_images": [str(path) for path in case["hero_images"]],
            }
            for case in exp3_cases
        ],
        "figures": {
            "workflow_path": str(workflow_path),
            "metrics_path": str(metrics_path),
            "montage_path": str(montage_path),
            "budget_frontier_path": str(budget_frontier_path),
        },
    }

    COMBINED_REPORT_MD.write_text(combined_markdown, encoding="utf-8")
    COMBINED_REPORT_JSON.write_text(json.dumps(combined_json, ensure_ascii=True, indent=2), encoding="utf-8")
    BENCHMARK_REPORT_MD.write_text(benchmark_markdown, encoding="utf-8")
    BENCHMARK_REPORT_JSON.write_text(
        json.dumps(combined_json["benchmark_program"], ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    CASE_STUDY_REPORT_MD.write_text(experiment3_markdown, encoding="utf-8")
    CASE_STUDY_REPORT_JSON.write_text(
        json.dumps(combined_json["experiment3_cases"], ensure_ascii=True, indent=2),
        encoding="utf-8",
    )

    print(COMBINED_REPORT_MD)
    print(COMBINED_REPORT_JSON)
    print(BENCHMARK_REPORT_MD)
    print(CASE_STUDY_REPORT_MD)


if __name__ == "__main__":
    main()
