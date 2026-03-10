from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path
from typing import Any, Mapping


DEFAULT_ANALYSIS_CONFIG: dict[str, Any] = {
    "min_genes": 200,
    "min_cells": 3,
    "target_sum": 10000.0,
    "n_top_genes": 2000,
    "n_neighbors": 10,
    "n_pcs": 40,
    "resolution": 0.5,
    "max_scale_value": 10.0,
    "seed": 0,
    "color_by": "leiden",
    "dpi": 150,
}


def run_pipeline(
    *,
    output_dir: str | Path,
    figure_path: str | Path,
    summary_path: str | Path,
    raw_data_path: str | Path | None = None,
    selected_repo: str = "scanpy",
    config: Mapping[str, Any] | None = None,
    figure_title: str = "PBMC3k UMAP",
) -> dict[str, Any]:
    import scanpy as sc
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

    sc.settings.autoshow = False
    sc.settings.verbosity = 0
    adata_raw = sc.datasets.pbmc3k()
    if raw_data is not None:
        adata_raw.write_h5ad(str(raw_data))
    adata = adata_raw.copy()

    sc.pp.filter_cells(adata, min_genes=int(cfg["min_genes"]))
    sc.pp.filter_genes(adata, min_cells=int(cfg["min_cells"]))
    adata.var["mt"] = adata.var_names.str.upper().str.startswith("MT-")
    sc.pp.calculate_qc_metrics(adata, qc_vars=["mt"], inplace=True)
    sc.pp.normalize_total(adata, target_sum=float(cfg["target_sum"]))
    sc.pp.log1p(adata)
    sc.pp.highly_variable_genes(adata, n_top_genes=int(cfg["n_top_genes"]), subset=True)
    sc.pp.scale(adata, max_value=float(cfg["max_scale_value"]))
    sc.tl.pca(adata, svd_solver="arpack")
    n_pcs = min(int(cfg["n_pcs"]), int(adata.obsm["X_pca"].shape[1]))
    sc.pp.neighbors(adata, n_neighbors=int(cfg["n_neighbors"]), n_pcs=n_pcs)
    sc.tl.umap(adata, random_state=int(cfg["seed"]))
    color_by = str(cfg["color_by"])
    sc.tl.leiden(
        adata,
        resolution=float(cfg["resolution"]),
        random_state=int(cfg["seed"]),
        key_added=color_by,
        flavor="igraph",
        n_iterations=2,
        directed=False,
    )

    figure = sc.pl.umap(adata, color=[color_by], return_fig=True, show=False, title=figure_title)
    figure.savefig(figure_path, dpi=int(cfg["dpi"]), bbox_inches="tight")
    image = Image.open(figure_path)
    cluster_sizes = {
        str(label): int(count)
        for label, count in adata.obs[color_by].value_counts().sort_index().items()
    }
    summary = {
        "selected_repo": selected_repo,
        "scanpy_version": importlib.metadata.version("scanpy"),
        "output_dir": str(output_dir),
        "figure_path": str(figure_path),
        "summary_path": str(summary_path),
        "raw_data_path": str(raw_data) if raw_data is not None else "",
        "n_obs": int(adata.n_obs),
        "n_vars": int(adata.n_vars),
        "highly_variable_gene_count": int(adata.n_vars),
        "cluster_key": color_by,
        "cluster_count": int(adata.obs[color_by].nunique()),
        "cluster_sizes": cluster_sizes,
        "umap_x_range": [
            float(adata.obsm["X_umap"][:, 0].min()),
            float(adata.obsm["X_umap"][:, 0].max()),
        ],
        "umap_y_range": [
            float(adata.obsm["X_umap"][:, 1].min()),
            float(adata.obsm["X_umap"][:, 1].max()),
        ],
        "figure_width": int(image.size[0]),
        "figure_height": int(image.size[1]),
        "analysis_config": cfg,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the PBMC3k Scanpy UMAP case-study pipeline.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--figure-path", required=True)
    parser.add_argument("--summary-path", required=True)
    parser.add_argument("--raw-data-path", default="")
    parser.add_argument("--selected-repo", default="scanpy")
    parser.add_argument("--config-json", default="")
    parser.add_argument("--title", default="PBMC3k UMAP")
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
