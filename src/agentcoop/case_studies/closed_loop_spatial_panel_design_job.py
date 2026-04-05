from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Mapping

import matplotlib.pyplot as plt
import pandas as pd

from agentcoop.case_studies.job_utils import ensure_output_dir, load_job_request, write_job_result
from agentcoop.case_studies.spatial_panel_common import (
    build_panel_design,
    load_reference_with_labels,
    resolve_mouse_brain_assets,
    write_json,
    write_table,
)


def _select_repo(candidate_repos: list[dict[str, Any]]) -> str:
    preferred = ("scanpy", "cell2location", "tangram")
    names = {str(repo.get("name", "")).strip(): repo for repo in candidate_repos if isinstance(repo, Mapping)}
    for name in preferred:
        if name in names:
            return name
    if candidate_repos:
        first = candidate_repos[0]
        if isinstance(first, Mapping):
            return str(first.get("name", "unknown_repo")).strip() or "unknown_repo"
    return "unknown_repo"


def _render_score_figure(panel_df: pd.DataFrame, output_path: Path) -> None:
    preview = panel_df.head(24).copy()
    fig, ax = plt.subplots(figsize=(11, 6))
    ax.barh(preview["gene"][::-1], preview["design_score"][::-1], color="#33658A")
    ax.set_title("Top Closed-loop Panel Design Candidates")
    ax.set_xlabel("design score")
    ax.set_ylabel("gene")
    fig.tight_layout()
    fig.savefig(output_path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def _coerce_int(value: Any, default: int) -> int:
    try:
        if value is None:
            raise TypeError
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Closed-loop spatial panel design job.")
    parser.add_argument("--input-json", required=True)
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args(argv)

    _, inputs, _, hints = load_job_request(args.input_json)
    output_dir = ensure_output_dir(hints, args.output_json)
    assets = resolve_mouse_brain_assets(hints)
    design_config = dict(inputs.get("design_config", {})) if isinstance(inputs.get("design_config", {}), Mapping) else {}
    candidate_repos = hints.get("candidate_repos", [])
    selected_repo = _select_repo(candidate_repos if isinstance(candidate_repos, list) else [])

    panel_size = _coerce_int(design_config.get("panel_size", 96), 96)
    backup_gene_count = _coerce_int(design_config.get("backup_gene_count", max(15, int(panel_size * 0.2))), max(15, int(panel_size * 0.2)))
    excluded_genes = design_config.get("excluded_genes", [])
    if not isinstance(excluded_genes, list):
        excluded_genes = []
    promote_genes = design_config.get("promote_genes", [])
    if not isinstance(promote_genes, list):
        promote_genes = []

    adata_ref, batch_col, annotation_col = load_reference_with_labels(
        assets["raw_scrna_h5ad_path"],
        assets["labels_csv_path"],
    )
    panel_df, backup_df, summary = build_panel_design(
        adata_ref,
        label_col="cell_label",
        batch_col=batch_col,
        panel_size=panel_size,
        backup_gene_count=backup_gene_count,
        excluded_genes=excluded_genes,
        preferred_genes=promote_genes,
        min_cells_per_label=_coerce_int(design_config.get("min_cells_per_label", 25), 25),
    )

    marker_scores_path = Path(write_table(panel_df, output_dir / "candidate_markers.csv"))
    panel_path = Path(
        write_table(
            panel_df[
                [
                    "gene",
                    "target_label",
                    "design_score",
                    "batch_support",
                    "probe_feasibility_score",
                    "probe_failure_reason",
                    "probe_feasibility_pass",
                ]
            ],
            output_dir / "panel_v1.csv",
        )
    )
    backup_path = Path(
        write_table(
            backup_df[
                [
                    "gene",
                    "target_label",
                    "design_score",
                    "batch_support",
                    "probe_feasibility_score",
                    "probe_failure_reason",
                    "probe_feasibility_pass",
                ]
            ],
            output_dir / "backup_genes.csv",
        )
    )
    probe_feasibility_path = Path(
        write_table(
            panel_df[
                [
                    "gene",
                    "target_label",
                    "probe_feasibility_score",
                    "probe_failure_reason",
                    "probe_feasibility_pass",
                    "batch_support",
                    "mean_in_label",
                    "specificity_score",
                ]
            ],
            output_dir / "probe_filter_report.csv",
        )
    )
    final_probes = panel_df[panel_df["probe_feasibility_pass"]].copy()
    if final_probes.empty:
        final_probes = panel_df.head(min(max(8, panel_size // 4), len(panel_df))).copy()
    final_probes_path = Path(
        write_table(
            final_probes[
                [
                    "gene",
                    "target_label",
                    "probe_feasibility_score",
                    "batch_support",
                    "design_score",
                ]
            ],
            output_dir / "final_probes.csv",
        )
    )
    score_figure_path = output_dir / "panel_marker_scores.png"
    _render_score_figure(panel_df, score_figure_path)

    design_summary = {
        "selected_repo": selected_repo,
        "dataset_family": assets["dataset_family"],
        "reference_raw_path": assets["raw_scrna_h5ad_path"],
        "labels_csv_path": assets["labels_csv_path"],
        "annotation_column": annotation_col,
        "batch_column": batch_col,
        "panel_size_requested": panel_size,
        "backup_gene_count_requested": backup_gene_count,
        "promote_gene_count_requested": len(promote_genes),
        **summary,
        "probe_failure_count": int((~panel_df["probe_feasibility_pass"]).sum()),
        "probe_pass_count": int(panel_df["probe_feasibility_pass"].sum()),
        "panel_preview": panel_df.head(10).to_dict(orient="records"),
        "backup_preview": backup_df.head(10).to_dict(orient="records"),
    }
    summary_path = Path(write_json(design_summary, output_dir / "panel_design_summary.json"))
    report_path = output_dir / "panel_design_report.md"
    report_path.write_text(
        "\n".join(
            [
                "# Closed-loop Spatial Panel Design",
                "",
                f"- Selected repo/tool family: `{selected_repo}`",
                f"- Dataset family: `{assets['dataset_family']}`",
                f"- Panel size requested: `{panel_size}`",
                f"- Panel size actual: `{summary['panel_size_actual']}`",
                f"- Backup genes: `{summary['backup_gene_count']}`",
                f"- Preferred replacement genes injected: `{len(promote_genes)}`",
                f"- Annotation column: `{annotation_col}`",
                f"- Batch column: `{batch_col}`",
                f"- Probe-feasible genes: `{int(panel_df['probe_feasibility_pass'].sum())}`",
                "",
                "## Outputs",
                f"- candidate markers: `{marker_scores_path}`",
                f"- panel: `{panel_path}`",
                f"- backups: `{backup_path}`",
                f"- probe filter report: `{probe_feasibility_path}`",
                f"- final probes: `{final_probes_path}`",
                f"- score figure: `{score_figure_path}`",
                f"- summary: `{summary_path}`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    outputs = {
        "selected_repo": selected_repo,
        "panel_path": str(panel_path),
        "backup_panel_path": str(backup_path),
        "candidate_marker_path": str(marker_scores_path),
        "probe_feasibility_path": str(probe_feasibility_path),
        "final_probes_path": str(final_probes_path),
        "marker_score_figure_path": str(score_figure_path),
        "design_summary_path": str(summary_path),
        "design_report_path": str(report_path),
        "panel_genes": panel_df["gene"].astype(str).tolist(),
        "backup_genes": backup_df["gene"].astype(str).tolist(),
        "probe_failed_genes": panel_df.loc[~panel_df["probe_feasibility_pass"], "gene"].astype(str).tolist(),
        "summary": "Closed-loop panel design completed.",
    }
    artifacts = [
        {"path": str(panel_path), "mime": "text/csv"},
        {"path": str(backup_path), "mime": "text/csv"},
        {"path": str(marker_scores_path), "mime": "text/csv"},
        {"path": str(probe_feasibility_path), "mime": "text/csv"},
        {"path": str(final_probes_path), "mime": "text/csv"},
        {"path": str(score_figure_path), "mime": "image/png"},
        {"path": str(summary_path), "mime": "application/json"},
        {"path": str(report_path), "mime": "text/markdown"},
    ]
    trace = {
        "closed_loop_design": True,
        "panel_size_requested": panel_size,
        "selected_repo": selected_repo,
    }
    write_job_result(
        output_json=args.output_json,
        outputs=outputs,
        summary={
            "design_summary_path": str(summary_path),
            "panel_path": str(panel_path),
            "backup_panel_path": str(backup_path),
            "probe_feasibility_path": str(probe_feasibility_path),
            "final_probes_path": str(final_probes_path),
            "marker_score_figure_path": str(score_figure_path),
        },
        artifacts=artifacts,
        trace=trace,
        confidence=0.88,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
