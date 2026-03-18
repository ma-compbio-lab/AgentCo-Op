from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Mapping

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from dynaforge.case_studies.job_utils import ensure_output_dir, load_job_request, write_job_result
from dynaforge.case_studies.spatial_panel_common import (
    load_visium_slide,
    resolve_mouse_brain_assets,
    validate_panel_on_slide,
    write_json,
    write_table,
)


def _render_gene_coverage(validation: Mapping[str, Any], output_path: Path) -> None:
    coverage = pd.DataFrame(validation.get("gene_detection", []))
    if coverage.empty:
        coverage = pd.DataFrame({"gene": ["none"], "spot_detection_rate": [0.0]})
    preview = coverage.sort_values("spot_detection_rate", ascending=False).head(24)
    fig, ax = plt.subplots(figsize=(11, 6))
    ax.barh(preview["gene"][::-1], preview["spot_detection_rate"][::-1], color="#55A630")
    ax.set_title("Panel Gene Coverage on Held-out Spatial Slide")
    ax.set_xlabel("spot detection rate")
    fig.tight_layout()
    fig.savefig(output_path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def _render_manual_layer_heatmap(analysis_adata: Any, output_path: Path) -> None:
    if "manual_layer" not in analysis_adata.obs or "panel_cluster" not in analysis_adata.obs:
        fig, ax = plt.subplots(figsize=(4, 3))
        ax.text(0.5, 0.5, "manual labels unavailable", ha="center", va="center")
        ax.axis("off")
        fig.tight_layout()
        fig.savefig(output_path, dpi=160, bbox_inches="tight")
        plt.close(fig)
        return

    valid = analysis_adata.obs["manual_layer"].fillna("").astype(str) != ""
    if int(valid.sum()) == 0:
        fig, ax = plt.subplots(figsize=(4, 3))
        ax.text(0.5, 0.5, "no labeled spots", ha="center", va="center")
        ax.axis("off")
        fig.tight_layout()
        fig.savefig(output_path, dpi=160, bbox_inches="tight")
        plt.close(fig)
        return

    table = pd.crosstab(
        analysis_adata.obs.loc[valid, "panel_cluster"].astype(str),
        analysis_adata.obs.loc[valid, "manual_layer"].astype(str),
    )
    fig, ax = plt.subplots(figsize=(8, 5))
    image = ax.imshow(table.values, cmap="Blues", aspect="auto")
    ax.set_title("Panel Cluster vs Manual Layer Agreement")
    ax.set_xlabel("manual layer")
    ax.set_ylabel("panel cluster")
    ax.set_xticks(range(table.shape[1]))
    ax.set_xticklabels(table.columns.tolist(), rotation=45, ha="right")
    ax.set_yticks(range(table.shape[0]))
    ax.set_yticklabels(table.index.tolist())
    fig.colorbar(image, ax=ax, fraction=0.046, pad=0.04, label="spot count")
    fig.tight_layout()
    fig.savefig(output_path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Closed-loop spatial panel validation job.")
    parser.add_argument("--input-json", required=True)
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args(argv)

    _, inputs, _, hints = load_job_request(args.input_json)
    output_dir = ensure_output_dir(hints, args.output_json)
    assets = resolve_mouse_brain_assets(hints)
    design_result = dict(inputs.get("design_result", {})) if isinstance(inputs.get("design_result", {}), Mapping) else {}
    validation_plan = dict(inputs.get("validation_plan", {})) if isinstance(inputs.get("validation_plan", {}), Mapping) else {}

    holdout_slide = str(validation_plan.get("holdout_slide", hints.get("input_assets", {}).get("holdout_slide", "ST8059050"))).strip() or "ST8059050"
    slide_dir = Path(assets["spatial_root"]) / "rawdata" / holdout_slide
    manual_layers = dict(assets.get("manual_layer_paths", {}))
    manual_layer_path = manual_layers.get(holdout_slide, "")
    adata = load_visium_slide(slide_dir, sample_id=holdout_slide, manual_label_path=manual_layer_path or None)

    panel_genes = design_result.get("panel_genes", [])
    backup_genes = design_result.get("backup_genes", [])
    if not isinstance(panel_genes, list) or not panel_genes:
        raise RuntimeError("validation job requires design_result.panel_genes")
    if not isinstance(backup_genes, list):
        backup_genes = []

    validation = validate_panel_on_slide(
        adata,
        panel_genes=panel_genes,
        backup_genes=backup_genes,
        resolution=float(validation_plan.get("resolution", 0.65)),
        seed=int(validation_plan.get("seed", 0)),
    )
    analysis_adata = validation["analysis_adata"]
    min_validation_score = float(validation_plan.get("min_validation_score", 0.22))
    max_failed_genes = int(validation_plan.get("max_failed_genes", max(6, int(0.12 * len(panel_genes)))))
    needs_replan = bool(
        validation["validation_score"] < min_validation_score
        or len(validation["failed_genes"]) > max_failed_genes
        or validation["usable_panel_size"] < max(12, int(0.75 * len(panel_genes)))
    )

    coverage_path = output_dir / "panel_gene_coverage.png"
    _render_gene_coverage(validation, coverage_path)
    spatial_cluster_path = output_dir / "panel_spatial_clusters.png"
    import scanpy as sc

    if "spatial" in analysis_adata.obsm and "panel_cluster" in analysis_adata.obs:
        sc.pl.spatial(analysis_adata, color="panel_cluster", spot_size=1.0, show=False)
        fig = plt.gcf()
        fig.savefig(spatial_cluster_path, dpi=160, bbox_inches="tight")
        plt.close(fig)
    else:
        fig, ax = plt.subplots(figsize=(4, 3))
        ax.text(0.5, 0.5, "spatial clusters unavailable", ha="center", va="center")
        ax.axis("off")
        fig.tight_layout()
        fig.savefig(spatial_cluster_path, dpi=160, bbox_inches="tight")
        plt.close(fig)
    agreement_path = output_dir / "panel_manual_layer_agreement.png"
    _render_manual_layer_heatmap(analysis_adata, agreement_path)

    detection_table_path = Path(write_table(pd.DataFrame(validation["gene_detection"]), output_dir / "panel_gene_detection.csv"))
    validation_summary = {
        "holdout_slide": holdout_slide,
        "selected_repo": str(design_result.get("selected_repo", "scanpy")),
        "validation_score": float(validation["validation_score"]),
        "ari": float(validation["ari"]),
        "nmi": float(validation["nmi"]),
        "failed_genes": list(validation["failed_genes"]),
        "replacement_candidates": list(validation["replacement_candidates"]),
        "usable_panel_size": int(validation["usable_panel_size"]),
        "requested_panel_size": int(validation["requested_panel_size"]),
        "labeled_spot_count": int(validation["labeled_spot_count"]),
        "needs_replan": needs_replan,
        "thresholds": {
            "min_validation_score": min_validation_score,
            "max_failed_genes": max_failed_genes,
        },
    }
    summary_path = Path(write_json(validation_summary, output_dir / "panel_validation_summary.json"))
    report_path = output_dir / "panel_validation_report.md"
    report_path.write_text(
        "\n".join(
            [
                "# Closed-loop Spatial Panel Validation",
                "",
                f"- Hold-out slide: `{holdout_slide}`",
                f"- Validation score: `{validation_summary['validation_score']:.4f}`",
                f"- ARI: `{validation_summary['ari']:.4f}`",
                f"- NMI: `{validation_summary['nmi']:.4f}`",
                f"- Failed genes: `{len(validation_summary['failed_genes'])}`",
                f"- Needs replan: `{needs_replan}`",
                "",
                "## Replacement candidates",
                f"`{validation_summary['replacement_candidates']}`",
                "",
                "## Artifacts",
                f"- gene coverage: `{coverage_path}`",
                f"- spatial clusters: `{spatial_cluster_path}`",
                f"- agreement heatmap: `{agreement_path}`",
                f"- detection table: `{detection_table_path}`",
                f"- summary: `{summary_path}`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    outputs = {
        "selected_repo": str(design_result.get("selected_repo", "scanpy")),
        "panel_validation_path": str(summary_path),
        "panel_validation_report_path": str(report_path),
        "panel_cluster_figure_path": str(spatial_cluster_path),
        "coverage_figure_path": str(coverage_path),
        "agreement_figure_path": str(agreement_path),
        "needs_replan": needs_replan,
        "failed_genes": list(validation["failed_genes"]),
        "replacement_candidates": list(validation["replacement_candidates"]),
        "validation_score": float(validation["validation_score"]),
        "ari": float(validation["ari"]),
        "nmi": float(validation["nmi"]),
        "summary": "Closed-loop panel validation completed.",
    }
    artifacts = [
        {"path": str(coverage_path), "mime": "image/png"},
        {"path": str(spatial_cluster_path), "mime": "image/png"},
        {"path": str(agreement_path), "mime": "image/png"},
        {"path": str(detection_table_path), "mime": "text/csv"},
        {"path": str(summary_path), "mime": "application/json"},
        {"path": str(report_path), "mime": "text/markdown"},
    ]
    trace = {
        "closed_loop_validation": True,
        "holdout_slide": holdout_slide,
        "needs_replan": needs_replan,
        "validation_score": float(validation["validation_score"]),
    }
    write_job_result(
        output_json=args.output_json,
        outputs=outputs,
        summary={
            "panel_validation_path": str(summary_path),
            "panel_cluster_figure_path": str(spatial_cluster_path),
            "coverage_figure_path": str(coverage_path),
            "agreement_figure_path": str(agreement_path),
        },
        artifacts=artifacts,
        trace=trace,
        confidence=min(0.94, max(0.45, 0.55 + float(validation["validation_score"]))),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
