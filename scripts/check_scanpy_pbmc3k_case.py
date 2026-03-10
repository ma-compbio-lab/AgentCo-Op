from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Scanpy PBMC3k case-study outputs.")
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
        "umap_x_range",
        "umap_y_range",
    }
    missing = sorted(required_keys - set(summary))
    if missing:
        raise SystemExit(f"summary missing keys: {missing}")

    if int(summary["cluster_count"]) < 2:
        raise SystemExit("cluster_count too small")
    if int(summary["n_obs"]) < 1000:
        raise SystemExit("n_obs unexpectedly small")
    if abs(int(summary["cluster_count"]) - int(reference_summary["cluster_count"])) > 3:
        raise SystemExit("cluster_count diverges too far from reference")
    if abs(int(summary["n_obs"]) - int(reference_summary["n_obs"])) > 50:
        raise SystemExit("n_obs diverges too far from reference")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
