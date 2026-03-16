from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate spatial multi-agent collaboration outputs.")
    parser.add_argument("--cluster-figure", required=True)
    parser.add_argument("--marker-figure", required=True)
    parser.add_argument("--spatial-figure", required=True)
    parser.add_argument("--interaction-figure", required=True)
    parser.add_argument("--cluster-summary", required=True)
    parser.add_argument("--interaction-summary", required=True)
    parser.add_argument("--reference-summary", required=True)
    parser.add_argument("--dataset-id", default="")
    args = parser.parse_args(argv)

    required_paths = {
        "cluster figure": Path(args.cluster_figure).resolve(),
        "marker figure": Path(args.marker_figure).resolve(),
        "spatial figure": Path(args.spatial_figure).resolve(),
        "interaction figure": Path(args.interaction_figure).resolve(),
        "cluster summary": Path(args.cluster_summary).resolve(),
        "interaction summary": Path(args.interaction_summary).resolve(),
        "reference summary": Path(args.reference_summary).resolve(),
    }
    for label, path in required_paths.items():
        if not path.exists():
            raise SystemExit(f"missing {label} output")

    for figure_label in ("cluster figure", "marker figure", "spatial figure", "interaction figure"):
        image = Image.open(required_paths[figure_label])
        if image.size[0] < 300 or image.size[1] < 300:
            raise SystemExit(f"{figure_label} dimensions too small")

    cluster_summary = json.loads(required_paths["cluster summary"].read_text(encoding="utf-8"))
    interaction_summary = json.loads(required_paths["interaction summary"].read_text(encoding="utf-8"))
    reference_summary = json.loads(required_paths["reference summary"].read_text(encoding="utf-8"))
    expected_dataset_id = str(args.dataset_id).strip().lower()
    reference_cluster = reference_summary.get("cluster_summary", {})
    reference_interaction = reference_summary.get("interaction_summary", {})

    cluster_required = {
        "cluster_count",
        "cluster_sizes",
        "cluster_key",
        "marker_gene_summary",
        "marker_gene_count",
        "annotated_data_path",
    }
    interaction_required = {
        "cluster_count",
        "cluster_labels",
        "nhood_enrichment_shape",
        "top_enriched_pairs",
        "top_depleted_pairs",
        "input_h5ad_path",
    }
    missing_cluster = sorted(cluster_required - set(cluster_summary))
    if missing_cluster:
        raise SystemExit(f"cluster summary missing keys: {missing_cluster}")
    missing_interaction = sorted(interaction_required - set(interaction_summary))
    if missing_interaction:
        raise SystemExit(f"interaction summary missing keys: {missing_interaction}")
    if expected_dataset_id:
        if str(cluster_summary.get("dataset_id", "")).strip().lower() != expected_dataset_id:
            raise SystemExit("cluster summary dataset_id does not match requested dataset")
        if str(interaction_summary.get("dataset_id", "")).strip().lower() != expected_dataset_id:
            raise SystemExit("interaction summary dataset_id does not match requested dataset")

    if int(cluster_summary["cluster_count"]) < 3:
        raise SystemExit("cluster_count too small for collaboration case")
    if int(cluster_summary["marker_gene_count"]) < 4:
        raise SystemExit("marker_gene_count too small for collaboration case")
    if int(interaction_summary["cluster_count"]) != int(cluster_summary["cluster_count"]):
        raise SystemExit("interaction summary cluster_count does not match clustering summary")
    if abs(int(cluster_summary["cluster_count"]) - int(reference_cluster.get("cluster_count", 0))) > 3:
        raise SystemExit("cluster_count diverges too far from reference")
    if abs(float(interaction_summary.get("nhood_zscore_abs_mean", 0.0)) - float(reference_interaction.get("nhood_zscore_abs_mean", 0.0))) > 1.5:
        raise SystemExit("interaction summary diverges too far from reference")
    if not interaction_summary.get("top_enriched_pairs"):
        raise SystemExit("interaction summary missing enriched pairs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
