from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Squidpy Visium case-study outputs.")
    parser.add_argument("--figure", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--reference-summary", required=True)
    parser.add_argument("--interaction-figure", default="")
    parser.add_argument("--require-interactions", action="store_true")
    args = parser.parse_args(argv)

    figure_path = Path(args.figure).resolve()
    summary_path = Path(args.summary).resolve()
    reference_summary_path = Path(args.reference_summary).resolve()
    interaction_figure_path = Path(args.interaction_figure).resolve() if args.interaction_figure else None

    if not figure_path.exists():
        raise SystemExit("missing figure output")
    if not summary_path.exists():
        raise SystemExit("missing summary output")
    if not reference_summary_path.exists():
        raise SystemExit("missing reference summary")
    if args.require_interactions and interaction_figure_path is None:
        raise SystemExit("interaction figure path required when --require-interactions is set")
    if interaction_figure_path is not None and not interaction_figure_path.exists():
        raise SystemExit("missing interaction figure output")

    image = Image.open(figure_path)
    if image.size[0] < 300 or image.size[1] < 300:
        raise SystemExit("figure dimensions too small")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    reference_summary = json.loads(reference_summary_path.read_text(encoding="utf-8"))
    required_keys = {
        "cluster_count",
        "cluster_sizes",
        "figure_width",
        "figure_height",
        "n_obs",
        "n_vars",
        "spatial_x_range",
        "spatial_y_range",
        "has_spatial_image",
        "spatial_connectivities_nnz",
    }
    missing = sorted(required_keys - set(summary))
    if missing:
        raise SystemExit(f"summary missing keys: {missing}")

    if not summary["has_spatial_image"]:
        raise SystemExit("expected tissue image metadata to be present")
    if int(summary["cluster_count"]) < 2:
        raise SystemExit("cluster_count too small")
    if int(summary["spatial_connectivities_nnz"]) <= 0:
        raise SystemExit("spatial neighbor graph missing")
    if abs(int(summary["cluster_count"]) - int(reference_summary["cluster_count"])) > 2:
        raise SystemExit("cluster_count diverges too far from reference")
    if abs(int(summary["n_obs"]) - int(reference_summary["n_obs"])) > 20:
        raise SystemExit("n_obs diverges too far from reference")

    if args.require_interactions:
        interaction_keys = {"has_nhood_enrichment", "interaction_figure_path", "nhood_enrichment_summary"}
        missing_interaction = sorted(interaction_keys - set(summary))
        if missing_interaction:
            raise SystemExit(f"interaction summary missing keys: {missing_interaction}")
        if not summary["has_nhood_enrichment"]:
            raise SystemExit("expected neighborhood enrichment to be present")
        if interaction_figure_path is not None:
            interaction_image = Image.open(interaction_figure_path)
            if interaction_image.size[0] < 300 or interaction_image.size[1] < 300:
                raise SystemExit("interaction figure dimensions too small")

        nhood_summary = summary.get("nhood_enrichment_summary", {})
        reference_nhood = reference_summary.get("nhood_enrichment_summary", {})
        if not isinstance(nhood_summary, dict) or not isinstance(reference_nhood, dict):
            raise SystemExit("expected neighborhood-enrichment summaries in both generated and reference outputs")
        required_nhood_keys = {"shape", "max_abs_zscore", "top_same_cluster_pairs", "top_cross_cluster_pairs"}
        missing_nhood = sorted(required_nhood_keys - set(nhood_summary))
        if missing_nhood:
            raise SystemExit(f"nhood_enrichment_summary missing keys: {missing_nhood}")
        if list(nhood_summary["shape"]) != list(reference_nhood.get("shape", [])):
            raise SystemExit("nhood_enrichment shape diverges from reference")
        if float(nhood_summary["max_abs_zscore"]) <= 0:
            raise SystemExit("nhood_enrichment max_abs_zscore must be positive")

        generated_same = nhood_summary.get("top_same_cluster_pairs", [])
        reference_same = reference_nhood.get("top_same_cluster_pairs", [])
        generated_cross = nhood_summary.get("top_cross_cluster_pairs", [])
        reference_cross = reference_nhood.get("top_cross_cluster_pairs", [])
        if not generated_same or not reference_same:
            raise SystemExit("missing top_same_cluster_pairs")
        if not generated_cross or not reference_cross:
            raise SystemExit("missing top_cross_cluster_pairs")

        generated_same_sig = (generated_same[0].get("source"), generated_same[0].get("target"))
        reference_same_sig = (reference_same[0].get("source"), reference_same[0].get("target"))
        if generated_same_sig != reference_same_sig:
            raise SystemExit("top same-cluster enrichment pair diverges from reference")

        generated_cross_sig = (generated_cross[0].get("source"), generated_cross[0].get("target"))
        reference_cross_sig = (reference_cross[0].get("source"), reference_cross[0].get("target"))
        if generated_cross_sig != reference_cross_sig:
            raise SystemExit("top cross-cluster enrichment pair diverges from reference")

        ref_z = float(reference_nhood.get("max_abs_zscore", 0.0))
        gen_z = float(nhood_summary["max_abs_zscore"])
        if ref_z > 0 and abs(gen_z - ref_z) / ref_z > 0.35:
            raise SystemExit("nhood_enrichment max_abs_zscore diverges too far from reference")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
