from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from PIL import Image


def _expected_group_spec(dataset_id: str) -> tuple[str, set[str]]:
    normalized = dataset_id.strip().lower()
    if normalized == "moignard15":
        return "exp_groups", {"PS", "NP", "HF", "4SG", "4SFG"}
    if normalized == "krumsiek11":
        return "cell_type", {"progenitor", "Mo", "Ery", "Mk", "Neu"}
    return "", set()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Scanpy trajectory method-transfer outputs.")
    parser.add_argument("--figure", required=True)
    parser.add_argument("--paga-figure", required=True)
    parser.add_argument("--gene-trend-figure", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--reference-summary", required=True)
    parser.add_argument("--dataset-id", required=True)
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
    dataset_id = str(args.dataset_id).strip().lower()
    if str(summary.get("dataset_id", "")).strip().lower() != dataset_id:
        raise SystemExit("summary dataset_id does not match requested transfer dataset")

    required_keys = {
        "dataset_id",
        "dataset_label",
        "dataset_group_key",
        "dataset_group_count",
        "dataset_group_labels",
        "external_group_metrics",
        "cluster_count",
        "pseudotime_range",
        "paga_edge_count",
        "gene_trend_figure_path",
        "dynamic_gene_panel_table_path",
        "dynamic_gene_panel_genes",
        "dynamic_gene_summary",
        "marker_gene_summary",
        "root_provenance",
    }
    missing = sorted(required_keys - set(summary))
    if missing:
        raise SystemExit(f"summary missing keys: {missing}")

    if int(summary["cluster_count"]) < 3:
        raise SystemExit("cluster_count too small for transfer case")
    if int(summary["paga_edge_count"]) < 2:
        raise SystemExit("paga_edge_count too small for transfer case")
    panel_genes = summary["dynamic_gene_panel_genes"]
    if not isinstance(panel_genes, list) or len(panel_genes) < 2:
        raise SystemExit("dynamic_gene_panel_genes missing or too short")
    table_path = Path(str(summary["dynamic_gene_panel_table_path"])).resolve()
    if not table_path.exists():
        raise SystemExit("dynamic_gene_panel_table_path missing on disk")

    pseudotime_range = summary["pseudotime_range"]
    if not isinstance(pseudotime_range, list) or len(pseudotime_range) != 2:
        raise SystemExit("pseudotime_range malformed")
    if not math.isfinite(float(pseudotime_range[0])) or not math.isfinite(float(pseudotime_range[1])):
        raise SystemExit("pseudotime_range must be finite for transfer case")
    if float(pseudotime_range[1]) <= float(pseudotime_range[0]):
        raise SystemExit("pseudotime_range is not increasing")

    root_provenance = summary["root_provenance"]
    if not isinstance(root_provenance, dict) or not root_provenance.get("strategy"):
        raise SystemExit("root_provenance missing expected metadata")

    expected_group_key, expected_group_labels = _expected_group_spec(dataset_id)
    if expected_group_key and str(summary.get("dataset_group_key", "")).strip() != expected_group_key:
        raise SystemExit("dataset_group_key does not match expected external metadata field")
    if expected_group_labels:
        observed_labels = {str(item) for item in summary.get("dataset_group_labels", [])}
        if observed_labels != expected_group_labels:
            raise SystemExit("dataset_group_labels do not match expected external metadata labels")
        if int(summary.get("dataset_group_count", 0) or 0) != len(expected_group_labels):
            raise SystemExit("dataset_group_count does not match expected label count")

    if dataset_id == "moignard15":
        if str(root_provenance.get("strategy", "")).strip() != "dataset_default":
            raise SystemExit("moignard15 transfer should use dataset_default root strategy")
        if not bool(root_provenance.get("used_dataset_default", False)):
            raise SystemExit("moignard15 transfer did not record dataset-default root usage")
        external_metrics = summary.get("external_group_metrics", {})
        if not isinstance(external_metrics, dict) or "ari" not in external_metrics or "nmi" not in external_metrics:
            raise SystemExit("moignard15 transfer summary missing external group alignment metrics")

    if abs(int(summary["cluster_count"]) - int(reference_summary.get("cluster_count", summary["cluster_count"]))) > 6:
        raise SystemExit("cluster_count diverges too far from reference")
    if abs(int(summary["paga_edge_count"]) - int(reference_summary.get("paga_edge_count", summary["paga_edge_count"]))) > 12:
        raise SystemExit("paga_edge_count diverges too far from reference")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
