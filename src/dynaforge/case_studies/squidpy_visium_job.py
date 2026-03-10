from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path
from typing import Any, Mapping


DEFAULT_ANALYSIS_CONFIG: dict[str, Any] = {
    "color_by": "cluster",
    "shape": "hex",
    "library_key": None,
    "img": True,
    "size": 1.0,
    "dpi": 150,
    "compute_spatial_neighbors": True,
}


def run_pipeline(
    *,
    output_dir: str | Path,
    figure_path: str | Path,
    summary_path: str | Path,
    raw_data_path: str | Path | None = None,
    selected_repo: str = "squidpy",
    config: Mapping[str, Any] | None = None,
    figure_title: str = "Visium H&E Spatial Plot",
) -> dict[str, Any]:
    import matplotlib.pyplot as plt
    import squidpy as sq
    from PIL import Image

    cfg = dict(DEFAULT_ANALYSIS_CONFIG)
    if config:
        cfg.update(dict(config))

    output_dir = Path(output_dir).resolve()
    figure_path = Path(figure_path).resolve()
    summary_path = Path(summary_path).resolve()
    raw_data = Path(raw_data_path).resolve() if raw_data_path else None
    output_dir.mkdir(parents=True, exist_ok=True)
    figure_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    if raw_data is not None:
        raw_data.parent.mkdir(parents=True, exist_ok=True)

    adata = sq.datasets.visium_hne_adata_crop()
    if raw_data is not None:
        adata.write_h5ad(str(raw_data))

    if bool(cfg.get("compute_spatial_neighbors", True)):
        sq.gr.spatial_neighbors(adata)

    color_by = str(cfg["color_by"])
    shape = str(cfg.get("shape", "hex"))
    library_key = cfg.get("library_key")
    library_key = str(library_key) if library_key else None
    axes = sq.pl.spatial_scatter(
        adata,
        color=color_by,
        shape=shape,
        library_key=library_key,
        img=bool(cfg.get("img", True)),
        size=float(cfg.get("size", 1.0)),
        title=figure_title,
        return_ax=True,
    )
    axis = axes[0] if isinstance(axes, (list, tuple)) else axes
    if axis is None:
        raise RuntimeError("squidpy.pl.spatial_scatter did not return matplotlib axes")
    figure = axis.figure
    figure.savefig(figure_path, dpi=int(cfg["dpi"]), bbox_inches="tight")
    plt.close(figure)

    image = Image.open(figure_path)
    cluster_sizes = {
        str(label): int(count)
        for label, count in adata.obs[color_by].value_counts().sort_index().items()
    }
    library_ids = []
    if library_key and library_key in adata.obs:
        library_ids = [str(value) for value in adata.obs[library_key].dropna().unique().tolist()]
    elif "spatial" in adata.uns:
        library_ids = sorted(str(value) for value in adata.uns["spatial"].keys())
    spatial = adata.obsm["spatial"]
    spatial_connectivities = adata.obsp.get("spatial_connectivities")
    summary = {
        "selected_repo": selected_repo,
        "squidpy_version": importlib.metadata.version("squidpy"),
        "output_dir": str(output_dir),
        "figure_path": str(figure_path),
        "summary_path": str(summary_path),
        "raw_data_path": str(raw_data) if raw_data is not None else "",
        "n_obs": int(adata.n_obs),
        "n_vars": int(adata.n_vars),
        "cluster_key": color_by,
        "cluster_count": int(adata.obs[color_by].nunique()),
        "cluster_sizes": cluster_sizes,
        "library_ids": library_ids,
        "has_spatial_image": bool(adata.uns.get("spatial")),
        "spatial_x_range": [float(spatial[:, 0].min()), float(spatial[:, 0].max())],
        "spatial_y_range": [float(spatial[:, 1].min()), float(spatial[:, 1].max())],
        "spatial_connectivities_nnz": int(getattr(spatial_connectivities, "nnz", 0)),
        "figure_width": int(image.size[0]),
        "figure_height": int(image.size[1]),
        "analysis_config": cfg,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Squidpy Visium spatial case-study pipeline.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--figure-path", required=True)
    parser.add_argument("--summary-path", required=True)
    parser.add_argument("--raw-data-path", default="")
    parser.add_argument("--selected-repo", default="squidpy")
    parser.add_argument("--config-json", default="")
    parser.add_argument("--title", default="Visium H&E Spatial Plot")
    args = parser.parse_args(argv)

    config = json.loads(args.config_json) if args.config_json else {}
    summary = run_pipeline(
        output_dir=args.output_dir,
        figure_path=args.figure_path,
        summary_path=args.summary_path,
        raw_data_path=args.raw_data_path or None,
        selected_repo=args.selected_repo,
        config=config,
        figure_title=args.title,
    )
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
