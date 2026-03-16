from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Scanpy Paul15 paper-figure reproduction outputs.")
    parser.add_argument("--figure", required=True)
    parser.add_argument("--paga-figure", required=True)
    parser.add_argument("--gene-trend-figure", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--reference-summary", required=True)
    args = parser.parse_args(argv)

    figure_path = Path(args.figure).resolve()
    paga_figure_path = Path(args.paga_figure).resolve()
    gene_trend_figure_path = Path(args.gene_trend_figure).resolve()
    summary_path = Path(args.summary).resolve()
    reference_summary_path = Path(args.reference_summary).resolve()

    for path, label in (
        (figure_path, "trajectory figure"),
        (paga_figure_path, "paga figure"),
        (gene_trend_figure_path, "gene-trend figure"),
        (summary_path, "summary"),
        (reference_summary_path, "reference summary"),
    ):
        if not path.exists():
            raise SystemExit(f"missing {label} output")

    main_image = Image.open(figure_path)
    paga_image = Image.open(paga_figure_path)
    gene_image = Image.open(gene_trend_figure_path)
    if main_image.size[0] < 500 or main_image.size[1] < 300:
        raise SystemExit("trajectory figure dimensions too small")
    if paga_image.size[0] < 300 or paga_image.size[1] < 300:
        raise SystemExit("paga figure dimensions too small")
    if gene_image.size[0] < 600 or gene_image.size[1] < 300:
        raise SystemExit("gene-trend figure dimensions too small")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    reference_summary = json.loads(reference_summary_path.read_text(encoding="utf-8"))
    required_keys = {
        "cluster_count",
        "pseudotime_range",
        "paga_edge_count",
        "gene_trend_figure_path",
        "dynamic_gene_panel_table_path",
        "gene_trend_figure_width",
        "gene_trend_figure_height",
        "dynamic_gene_panel_genes",
        "dynamic_gene_panel_summary",
        "dynamic_gene_summary",
        "marker_gene_summary",
        "preprocessing_summary",
        "root_provenance",
    }
    missing = sorted(required_keys - set(summary))
    if missing:
        raise SystemExit(f"summary missing keys: {missing}")
    if int(summary["cluster_count"]) < 4:
        raise SystemExit("cluster_count too small")
    if int(summary["paga_edge_count"]) < 3:
        raise SystemExit("paga_edge_count too small")
    panel_genes = summary["dynamic_gene_panel_genes"]
    if not isinstance(panel_genes, list) or len(panel_genes) < 2:
        raise SystemExit("dynamic_gene_panel_genes missing or too short")
    table_path = Path(str(summary["dynamic_gene_panel_table_path"])).resolve()
    if not table_path.exists():
        raise SystemExit("dynamic_gene_panel_table_path missing on disk")
    panel_summary = summary["dynamic_gene_panel_summary"]
    if not isinstance(panel_summary, dict) or not panel_summary.get("plotted_genes"):
        raise SystemExit("dynamic_gene_panel_summary missing plotted gene metadata")
    dynamic_summary = summary["dynamic_gene_summary"]
    if not isinstance(dynamic_summary, dict) or not dynamic_summary.get("positive") or not dynamic_summary.get("negative"):
        raise SystemExit("dynamic_gene_summary missing expected positive/negative sections")
    root_provenance = summary["root_provenance"]
    if not isinstance(root_provenance, dict) or not root_provenance.get("strategy") or "root_cluster" not in root_provenance:
        raise SystemExit("root_provenance missing expected metadata")
    if abs(int(summary["cluster_count"]) - int(reference_summary["cluster_count"])) > 4:
        raise SystemExit("cluster_count diverges too far from reference")
    if abs(int(summary["paga_edge_count"]) - int(reference_summary["paga_edge_count"])) > 10:
        raise SystemExit("paga_edge_count diverges too far from reference")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
