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
    args = parser.parse_args(argv)

    figure_path = Path(args.figure).resolve()
    summary_path = Path(args.summary).resolve()
    reference_summary_path = Path(args.reference_summary).resolve()

    if not figure_path.exists():
        raise SystemExit("missing figure output")
    if not summary_path.exists():
        raise SystemExit("missing summary output")
    if not reference_summary_path.exists():
        raise SystemExit("missing reference summary")

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
