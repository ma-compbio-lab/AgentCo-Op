from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path
from typing import Any, Mapping


DEFAULT_ANALYSIS_CONFIG: dict[str, Any] = {
    "dataset_id": "visium_hne_adata_crop",
    "target_sum": 10000.0,
    "n_top_genes": 2000,
    "n_neighbors": 10,
    "n_pcs": 30,
    "resolution": 0.6,
    "max_scale_value": 10.0,
    "seed": 0,
    "cluster_key": "scanpy_leiden",
    "dpi": 150,
    "marker_top_n": 3,
    "marker_cluster_limit": 6,
}


def _load_dataset(squidpy_module: Any, dataset_id: str) -> tuple[Any, dict[str, Any]]:
    normalized = dataset_id.strip().lower() or "visium_hne_adata_crop"
    if normalized in {"visium", "visium_hne", "visium_hne_adata_crop"}:
        return squidpy_module.datasets.visium_hne_adata_crop(), {
            "dataset_id": "visium_hne_adata_crop",
            "dataset_label": "Visium H&E",
            "raw_filename": "visium_hne_raw.h5ad",
        }
    if normalized in {"seqfish", "seqfish_mouse_embryo"}:
        return squidpy_module.datasets.seqfish(), {
            "dataset_id": "seqfish",
            "dataset_label": "seqFISH",
            "raw_filename": "seqfish_raw.h5ad",
        }
    raise ValueError(f"Unsupported spatial dataset_id: {dataset_id}")


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def run_pipeline(
    *,
    output_dir: str | Path,
    cluster_figure_path: str | Path,
    marker_figure_path: str | Path,
    summary_path: str | Path,
    annotated_data_path: str | Path,
    raw_data_path: str | Path,
    selected_repo: str = "scanpy",
    config: Mapping[str, Any] | None = None,
    cluster_title: str = "Spatial Scanpy Clusters",
    marker_title: str = "Spatial Cluster Marker Heatmap",
) -> dict[str, Any]:
    import matplotlib.pyplot as plt
    import numpy as np
    import scanpy as sc
    from PIL import Image
    from scipy import sparse

    cfg = dict(DEFAULT_ANALYSIS_CONFIG)
    if config:
        cfg.update(dict(config))
    dataset_id = str(cfg.get("dataset_id", "visium_hne_adata_crop")).strip().lower() or "visium_hne_adata_crop"

    output_dir = Path(output_dir).resolve()
    cluster_figure_path = Path(cluster_figure_path).resolve()
    marker_figure_path = Path(marker_figure_path).resolve()
    summary_path = Path(summary_path).resolve()
    annotated_data_path = Path(annotated_data_path).resolve()
    raw_data_path = Path(raw_data_path).resolve()
    for path in (
        output_dir,
        cluster_figure_path.parent,
        marker_figure_path.parent,
        summary_path.parent,
        annotated_data_path.parent,
        raw_data_path.parent,
    ):
        path.mkdir(parents=True, exist_ok=True)

    sc.settings.autoshow = False
    sc.settings.verbosity = 0

    dataset_meta: dict[str, Any] = {}
    if not raw_data_path.exists():
        import squidpy as sq

        adata, dataset_meta = _load_dataset(sq, dataset_id)
        adata.write_h5ad(str(raw_data_path))

    adata = sc.read_h5ad(str(raw_data_path))
    if not dataset_meta:
        dataset_meta = {
            "dataset_id": dataset_id,
            "dataset_label": "seqFISH" if dataset_id == "seqfish" else "Visium H&E",
        }
    if sparse.issparse(adata.X):
        total_counts = np.asarray(adata.X.sum(axis=1)).ravel()
        detected_genes = np.asarray((adata.X > 0).sum(axis=1)).ravel()
    else:
        total_counts = np.asarray(adata.X.sum(axis=1)).ravel()
        detected_genes = np.asarray((adata.X > 0).sum(axis=1)).ravel()
    qc_summary = {
        "total_counts_mean": float(total_counts.mean()),
        "total_counts_range": [float(total_counts.min()), float(total_counts.max())],
        "detected_genes_mean": float(detected_genes.mean()),
        "detected_genes_range": [float(detected_genes.min()), float(detected_genes.max())],
    }

    sc.pp.normalize_total(adata, target_sum=float(cfg["target_sum"]))
    sc.pp.log1p(adata)
    sc.pp.highly_variable_genes(adata, n_top_genes=int(cfg["n_top_genes"]), subset=True)
    adata_log = adata.copy()
    sc.pp.scale(adata, max_value=float(cfg["max_scale_value"]))
    sc.tl.pca(adata, svd_solver="arpack")
    n_pcs = min(int(cfg["n_pcs"]), int(adata.obsm["X_pca"].shape[1]))
    sc.pp.neighbors(adata, n_neighbors=int(cfg["n_neighbors"]), n_pcs=n_pcs)
    sc.tl.umap(adata, random_state=int(cfg["seed"]))
    cluster_key = str(cfg["cluster_key"])
    sc.tl.leiden(
        adata,
        resolution=float(cfg["resolution"]),
        random_state=int(cfg["seed"]),
        key_added=cluster_key,
        flavor="igraph",
        n_iterations=2,
        directed=False,
    )

    adata_log.obs[cluster_key] = adata.obs[cluster_key].astype(str).tolist()
    adata_log.obs[cluster_key] = adata_log.obs[cluster_key].astype("category")
    sc.tl.rank_genes_groups(adata_log, groupby=cluster_key, method="wilcoxon")

    cluster_figure = sc.pl.umap(adata, color=[cluster_key], return_fig=True, show=False, title=cluster_title)
    cluster_figure.savefig(cluster_figure_path, dpi=int(cfg["dpi"]), bbox_inches="tight")
    plt.close(cluster_figure)

    top_n = max(1, int(cfg.get("marker_top_n", 3)))
    cluster_limit = max(1, int(cfg.get("marker_cluster_limit", 6)))
    cluster_order = [str(value) for value in adata_log.obs[cluster_key].cat.categories[:cluster_limit]]
    names_by_group = adata_log.uns.get("rank_genes_groups", {}).get("names")
    marker_gene_summary: dict[str, list[str]] = {}
    marker_genes: list[str] = []
    if names_by_group is not None:
        for cluster in cluster_order:
            genes = [str(gene) for gene in names_by_group[str(cluster)][:top_n] if str(gene).strip()]
            marker_gene_summary[str(cluster)] = genes
            marker_genes.extend(genes)
    marker_genes = _ordered_unique(marker_genes)

    expr = adata_log.X.toarray() if sparse.issparse(adata_log.X) else np.asarray(adata_log.X)
    expr = np.asarray(expr, dtype=float)
    gene_to_idx = {str(name): idx for idx, name in enumerate(adata_log.var_names)}
    marker_matrix = np.zeros((len(cluster_order), len(marker_genes)), dtype=float)
    for row_idx, cluster in enumerate(cluster_order):
        mask = (adata_log.obs[cluster_key].astype(str).to_numpy() == str(cluster))
        if not mask.any():
            continue
        cluster_expr = expr[mask]
        for col_idx, gene in enumerate(marker_genes):
            if gene not in gene_to_idx:
                continue
            marker_matrix[row_idx, col_idx] = float(cluster_expr[:, gene_to_idx[gene]].mean())

    fig, ax = plt.subplots(figsize=(max(6.0, 0.5 * max(len(marker_genes), 4)), max(4.0, 0.7 * max(len(cluster_order), 2))))
    im = ax.imshow(marker_matrix, aspect="auto", cmap="viridis")
    ax.set_title(marker_title)
    ax.set_xlabel("Marker genes")
    ax.set_ylabel("Clusters")
    ax.set_xticks(range(len(marker_genes)))
    ax.set_xticklabels(marker_genes, rotation=45, ha="right")
    ax.set_yticks(range(len(cluster_order)))
    ax.set_yticklabels(cluster_order)
    plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label="Mean log1p expression")
    fig.tight_layout()
    fig.savefig(marker_figure_path, dpi=int(cfg["dpi"]), bbox_inches="tight")
    plt.close(fig)

    adata.write_h5ad(str(annotated_data_path))

    cluster_image = Image.open(cluster_figure_path)
    marker_image = Image.open(marker_figure_path)
    cluster_sizes = {
        str(label): int(count)
        for label, count in adata.obs[cluster_key].value_counts().sort_index().items()
    }
    summary = {
        "dataset_id": str(dataset_meta.get("dataset_id", dataset_id)),
        "dataset_label": str(dataset_meta.get("dataset_label", dataset_id)),
        "selected_repo": selected_repo,
        "scanpy_version": importlib.metadata.version("scanpy"),
        "output_dir": str(output_dir),
        "cluster_figure_path": str(cluster_figure_path),
        "marker_figure_path": str(marker_figure_path),
        "summary_path": str(summary_path),
        "annotated_data_path": str(annotated_data_path),
        "raw_data_path": str(raw_data_path),
        "cluster_key": cluster_key,
        "n_obs": int(adata.n_obs),
        "n_vars": int(adata.n_vars),
        "cluster_count": int(adata.obs[cluster_key].nunique()),
        "cluster_sizes": cluster_sizes,
        "cluster_figure_width": int(cluster_image.size[0]),
        "cluster_figure_height": int(cluster_image.size[1]),
        "marker_figure_width": int(marker_image.size[0]),
        "marker_figure_height": int(marker_image.size[1]),
        "marker_cluster_order": cluster_order,
        "marker_gene_summary": marker_gene_summary,
        "marker_gene_union": marker_genes,
        "marker_gene_count": int(len(marker_genes)),
        "qc_summary": qc_summary,
        "preprocessing_summary": {
            "normalization_target_sum": float(cfg["target_sum"]),
            "highly_variable_genes_selected": int(cfg["n_top_genes"]),
            "neighbors_k": int(cfg["n_neighbors"]),
            "requested_n_pcs": int(cfg["n_pcs"]),
            "leiden_resolution": float(cfg["resolution"]),
        },
        "analysis_config": cfg,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Scanpy clustering specialist pipeline for a spatial dataset.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--cluster-figure-path", required=True)
    parser.add_argument("--marker-figure-path", required=True)
    parser.add_argument("--summary-path", required=True)
    parser.add_argument("--annotated-data-path", required=True)
    parser.add_argument("--raw-data-path", required=True)
    parser.add_argument("--selected-repo", default="scanpy")
    parser.add_argument("--config-json", default="")
    parser.add_argument("--cluster-title", default="Spatial Scanpy Clusters")
    parser.add_argument("--marker-title", default="Spatial Cluster Marker Heatmap")
    args = parser.parse_args(argv)

    config = json.loads(args.config_json) if args.config_json else {}
    summary = run_pipeline(
        output_dir=args.output_dir,
        cluster_figure_path=args.cluster_figure_path,
        marker_figure_path=args.marker_figure_path,
        summary_path=args.summary_path,
        annotated_data_path=args.annotated_data_path,
        raw_data_path=args.raw_data_path,
        selected_repo=args.selected_repo,
        config=config,
        cluster_title=args.cluster_title,
        marker_title=args.marker_title,
    )
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
