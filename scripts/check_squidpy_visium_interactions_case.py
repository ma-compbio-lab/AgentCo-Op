from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image


def _pair_signature(items: list[dict[str, object]], *, top_n: int) -> list[tuple[str, str]]:
    signatures: list[tuple[str, str]] = []
    for item in items[:top_n]:
        signatures.append((str(item.get("source", "")), str(item.get("target", ""))))
    return signatures


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    raise ValueError(f"unsupported boolean value: {value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Squidpy spatial interaction-case outputs.")
    parser.add_argument("--figure", required=True)
    parser.add_argument("--interaction-figure", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--reference-summary", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--expect-spatial-image", default="true")
    parser.add_argument("--expected-cluster-key", default="")
    args = parser.parse_args(argv)

    figure_path = Path(args.figure).resolve()
    interaction_figure_path = Path(args.interaction_figure).resolve()
    summary_path = Path(args.summary).resolve()
    reference_summary_path = Path(args.reference_summary).resolve()

    if not figure_path.exists():
        raise SystemExit("missing figure output")
    if not interaction_figure_path.exists():
        raise SystemExit("missing interaction figure output")
    if not summary_path.exists():
        raise SystemExit("missing summary output")
    if not reference_summary_path.exists():
        raise SystemExit("missing reference summary")

    image = Image.open(figure_path)
    if image.size[0] < 300 or image.size[1] < 300:
        raise SystemExit("figure dimensions too small")
    interaction_image = Image.open(interaction_figure_path)
    if interaction_image.size[0] < 300 or interaction_image.size[1] < 300:
        raise SystemExit("interaction figure dimensions too small")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    reference_summary = json.loads(reference_summary_path.read_text(encoding="utf-8"))
    dataset_id = str(args.dataset_id).strip().lower()
    expect_spatial_image = _parse_bool(str(args.expect_spatial_image))
    required_keys = {
        "dataset_id",
        "dataset_label",
        "cluster_key",
        "cluster_count",
        "cluster_sizes",
        "figure_width",
        "figure_height",
        "interaction_figure_width",
        "interaction_figure_height",
        "n_obs",
        "n_vars",
        "has_spatial_image",
        "spatial_connectivities_nnz",
        "nhood_enrichment_shape",
        "nhood_counts_total",
        "nhood_zscore_abs_mean",
        "top_enriched_pairs",
        "top_depleted_pairs",
    }
    missing = sorted(required_keys - set(summary))
    if missing:
        raise SystemExit(f"summary missing keys: {missing}")

    if str(summary["dataset_id"]).strip().lower() != dataset_id:
        raise SystemExit("summary dataset_id does not match requested dataset")
    if bool(summary["has_spatial_image"]) != expect_spatial_image:
        raise SystemExit("has_spatial_image does not match expected dataset behavior")
    expected_cluster_key = str(args.expected_cluster_key).strip()
    if expected_cluster_key and str(summary.get("cluster_key", "")).strip() != expected_cluster_key:
        raise SystemExit("cluster_key does not match expected value")
    if int(summary["cluster_count"]) < 2:
        raise SystemExit("cluster_count too small")
    if int(summary["spatial_connectivities_nnz"]) <= 0:
        raise SystemExit("spatial neighbor graph missing")
    if list(summary["nhood_enrichment_shape"]) != list(reference_summary["nhood_enrichment_shape"]):
        raise SystemExit("nhood_enrichment_shape mismatch")
    if abs(int(summary["cluster_count"]) - int(reference_summary["cluster_count"])) > 0:
        raise SystemExit("cluster_count diverges from reference")
    if abs(int(summary["n_obs"]) - int(reference_summary["n_obs"])) > 0:
        raise SystemExit("n_obs diverges from reference")
    if abs(int(summary["nhood_counts_total"]) - int(reference_summary["nhood_counts_total"])) > 20:
        raise SystemExit("nhood_counts_total diverges too far from reference")
    if abs(float(summary["nhood_zscore_abs_mean"]) - float(reference_summary["nhood_zscore_abs_mean"])) > 0.25:
        raise SystemExit("nhood_zscore_abs_mean diverges too far from reference")
    if _pair_signature(summary["top_enriched_pairs"], top_n=3) != _pair_signature(reference_summary["top_enriched_pairs"], top_n=3):
        raise SystemExit("top enriched interaction pairs diverge from reference")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
