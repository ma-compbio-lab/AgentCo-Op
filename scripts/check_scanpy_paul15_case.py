from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Scanpy Paul15 trajectory case-study outputs.")
    parser.add_argument("--figure", required=True)
    parser.add_argument("--paga-figure", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--reference-summary", required=True)
    args = parser.parse_args(argv)

    figure_path = Path(args.figure).resolve()
    paga_figure_path = Path(args.paga_figure).resolve()
    summary_path = Path(args.summary).resolve()
    reference_summary_path = Path(args.reference_summary).resolve()

    if not figure_path.exists():
        raise SystemExit("missing trajectory figure output")
    if not paga_figure_path.exists():
        raise SystemExit("missing paga figure output")
    if not summary_path.exists():
        raise SystemExit("missing summary output")
    if not reference_summary_path.exists():
        raise SystemExit("missing reference summary")

    main_image = Image.open(figure_path)
    paga_image = Image.open(paga_figure_path)
    if main_image.size[0] < 500 or main_image.size[1] < 300:
        raise SystemExit("trajectory figure dimensions too small")
    if paga_image.size[0] < 300 or paga_image.size[1] < 300:
        raise SystemExit("paga figure dimensions too small")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    reference_summary = json.loads(reference_summary_path.read_text(encoding="utf-8"))
    required_keys = {
        "cluster_count",
        "cluster_sizes",
        "figure_width",
        "figure_height",
        "paga_figure_width",
        "paga_figure_height",
        "n_obs",
        "n_vars",
        "pseudotime_range",
        "paga_edge_count",
        "root_strategy",
        "root_cluster",
        "qc_summary",
        "marker_gene_summary",
        "dynamic_gene_summary",
    }
    missing = sorted(required_keys - set(summary))
    if missing:
        raise SystemExit(f"summary missing keys: {missing}")

    if int(summary["cluster_count"]) < 4:
        raise SystemExit("cluster_count too small for trajectory case")
    if int(summary["n_obs"]) < 2000:
        raise SystemExit("n_obs unexpectedly small")
    pseudotime_range = summary["pseudotime_range"]
    if not isinstance(pseudotime_range, list) or len(pseudotime_range) != 2:
        raise SystemExit("invalid pseudotime_range")
    if float(pseudotime_range[1]) - float(pseudotime_range[0]) <= 0.1:
        raise SystemExit("pseudotime_range too narrow")
    if int(summary["paga_edge_count"]) < 3:
        raise SystemExit("paga_edge_count too small")
    if not isinstance(summary["qc_summary"], dict) or "total_counts_mean" not in summary["qc_summary"]:
        raise SystemExit("qc_summary missing expected fields")
    if not isinstance(summary["marker_gene_summary"], dict) or not summary["marker_gene_summary"]:
        raise SystemExit("marker_gene_summary missing or empty")
    first_marker_list = next(iter(summary["marker_gene_summary"].values()))
    if not isinstance(first_marker_list, list) or not first_marker_list:
        raise SystemExit("marker_gene_summary entries are empty")
    dynamic_gene_summary = summary["dynamic_gene_summary"]
    if not isinstance(dynamic_gene_summary, dict):
        raise SystemExit("dynamic_gene_summary missing")
    if not dynamic_gene_summary.get("positive") or not dynamic_gene_summary.get("negative"):
        raise SystemExit("dynamic_gene_summary missing positive or negative genes")
    if abs(int(summary["cluster_count"]) - int(reference_summary["cluster_count"])) > 4:
        raise SystemExit("cluster_count diverges too far from reference")
    if abs(int(summary["paga_edge_count"]) - int(reference_summary["paga_edge_count"])) > 10:
        raise SystemExit("paga_edge_count diverges too far from reference")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
