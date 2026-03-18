#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import textwrap
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
from PIL import Image, ImageDraw, ImageOps


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "output" / "reports"
FIGURES_DIR = OUTPUT_DIR / "figures"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_dirs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)


def rel_from_reports(path: Path) -> str:
    return path.relative_to(OUTPUT_DIR).as_posix()


def md_table(headers: list[str], rows: list[list[Any]]) -> str:
    normalized = [[str(cell) for cell in row] for row in rows]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in normalized)
    return "\n".join(lines)


def shorten(text: str, limit: int = 180) -> str:
    normalized = " ".join(str(text).split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def pretty_metric(value: float | int | None, digits: int = 4) -> str:
    if value is None:
        return "-"
    return f"{float(value):.{digits}f}"


def wrap(text: str, width: int) -> str:
    return "\n".join(textwrap.wrap(text, width=width))


def latest_run_dir(parent: Path) -> Path | None:
    if not parent.exists():
        return None
    candidates = sorted([path for path in parent.iterdir() if path.is_dir()])
    return candidates[-1] if candidates else None


def find_latest_task_result_runs(base_glob: str) -> list[Path]:
    paths = []
    for task_result_path in sorted(PROJECT_ROOT.glob(base_glob)):
        run_dir = task_result_path.parent.parent
        paths.append(run_dir)
    return paths


def load_task_results(task_results_path: Path) -> list[dict[str, Any]]:
    data = load_json(task_results_path)
    if isinstance(data, dict):
        return list(data.get("task_results") or data.get("results") or [])
    return list(data)


def read_summary(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    return load_json(path)


def role_style(node_label: str) -> tuple[str, str]:
    lowered = node_label.lower()
    if "review" in lowered or "validator" in lowered:
        return "#FFF1B8", "#8C6D1F"
    if "repair" in lowered or "refin" in lowered:
        return "#FFD6E0", "#9C275F"
    if "runner" in lowered or "tool" in lowered or "specialist" in lowered:
        return "#D7F5DE", "#216E39"
    if "selector" in lowered or "router" in lowered or "planner" in lowered:
        return "#DDEBFF", "#1D4ED8"
    return "#E8E8F0", "#4B5563"


def draw_story_lane(
    ax: Any,
    *,
    title: str,
    subtitle: str,
    nodes: list[str],
    badges: list[str],
    note: str,
) -> None:
    ax.set_axis_off()
    lane = FancyBboxPatch(
        (0.01, 0.06),
        0.98,
        0.88,
        boxstyle="round,pad=0.012,rounding_size=0.02",
        facecolor="#F8FAFC",
        edgecolor="#CBD5E1",
        linewidth=1.2,
    )
    ax.add_patch(lane)
    ax.text(0.03, 0.90, title, fontsize=12, fontweight="bold", ha="left", va="top", color="#0F172A")
    ax.text(0.03, 0.80, subtitle, fontsize=9.5, ha="left", va="top", color="#334155")

    badge_x = 0.72
    for idx, badge in enumerate(badges[:3]):
        bx = badge_x + idx * 0.09
        patch = FancyBboxPatch(
            (bx, 0.84),
            0.08,
            0.07,
            boxstyle="round,pad=0.01,rounding_size=0.015",
            facecolor="#E2E8F0",
            edgecolor="#94A3B8",
            linewidth=0.8,
        )
        ax.add_patch(patch)
        ax.text(bx + 0.04, 0.875, badge, ha="center", va="center", fontsize=8, color="#0F172A")

    width = min(0.12, 0.72 / max(len(nodes), 1))
    gap = 0.02
    total = len(nodes) * width + max(0, len(nodes) - 1) * gap
    start_x = max(0.04, (0.70 - total) / 2)
    y = 0.48
    for idx, node in enumerate(nodes):
        x = start_x + idx * (width + gap)
        shadow = FancyBboxPatch(
            (x + 0.004, y - 0.084),
            width,
            0.16,
            boxstyle="round,pad=0.01,rounding_size=0.02",
            facecolor="#CBD5E1",
            edgecolor="none",
            alpha=0.45,
        )
        face, edge = role_style(node)
        box = FancyBboxPatch(
            (x, y - 0.08),
            width,
            0.16,
            boxstyle="round,pad=0.01,rounding_size=0.02",
            facecolor=face,
            edgecolor=edge,
            linewidth=1.2,
        )
        ax.add_patch(shadow)
        ax.add_patch(box)
        ax.text(x + width / 2, y, wrap(node, 14), ha="center", va="center", fontsize=8.5, color="#0F172A")
        if idx < len(nodes) - 1:
            arrow = FancyArrowPatch(
                (x + width, y),
                (x + width + gap - 0.003, y),
                arrowstyle="-|>",
                mutation_scale=10,
                linewidth=1.0,
                color="#64748B",
            )
            ax.add_patch(arrow)

    ax.text(0.03, 0.17, wrap(note, 120), fontsize=8.5, ha="left", va="top", color="#475569")


def build_story_workflow_figure(
    *,
    path: Path,
    lanes: list[dict[str, Any]],
    title: str,
) -> Path:
    fig, axes = plt.subplots(len(lanes), 1, figsize=(15, 2.8 * len(lanes)))
    if len(lanes) == 1:
        axes = [axes]
    fig.suptitle(title, fontsize=15, fontweight="bold", y=0.995)
    for ax, lane in zip(axes, lanes):
        draw_story_lane(
            ax,
            title=lane["title"],
            subtitle=lane["subtitle"],
            nodes=list(lane["nodes"]),
            badges=list(lane["badges"]),
            note=lane["note"],
        )
    fig.tight_layout(rect=[0, 0, 1, 0.98])
    fig.savefig(path, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return path


def fit_image(path: Path, size: tuple[int, int]) -> Image.Image:
    image = Image.open(path).convert("RGB")
    image = ImageOps.contain(image, size)
    canvas = Image.new("RGB", size, (250, 250, 252))
    offset = ((size[0] - image.width) // 2, (size[1] - image.height) // 2)
    canvas.paste(image, offset)
    return canvas


def build_exp3_gallery(path: Path, cases: list[dict[str, Any]]) -> Path:
    card_w, card_h = 720, 450
    title_h = 70
    margin = 28
    canvas = Image.new(
        "RGB",
        (margin * 2 + card_w * 2, margin * (len(cases) + 1) + len(cases) * (card_h + title_h)),
        (250, 250, 252),
    )
    draw = ImageDraw.Draw(canvas)
    for row, case in enumerate(cases):
        top = margin + row * (card_h + title_h + margin)
        draw.text((margin, top), case["label"], fill=(15, 23, 42))
        draw.text((margin, top + 20), shorten(case["caption"], 120), fill=(71, 85, 105))
        for col, image_path in enumerate(case["images"][:2]):
            if not image_path.exists():
                continue
            left = margin + col * card_w
            panel = fit_image(image_path, (card_w - 20, card_h - 20))
            frame = Image.new("RGB", (card_w, card_h), (255, 255, 255))
            frame_draw = ImageDraw.Draw(frame)
            frame_draw.rounded_rectangle((0, 0, card_w - 1, card_h - 1), 22, outline=(203, 213, 225), width=3)
            frame.paste(panel, (10, 10))
            canvas.paste(frame, (left, top + title_h))
            draw.text((left + 10, top + title_h + card_h + 4), image_path.name, fill=(100, 116, 139))
    canvas.save(path)
    return path


def benchmark_specs() -> dict[str, dict[str, Any]]:
    return {
        "math": {
            "label": "MATH",
            "why": "Stress exact-answer reasoning where a useful workflow needs executable verification, not just a long rationale.",
            "dataset": "Hendrycks MATH level-5 four-type AFlow slice",
            "split": "119 validation / 486 test",
            "metric": "solve_rate",
            "base_model": "gpt-4o-mini",
            "workflow": "solver + programmer -> program_exec -> selector -> [reviewer -> reviser]",
            "nodes": ["solver", "programmer", "program_exec", "selector", "reviewer", "reviser"],
            "aggregate": PROJECT_ROOT / "runs-current-benchmark/math-aggregate/20260318_034850_e4b1dd84/summaries/summary.json",
            "task_results_glob": "runs/aflow-full/math-test-shard*/**/eval/task_results.json",
        },
        "humaneval": {
            "label": "HumanEval",
            "why": "Stress code generation where the workflow should use tests as real evidence instead of relying on self-reported confidence.",
            "dataset": "HumanEval AFlow-aligned released split",
            "split": "33 validation / 131 test",
            "metric": "pass@1",
            "base_model": "gpt-4o-mini",
            "workflow": "coder -> public_test_runner -> [reviewer -> reviser -> retest_runner]",
            "nodes": ["coder", "public_test_runner", "reviewer", "reviser", "retest_runner"],
            "aggregate": PROJECT_ROOT / "runs-current-benchmark/humaneval-aggregate/20260318_034850_c72333b0/summaries/summary.json",
            "task_results_glob": "runs/aflow-full/humaneval-test-shard*/**/eval/task_results.json",
        },
        "medqa": {
            "label": "MedQA",
            "why": "Probe whether the workflow can stay simple on lower-entropy medical MCQ tasks instead of over-engineering review chains.",
            "dataset": "MedQA official English test set",
            "split": "full test: 1273",
            "metric": "accuracy",
            "base_model": "gpt-5-nano",
            "workflow": "solver -> [reviewer -> reviser]",
            "nodes": ["solver", "reviewer", "reviser"],
            "legacy_full": PROJECT_ROOT / "runs/aggregated/medqa/full/medqa-aggregate/20260316_022906_5ca6c968/summaries/summary.json",
            "current_smoke": PROJECT_ROOT / "runs-repair-pass2/medqa-smoke/20260318_034550_65292d8e/summaries/summary.json",
            "partial_glob": "runs-current-benchmark/medqa-full-shard*/**/eval/task_results.json",
            "legacy_task_results_glob": "runs/benchmark-full/medqa-full-shard*/**/eval/task_results.json",
        },
    }


def summarize_math_failures(task_results_glob: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for path in sorted(PROJECT_ROOT.glob(task_results_glob)):
        rows.extend(load_task_results(path))
    exec_pass = 0
    review_count = 0
    representative: dict[str, Any] | None = None
    for row in rows:
        report_path = Path(row["report_path"])
        if not report_path.exists():
            continue
        report = load_json(report_path)
        node_results = report.get("node_results", {})
        program_exec = (node_results.get("program_exec") or {}).get("outputs", {})
        selector = (node_results.get("selector") or {}).get("outputs", {})
        if program_exec.get("passed"):
            exec_pass += 1
        if "reviewer" in node_results:
            review_count += 1
        if representative is None and row.get("failure_type") == "incorrect_answer" and not program_exec.get("passed"):
            solver = (node_results.get("solver") or {}).get("outputs", {})
            representative = {
                "gold_answer": row.get("gold_answer"),
                "solver_answer": solver.get("final_answer"),
                "execution_error": program_exec.get("execution_error"),
                "selector_final_answer": selector.get("final_answer"),
                "selector_notes": selector.get("arbitration_notes"),
            }
    return {
        "task_count": len(rows),
        "execution_success_count": exec_pass,
        "review_count": review_count,
        "representative_failure": representative or {},
    }


def summarize_humaneval_failures(task_results_glob: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for path in sorted(PROJECT_ROOT.glob(task_results_glob)):
        rows.extend(load_task_results(path))
    first_pass = 0
    repaired = 0
    reviewed = 0
    representative: dict[str, Any] | None = None
    for row in rows:
        report_path = Path(row["report_path"])
        if not report_path.exists():
            continue
        report = load_json(report_path)
        node_results = report.get("node_results", {})
        public_test = (node_results.get("public_test_runner") or {}).get("outputs", {})
        retest = (node_results.get("retest_runner") or {}).get("outputs", {})
        reviewer = (node_results.get("reviewer") or {}).get("outputs", {})
        reviser = (node_results.get("reviser") or {}).get("outputs", {})
        if "reviewer" in node_results:
            reviewed += 1
            if retest.get("passed"):
                repaired += 1
        elif public_test.get("passed"):
            first_pass += 1
        if representative is None and row.get("failure_type") == "incorrect_answer" and "reviewer" in node_results:
            representative = {
                "public_test_result": shorten(public_test.get("result", ""), 240),
                "review_notes": shorten(reviewer.get("review_notes", ""), 220),
                "reviser_notes": shorten(reviser.get("notes", ""), 220),
                "retest_result": shorten(retest.get("result", ""), 240),
            }
    return {
        "task_count": len(rows),
        "first_pass_count": first_pass,
        "reviewed_count": reviewed,
        "repaired_count": repaired,
        "representative_failure": representative or {},
    }


def summarize_medqa_current(spec: dict[str, Any]) -> dict[str, Any]:
    legacy_full = read_summary(spec["legacy_full"]) or {}
    current_smoke = read_summary(spec["current_smoke"]) or {}
    rows: list[dict[str, Any]] = []
    for path in sorted(PROJECT_ROOT.glob(spec["partial_glob"])):
        rows.extend(load_task_results(path))
    partial = None
    if rows:
        correct = sum(1 for row in rows if row.get("correct"))
        workflow_patterns = Counter(row.get("workflow_signature", "") for row in rows)
        failure_breakdown = Counter(row.get("failure_type", "none") for row in rows)
        partial = {
            "task_count": len(rows),
            "accuracy": correct / len(rows),
            "workflow_patterns": dict(workflow_patterns),
            "failure_breakdown": dict(failure_breakdown),
        }
    representative_success = None
    for row in rows:
        if not row.get("correct"):
            continue
        if "review" not in (row.get("workflow_signature") or ""):
            continue
        report = load_json(Path(row["report_path"]))
        node_results = report.get("node_results", {})
        solver = (node_results.get("solver") or {}).get("outputs", {})
        reviewer = (node_results.get("reviewer") or {}).get("outputs", {})
        reviser = (node_results.get("reviser") or {}).get("outputs", {})
        representative_success = {
            "solver_answer": solver.get("final_answer_label"),
            "solver_rationale": shorten(solver.get("rationale", ""), 220),
            "reviewer_verdict": reviewer.get("review_verdict"),
            "revised_answer": reviser.get("final_answer_label"),
        }
        break
    return {
        "legacy_full": legacy_full,
        "current_smoke": current_smoke,
        "current_partial": partial,
        "representative_reviewed_success": representative_success or {},
    }


@dataclass(frozen=True)
class Exp3Spec:
    slug: str
    label: str
    why: str
    inputs_summary: str
    expected_summary: str
    summary_path: Path
    hero_images: list[Path]


def exp3_specs() -> list[Exp3Spec]:
    return [
        Exp3Spec(
            slug="experiment3_1",
            label="Experiment 3.1: Closed-loop Spatial Panel Design and Validation",
            why="Test whether the workflow can design a spatial panel, validate it downstream, detect failure, and roll back to redesign instead of stopping at the first artifact bundle.",
            inputs_summary="Mouse-brain single-cell reference, held-out Visium slide, 96-gene panel budget, backup-gene requirement, and validation target on manual layer structure.",
            expected_summary="A candidate panel, backup genes, validation report, replacement trace, and a final panel that survives one full redesign loop.",
            summary_path=PROJECT_ROOT / "runs-real-exp3/closed-loop-spatial-panel-design/20260317_082131_57593e92/summaries/summary.json",
            hero_images=[
                PROJECT_ROOT
                / "runs-real-exp3/closed-loop-spatial-panel-design/20260317_082131_57593e92/artifacts/case_output/panel_marker_scores.png",
                PROJECT_ROOT
                / "runs-real-exp3/closed-loop-spatial-panel-design/20260317_082131_57593e92/artifacts/case_output/panel_spatial_clusters.png",
            ],
        ),
        Exp3Spec(
            slug="experiment3_2",
            label="Experiment 3.2: cell2location Repo Transfer",
            why="Test whether the workflow can read a paper/repo, pick the right repository, adapt a new dataset to the repo contract, and produce paper-style outputs without reimplementing the method.",
            inputs_summary="Official cell2location repo, precomputed reference signatures, held-out Visium mouse-brain slide, and transfer-specific training defaults.",
            expected_summary="Mapped spatial AnnData, abundance maps, Leiden regions, QC figure, and a transfer report documenting adaptation decisions.",
            summary_path=PROJECT_ROOT / "runs-real-exp3/cell2location-repo-transfer/20260317_092100_d009c9c9/summaries/summary.json",
            hero_images=[
                PROJECT_ROOT
                / "runs-real-exp3/cell2location-repo-transfer/20260317_092100_d009c9c9/artifacts/case_output/cell_abundance_panel.png",
                PROJECT_ROOT
                / "runs-real-exp3/cell2location-repo-transfer/20260317_092100_d009c9c9/artifacts/case_output/leiden_regions.png",
            ],
        ),
        Exp3Spec(
            slug="experiment3_3",
            label="Experiment 3.3: Spatially Grounded CRISPR Specialist Collaboration",
            why="Test whether the workflow can reuse existing specialized agents in separate sandboxes and compose them into a new capability instead of building a monolith from scratch.",
            inputs_summary="Breast-cancer Visium sample, SpatialAgent repo, BioDiscoveryAgent repo, and a collaboration objective that requires spatial grounding before perturbation design.",
            expected_summary="Spatial context report, routed objective, ranked perturbation batch, hit-rate evaluation, and a collaboration report explaining the handoff.",
            summary_path=PROJECT_ROOT / "runs-real-exp3/spatial-crispr-specialist-collaboration/20260317_091148_0e521402/summaries/summary.json",
            hero_images=[
                PROJECT_ROOT
                / "runs-real-exp3/spatial-crispr-specialist-collaboration/20260317_091148_0e521402/artifacts/case_output/suppressive_niche_map.png",
                PROJECT_ROOT
                / "runs-real-exp3/spatial-crispr-specialist-collaboration/20260317_091148_0e521402/artifacts/case_output/objective_rationale_figure.png",
            ],
        ),
    ]


def load_exp3_case(spec: Exp3Spec) -> dict[str, Any]:
    summary = load_json(spec.summary_path)
    traces = {trace["node_id"]: trace for trace in summary.get("trace_details", [])}
    key_takeaway = ""
    if spec.slug == "experiment3_1":
        validation_path = Path(summary["artifact_paths"]["panel_validation_summary_path"])
        validation = load_json(validation_path)
        key_takeaway = (
            f"One real replan fired. Validation score ended at {pretty_metric(validation.get('validation_score'))} "
            f"with ARI/NMI {pretty_metric(validation.get('ari'))}/{pretty_metric(validation.get('nmi'))}, "
            "which is operationally successful but still scientifically modest."
        )
    elif spec.slug == "experiment3_2":
        mapping_path = Path(summary["artifact_paths"]["mapping_summary_path"])
        mapping = load_json(mapping_path)
        repo_reason = traces["repo_scout"]["outputs"]["selected_repo"]["reason"]
        key_takeaway = (
            f"RepoScout selected the official cell2location repository. The transfer kept {mapping.get('shared_gene_count')} shared genes "
            f"and produced ARI/NMI {pretty_metric(mapping.get('ari'))}/{pretty_metric(mapping.get('nmi'))}. "
            f"Why that repo: {shorten(repo_reason, 180)}"
        )
    else:
        routed = traces["specialist_a"]["outputs"]
        perturb = traces["specialist_b"]["outputs"]
        key_takeaway = (
            f"SpatialAgent routed the case to {routed.get('routed_objective')} with confidence {pretty_metric(routed.get('routing_confidence'))}; "
            f"BioDiscoveryAgent returned {len(perturb.get('predicted_genes', []))} ranked genes and the repair branch refined the final batch."
        )
    return {
        "spec": spec,
        "summary": summary,
        "traces": traces,
        "key_takeaway": key_takeaway,
    }


def build_benchmark_story_report() -> dict[str, Any]:
    ensure_dirs()
    specs = benchmark_specs()
    math_summary = load_json(specs["math"]["aggregate"])
    humaneval_summary = load_json(specs["humaneval"]["aggregate"])
    medqa_summary = summarize_medqa_current(specs["medqa"])
    math_detail = summarize_math_failures(specs["math"]["task_results_glob"])
    humaneval_detail = summarize_humaneval_failures(specs["humaneval"]["task_results_glob"])

    benchmark_rows = [
        [
            "MATH",
            specs["math"]["dataset"],
            specs["math"]["metric"],
            specs["math"]["base_model"],
            "0.4300",
            "Completed, but underperforming",
        ],
        [
            "HumanEval",
            specs["humaneval"]["dataset"],
            specs["humaneval"]["metric"],
            specs["humaneval"]["base_model"],
            pretty_metric(humaneval_summary.get("pass_at_1")),
            "Completed",
        ],
        [
            "MedQA",
            specs["medqa"]["dataset"],
            specs["medqa"]["metric"],
            specs["medqa"]["base_model"],
            "partial full rerun: 0.7164 on 483 / 1273",
            "Current rerun in progress",
        ],
    ]

    workflow_figure = build_story_workflow_figure(
        path=FIGURES_DIR / "benchmark_story_workflows.png",
        title="Experiment 1: Benchmark Workflows",
        lanes=[
            {
                "title": "MATH",
                "subtitle": "AFlow-aligned exact-answer reasoning benchmark",
                "nodes": specs["math"]["nodes"],
                "badges": ["486 test", "0.4300", "gpt-4o-mini"],
                "note": "Why this task: exact-answer math exposes whether execution-backed verification actually helps. Current evidence says the graph runs reliably, but the selector still picks too many confidently wrong candidates.",
            },
            {
                "title": "HumanEval",
                "subtitle": "AFlow-aligned code benchmark with visible public tests",
                "nodes": specs["humaneval"]["nodes"],
                "badges": ["131 test", pretty_metric(humaneval_summary.get("pass_at_1")), "gpt-4o-mini"],
                "note": "Why this task: code should be repaired by concrete test evidence, not just by critique. The public-test loop is working; most tasks stop after coder -> public_test_runner, and a minority enter repair.",
            },
            {
                "title": "MedQA",
                "subtitle": "Repository extension benchmark for low-entropy medical MCQ",
                "nodes": specs["medqa"]["nodes"],
                "badges": ["1273 test", "0.7164*", "gpt-5-nano"],
                "note": "Why this task: MedQA should reward restraint. The redesigned path is intentionally closer to a strong single-step solver, with review only when the answer is genuinely unstable.",
            },
        ],
    )

    markdown_lines = [
        "# Benchmark Program Report",
        "",
        "## Story",
        "Experiment 1 is meant to answer a narrow question: can the workflow choose the right amount of structure for different benchmark families instead of forcing every task through the same multi-agent recipe?",
        "",
        "The three benchmarks deliberately stress different failure modes:",
        "- `MATH` is about exact answers and executable verification.",
        "- `HumanEval` is about code generation grounded by tests.",
        "- `MedQA` is about not over-engineering a task that often benefits from a strong single-step solver.",
        "",
        "## Status Table",
        md_table(
            ["Benchmark", "Dataset / Split", "Metric", "Base Model", "Current Evidence", "Status"],
            benchmark_rows,
        ),
        "",
        f"![Benchmark workflows]({rel_from_reports(workflow_figure)})",
        "",
        "## MATH",
        f"Why this task: {specs['math']['why']}",
        "",
        md_table(
            ["Field", "Value"],
            [
                ["Input", "Competition math problem statement with exact-answer grading."],
                ["Expected output", "One exact final answer, ideally backed by executable reasoning."],
                ["Workflow", specs["math"]["workflow"]],
                ["Current full result", pretty_metric(math_summary.get("solve_rate"))],
            ],
        ),
        "",
        "Current log interpretation:",
        f"- Program execution succeeded on {math_detail['execution_success_count']} / {math_detail['task_count']} tasks, so the weak score is not mainly a sandbox-crash problem.",
        f"- The review branch almost never fired: {math_detail['review_count']} / {math_detail['task_count']} tasks.",
        "- The dominant failure mode is still confidently wrong answer selection after the graph has already done the expensive work.",
        "",
        "Representative failure pattern:",
        f"- Solver answer: `{math_detail['representative_failure'].get('solver_answer', '-')}`",
        f"- Execution error: `{math_detail['representative_failure'].get('execution_error', '-')}`",
        f"- Selector final answer: `{math_detail['representative_failure'].get('selector_final_answer', '-')}`",
        f"- Selector note: {shorten(math_detail['representative_failure'].get('selector_notes', '-'), 220)}",
        "",
        "Interpretation: the current `reason_execute_select` graph is operational but not yet strong enough. The next real gain will need structural work such as stronger ensemble diversity or better exact-answer verification, not just more generic review text.",
        "",
        "## HumanEval",
        f"Why this task: {specs['humaneval']['why']}",
        "",
        md_table(
            ["Field", "Value"],
            [
                ["Input", "HumanEval prompt plus required Python function signature."],
                ["Expected output", "A function that passes public tests and is evaluated with pass@1."],
                ["Workflow", specs["humaneval"]["workflow"]],
                ["Current full result", pretty_metric(humaneval_summary.get("pass_at_1"))],
            ],
        ),
        "",
        "Current log interpretation:",
        f"- `{humaneval_detail['first_pass_count']}` tasks passed immediately after `coder -> public_test_runner`.",
        f"- `{humaneval_detail['reviewed_count']}` tasks entered the repair branch.",
        f"- `{humaneval_detail['repaired_count']}` of those repaired tasks passed after `reviewer -> reviser -> retest_runner`.",
        "",
        "Representative repair pattern:",
        f"- Public-test failure: {humaneval_detail['representative_failure'].get('public_test_result', '-')}",
        f"- Reviewer note: {humaneval_detail['representative_failure'].get('review_notes', '-')}",
        f"- Retest result: {humaneval_detail['representative_failure'].get('retest_result', '-')}",
        "",
        "Interpretation: this is the healthiest benchmark of the three. The test loop is doing real work. The remaining gap is not “more critique”; it is better repair fidelity or a stronger candidate-generation step before repair.",
        "",
        "## MedQA",
        f"Why this task: {specs['medqa']['why']}",
        "",
        md_table(
            ["Field", "Value"],
            [
                ["Input", "Medical multiple-choice stem plus answer options."],
                ["Expected output", "One option label plus the exact option text."],
                ["Legacy full baseline", f"{pretty_metric(medqa_summary['legacy_full'].get('accuracy'))} with planner -> responder -> [reviewer -> reviser]"],
                ["Current smoke", f"{pretty_metric(medqa_summary['current_smoke'].get('accuracy'))} with review on only {medqa_summary['current_smoke']['gate_activations'].get('direct_answer_review_gate', 0)} / 20 tasks"],
                ["Current partial full rerun", "0.7164 on 483 / 1273 tasks"],
            ],
        ),
        "",
        "Current log interpretation:",
        "- The earlier planner/responder baseline overused review and often amplified weak guesses instead of correcting them.",
        "- The current redesign moved MedQA back toward a direct-answer workflow, which is closer to the task entropy.",
        "- A robustness fix was needed because reviewer outputs sometimes used `output_...` alias keys instead of the expected schema fields; the runtime now normalizes those aliases.",
        "",
        "Current partial full-run evidence:",
        f"- Current partial accuracy: `{pretty_metric(medqa_summary['current_partial']['accuracy'])}` over `{medqa_summary['current_partial']['task_count']}` tasks.",
        f"- Current workflow mix: `{medqa_summary['current_partial']['workflow_patterns']}`",
        f"- Current failure breakdown: `{medqa_summary['current_partial']['failure_breakdown']}`",
        "",
        "Representative reviewed success:",
        f"- Solver answer: `{medqa_summary['representative_reviewed_success'].get('solver_answer', '-')}`",
        f"- Reviewer verdict: `{medqa_summary['representative_reviewed_success'].get('reviewer_verdict', '-')}`",
        f"- Revised answer: `{medqa_summary['representative_reviewed_success'].get('revised_answer', '-')}`",
        "",
        "Interpretation: MedQA is the clearest example that simplicity matters. The current redesign is finally moving in the right direction, but the benchmark is not finished until the new full rerun completes.",
    ]

    md_path = OUTPUT_DIR / "benchmark_program_report.md"
    json_path = OUTPUT_DIR / "benchmark_program_report.json"
    md_path.write_text("\n".join(markdown_lines).strip() + "\n", encoding="utf-8")
    payload = {
        "benchmarks": {
            "math": {"summary": math_summary, "failure_analysis": math_detail},
            "humaneval": {"summary": humaneval_summary, "failure_analysis": humaneval_detail},
            "medqa": medqa_summary,
        },
        "figures": {"workflow": rel_from_reports(workflow_figure)},
    }
    write_json(json_path, payload)
    return {"markdown": md_path, "json": json_path, "payload": payload}


def build_exp3_story_report() -> dict[str, Any]:
    ensure_dirs()
    cases = [load_exp3_case(spec) for spec in exp3_specs()]
    workflow_figure = build_story_workflow_figure(
        path=FIGURES_DIR / "exp3_story_workflows.png",
        title="Experiment 3: Scientific Case Studies",
        lanes=[
            {
                "title": case["spec"].label,
                "subtitle": case["spec"].why,
                "nodes": case["summary"]["workflow_signature"].split("->"),
                "badges": [
                    case["summary"]["workflow_pattern"],
                    f"${pretty_metric(case['summary']['cost'].get('usd'), 3)}",
                    f"{pretty_metric(case['summary']['cost'].get('wall_time_s'), 1)}s",
                ],
                "note": case["key_takeaway"],
            }
            for case in cases
        ],
    )
    gallery = build_exp3_gallery(
        FIGURES_DIR / "exp3_story_gallery.png",
        [
            {
                "label": case["spec"].label,
                "images": case["spec"].hero_images,
                "caption": case["spec"].expected_summary,
            }
            for case in cases
        ],
    )

    overview_rows = []
    for case in cases:
        summary = case["summary"]
        overview_rows.append(
            [
                case["spec"].label,
                case["spec"].inputs_summary,
                ", ".join(summary.get("expected_outputs", [])[:4]) + (" ..." if len(summary.get("expected_outputs", [])) > 4 else ""),
                "success" if summary.get("success") else "failure",
            ]
        )

    lines = [
        "# Experiment 3 Case Study Report",
        "",
        "## Story",
        "Experiment 3 exists to show what the benchmark track cannot show: whether the workflow can leave the comfort of fixed QA/code benchmarks and still stay coherent when the task requires real scientific artifacts, repo reuse, repair loops, or specialist handoffs.",
        "",
        "The redesigned Experiment 3 now follows three escalating stories:",
        "- `Experiment 3.1` asks whether the system can behave like a real closed-loop design workflow.",
        "- `Experiment 3.2` asks whether the system can transfer a paper/repo workflow onto a new dataset.",
        "- `Experiment 3.3` asks whether the system can reuse multiple external specialists instead of rebuilding a monolith from scratch.",
        "",
        "## Overview Table",
        md_table(["Case", "Inputs", "Expected outputs", "Outcome"], overview_rows),
        "",
        f"![Experiment 3 workflows]({rel_from_reports(workflow_figure)})",
        "",
        f"![Experiment 3 gallery]({rel_from_reports(gallery)})",
        "",
    ]

    for case in cases:
        spec = case["spec"]
        summary = case["summary"]
        traces = case["traces"]
        lines.extend(
            [
                f"## {spec.label}",
                "",
                f"Why this task: {spec.why}",
                "",
                md_table(
                    ["Field", "Value"],
                    [
                        ["Inputs", spec.inputs_summary],
                        ["Expected outputs", spec.expected_summary],
                        ["Compiled pattern", summary.get("workflow_pattern", "-")],
                        ["Activated path", summary.get("workflow_signature", "-")],
                    ],
                ),
                "",
            ]
        )
        if spec.slug == "experiment3_1":
            validation = load_json(Path(summary["artifact_paths"]["panel_validation_summary_path"]))
            lines.extend(
                [
                    "What the runtime actually did:",
                    "- The compiler chose `closed_loop_design_validate` rather than a repo-transfer or specialist pattern.",
                    "- Validation on the held-out slide triggered `closed_loop_replan_gate`, so the graph expanded and reran the design stage with replacement genes.",
                    f"- Replacement loop: `{validation.get('replaced_genes', [])}`",
                    "",
                    "How to read the result:",
                    f"- Final validation score: `{pretty_metric(validation.get('validation_score'))}`",
                    f"- ARI / NMI against manual regions: `{pretty_metric(validation.get('ari'))}` / `{pretty_metric(validation.get('nmi'))}`",
                    "- This is a real closed-loop success, but not yet a strong biological panel. The correct claim is executional success with modest recovery quality.",
                    "",
                ]
            )
        elif spec.slug == "experiment3_2":
            repo_scout = traces["repo_scout"]["outputs"]
            repo_runner = traces["repo_runner"]["outputs"]
            validator = traces["validator"]["outputs"]
            lines.extend(
                [
                    "What the runtime actually did:",
                    f"- RepoScout chose `{repo_scout['selected_repo']['name']}` over `{len(repo_scout.get('repo_candidates', [])) - 1}` fallback candidates.",
                    f"- Why that repo: {shorten(repo_scout['selected_repo']['reason'], 220)}",
                    f"- Analysis configuration chosen at compile/runtime: `{repo_scout.get('analysis_config', {})}`",
                    "",
                    "How to read the result:",
                    f"- Shared genes transferred into the mapping run: `{repo_runner.get('shared_gene_count')}`",
                    f"- ARI / NMI vs manual layer regions: `{pretty_metric(repo_runner.get('ari'))}` / `{pretty_metric(repo_runner.get('nmi'))}`",
                    f"- Validator verdict: `{validator.get('overall_verdict')}` with concerns `{validator.get('concerns')}`",
                    "- The important story is not the raw score; it is that the official repo was executed and adapted on a held-out slide after fixing real schema and gene-ID mismatches.",
                    "",
                ]
            )
        else:
            router = traces["specialist_router"]["outputs"]
            specialist_a = traces["specialist_a"]["outputs"]
            specialist_b = traces["specialist_b"]["outputs"]
            lines.extend(
                [
                    "What the runtime actually did:",
                    f"- The router selected specialists `{router.get('selected_specialists')}` and wrote an explicit handoff contract.",
                    f"- SpatialAgent routed the case to `{specialist_a.get('routed_objective')}` with scores `{specialist_a.get('objective_scores')}`.",
                    f"- BioDiscoveryAgent produced `{len(specialist_b.get('predicted_genes', []))}` ranked genes before refinement.",
                    "",
                    "How to read the result:",
                    f"- Activated repair gate: `{summary.get('activated_gates', [])}`",
                    f"- Final selected repos: `{summary.get('selected_repos')}`",
                    "- The run proves cross-sandbox specialist collaboration. The remaining weakness is scientific grounding: routing confidence is still low, so the main next step is better evidence grounding, not more orchestration complexity.",
                    "",
                ]
            )

    md_path = OUTPUT_DIR / "experiment3_case_study_report.md"
    json_path = OUTPUT_DIR / "experiment3_case_study_report.json"
    md_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    payload = {
        "cases": [
            {
                "label": case["spec"].label,
                "summary_path": str(case["spec"].summary_path.relative_to(PROJECT_ROOT)),
                "workflow_pattern": case["summary"].get("workflow_pattern"),
                "workflow_signature": case["summary"].get("workflow_signature"),
                "key_takeaway": case["key_takeaway"],
            }
            for case in cases
        ],
        "figures": {
            "workflow": rel_from_reports(workflow_figure),
            "gallery": rel_from_reports(gallery),
        },
    }
    write_json(json_path, payload)
    return {"markdown": md_path, "json": json_path, "payload": payload}


def build_combined_report(benchmark_report: dict[str, Any], exp3_report: dict[str, Any]) -> dict[str, Any]:
    md_path = OUTPUT_DIR / "redesigned_program_report.md"
    json_path = OUTPUT_DIR / "redesigned_program_report.json"
    lines = [
        "# DynaForge Program Report",
        "",
        "## What This Program Is Trying To Show",
        "The benchmark track and the case-study track are not redundant. They answer different questions.",
        "",
        "- Experiment 1 asks whether the workflow can choose the right amount of structure on standard tasks.",
        "- Experiment 2 remains the budget/protocol layer on top of those benchmark workflows.",
        "- Experiment 3 asks whether the same system can leave closed benchmarks and still organize scientific work coherently.",
        "",
        "## Current Headline Status",
        "- `MATH`: aligned full run completed, but performance is still weak enough that the workflow needs a deeper structural redesign.",
        "- `HumanEval`: aligned full run completed with a healthy public-test loop.",
        "- `MedQA`: current redesigned direct-answer full rerun is in progress and is materially ahead of the older planner/responder baseline on early evidence.",
        "- `Experiment 3.1 / 3.2 / 3.3`: all completed successfully on the compiled-generic path.",
        "",
        "## Report Index",
        f"- Benchmark report: [{benchmark_report['markdown'].name}]({rel_from_reports(benchmark_report['markdown'])})",
        f"- Experiment 3 report: [{exp3_report['markdown'].name}]({rel_from_reports(exp3_report['markdown'])})",
        "",
        "## Read This Report First If",
        "- You want benchmark status and current workflow bottlenecks: start with the benchmark report.",
        "- You want the scientific case-study story and artifact-level evidence: start with the Experiment 3 report.",
        "",
    ]
    md_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    payload = {
        "benchmark_report": rel_from_reports(benchmark_report["markdown"]),
        "experiment3_report": rel_from_reports(exp3_report["markdown"]),
    }
    write_json(json_path, payload)
    return {"markdown": md_path, "json": json_path, "payload": payload}


def build_all_reports() -> dict[str, Any]:
    benchmark_report = build_benchmark_story_report()
    exp3_report = build_exp3_story_report()
    combined_report = build_combined_report(benchmark_report, exp3_report)
    return {
        "benchmark": benchmark_report,
        "exp3": exp3_report,
        "combined": combined_report,
    }


def main() -> None:
    build_all_reports()


if __name__ == "__main__":
    main()
