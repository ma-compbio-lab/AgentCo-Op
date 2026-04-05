from __future__ import annotations

import argparse
import importlib.metadata
import json
from collections.abc import Mapping as AbcMapping, Sequence as AbcSequence
from pathlib import Path
from typing import Any, Mapping


DEFAULT_ANALYSIS_CONFIG: dict[str, Any] = {
    "dataset_id": "paul15",
    "target_sum": 10000.0,
    "n_top_genes": 2000,
    "n_neighbors": 12,
    "n_pcs": 30,
    "resolution": 0.8,
    "max_scale_value": 10.0,
    "seed": 0,
    "cluster_key": "leiden",
    "root_strategy": "min_diffmap_1",
    "dpi": 150,
    "paga_threshold": 0.03,
    "marker_top_n": 3,
    "dynamic_gene_top_n": 5,
    "dynamic_gene_panel_top_n": 4,
}


def _load_dataset(scanpy_module: Any, dataset_id: str) -> tuple[Any, dict[str, Any]]:
    normalized_id = dataset_id.strip().lower() or "paul15"
    if normalized_id == "paul15":
        return scanpy_module.datasets.paul15(), {
            "dataset_id": "paul15",
            "dataset_label": "Paul15",
            "group_key": "paul15_clusters",
        }
    if normalized_id == "moignard15":
        return scanpy_module.datasets.moignard15(), {
            "dataset_id": "moignard15",
            "dataset_label": "Moignard15",
            "group_key": "exp_groups",
        }
    if normalized_id == "krumsiek11":
        return scanpy_module.datasets.krumsiek11(), {
            "dataset_id": "krumsiek11",
            "dataset_label": "Krumsiek11",
            "group_key": "cell_type",
        }
    raise ValueError(f"Unsupported trajectory dataset_id: {dataset_id}")


def _stringify_uns_keys(value: Any) -> Any:
    if isinstance(value, AbcMapping):
        return {str(key): _stringify_uns_keys(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_stringify_uns_keys(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_stringify_uns_keys(item) for item in value)
    if isinstance(value, AbcSequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_stringify_uns_keys(item) for item in value]
    return value


def run_pipeline(
    *,
    output_dir: str | Path,
    figure_path: str | Path,
    paga_figure_path: str | Path,
    gene_trend_figure_path: str | Path | None = None,
    summary_path: str | Path,
    raw_data_path: str | Path | None = None,
    selected_repo: str = "scanpy",
    config: Mapping[str, Any] | None = None,
    figure_title: str = "Paul15 Trajectory",
    paga_title: str = "Paul15 PAGA Graph",
) -> dict[str, Any]:
    import matplotlib.pyplot as plt
    import numpy as np
    import scanpy as sc
    from PIL import Image
    from scipy import sparse
    from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score

    cfg = dict(DEFAULT_ANALYSIS_CONFIG)
    if config:
        cfg.update(dict(config))
    dataset_id = str(cfg.get("dataset_id", "paul15")).strip().lower() or "paul15"

    output_dir = Path(output_dir).resolve()
    figure_path = Path(figure_path).resolve()
    paga_figure_path = Path(paga_figure_path).resolve()
    gene_trend_figure = Path(gene_trend_figure_path).resolve() if gene_trend_figure_path else None
    summary_path = Path(summary_path).resolve()
    raw_data = Path(raw_data_path).resolve() if raw_data_path else None
    output_dir.mkdir(parents=True, exist_ok=True)
    figure_path.parent.mkdir(parents=True, exist_ok=True)
    paga_figure_path.parent.mkdir(parents=True, exist_ok=True)
    if gene_trend_figure is not None:
        gene_trend_figure.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    if raw_data is not None:
        raw_data.parent.mkdir(parents=True, exist_ok=True)

    sc.settings.autoshow = False
    sc.settings.verbosity = 0

    adata_raw, dataset_meta = _load_dataset(sc, dataset_id)
    if not sparse.issparse(adata_raw.X):
        adata_raw.X = np.asarray(adata_raw.X, dtype=float)
    dataset_label = str(dataset_meta["dataset_label"])
    dataset_group_key = str(dataset_meta.get("group_key", "")).strip()
    if hasattr(adata_raw, "obs_names_make_unique"):
        adata_raw.obs_names_make_unique()
    if hasattr(adata_raw, "uns") and isinstance(adata_raw.uns, AbcMapping):
        adata_raw.uns = _stringify_uns_keys(dict(adata_raw.uns))
    if raw_data is not None:
        adata_raw.write_h5ad(str(raw_data))
    adata = adata_raw.copy()

    if sparse.issparse(adata_raw.X):
        total_counts = np.asarray(adata_raw.X.sum(axis=1)).ravel()
        detected_genes = np.asarray((adata_raw.X > 0).sum(axis=1)).ravel()
    else:
        total_counts = np.asarray(adata_raw.X.sum(axis=1)).ravel()
        detected_genes = np.asarray((adata_raw.X > 0).sum(axis=1)).ravel()
    preprocessed_input = dataset_id in {"moignard15", "krumsiek11"}
    if preprocessed_input:
        qc_summary = {
            "input_representation": "preprocessed_noncount_matrix",
            "value_mean": float(total_counts.mean()),
            "value_range": [float(total_counts.min()), float(total_counts.max())],
            "nonzero_features_mean": float(detected_genes.mean()),
            "nonzero_features_range": [float(detected_genes.min()), float(detected_genes.max())],
        }
    else:
        qc_summary = {
            "input_representation": "raw_counts",
            "total_counts_mean": float(total_counts.mean()),
            "total_counts_range": [float(total_counts.min()), float(total_counts.max())],
            "detected_genes_mean": float(detected_genes.mean()),
            "detected_genes_range": [float(detected_genes.min()), float(detected_genes.max())],
        }
    preprocessing_summary = {
        "cell_filtering": "none",
        "gene_filtering": "none",
        "normalization_target_sum": "skipped_preprocessed_input" if preprocessed_input else float(cfg["target_sum"]),
        "highly_variable_genes_selected": "all_input_genes" if preprocessed_input else int(cfg["n_top_genes"]),
        "neighbors_k": int(cfg["n_neighbors"]),
        "requested_n_pcs": int(cfg["n_pcs"]),
        "leiden_resolution": float(cfg["resolution"]),
        "paga_threshold": float(cfg["paga_threshold"]),
        "dataset_adaptation": (
            "Dataset values are not raw counts; skip count normalization/log1p/HVG selection and preserve target-dataset semantics."
            if preprocessed_input
            else "Standard count-based preprocessing."
        ),
    }

    if preprocessed_input:
        adata_log = adata.copy()
    else:
        sc.pp.normalize_total(adata, target_sum=float(cfg["target_sum"]))
        sc.pp.log1p(adata)
        sc.pp.highly_variable_genes(adata, n_top_genes=int(cfg["n_top_genes"]))
        adata = adata[:, adata.var["highly_variable"]].copy()
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
    sc.tl.paga(adata, groups=cluster_key)
    sc.tl.diffmap(adata)
    root_strategy = str(cfg.get("root_strategy", "min_diffmap_1"))
    configured_root = cfg.get("root_index")
    if configured_root is not None:
        root_index = int(configured_root)
    elif root_strategy == "dataset_default" and "iroot" in adata.uns:
        root_index = int(adata.uns["iroot"])
    elif root_strategy == "min_diffmap_1":
        root_index = int(np.argmin(adata.obsm["X_diffmap"][:, 0]))
    elif root_strategy == "max_diffmap_1":
        root_index = int(np.argmax(adata.obsm["X_diffmap"][:, 0]))
    else:
        root_index = int(np.argmin(adata.obsm["X_diffmap"][:, 0]))
    adata.uns["iroot"] = root_index
    sc.tl.dpt(adata)
    pseudotime_values = np.asarray(adata.obs["dpt_pseudotime"], dtype=float)
    finite_pseudotime = np.isfinite(pseudotime_values)
    pseudotime_method = "dpt"
    if int(finite_pseudotime.sum()) < int(pseudotime_values.size):
        diffmap_axis = np.asarray(adata.obsm["X_diffmap"][:, 0], dtype=float)
        order = np.argsort(diffmap_axis)
        fallback = np.zeros_like(diffmap_axis, dtype=float)
        if int(order.size) > 1:
            fallback[order] = np.linspace(0.0, 1.0, num=int(order.size), dtype=float)
        pseudotime_values = fallback
        adata.obs["dpt_pseudotime"] = pseudotime_values.tolist()
        finite_pseudotime = np.isfinite(pseudotime_values)
        pseudotime_method = "diffmap_rank_fallback"

    adata_log.obs[cluster_key] = adata.obs[cluster_key].astype(str).tolist()
    adata_log.obs[cluster_key] = adata_log.obs[cluster_key].astype("category")
    adata_log.obs["dpt_pseudotime"] = adata.obs["dpt_pseudotime"].astype(float).tolist()
    sc.tl.rank_genes_groups(adata_log, groupby=cluster_key, method="wilcoxon")

    figure = sc.pl.umap(
        adata,
        color=[cluster_key, "dpt_pseudotime"],
        return_fig=True,
        show=False,
        title=[figure_title, "Paul15 pseudotime"],
    )
    figure.savefig(figure_path, dpi=int(cfg["dpi"]), bbox_inches="tight")
    plt.close(figure)

    sc.pl.paga(
        adata,
        color=cluster_key,
        threshold=float(cfg["paga_threshold"]),
        show=False,
        title=paga_title,
    )
    paga_fig = plt.gcf()
    paga_fig.savefig(paga_figure_path, dpi=int(cfg["dpi"]), bbox_inches="tight")
    plt.close(paga_fig)

    main_image = Image.open(figure_path)
    paga_image = Image.open(paga_figure_path)
    cluster_sizes = {
        str(label): int(count)
        for label, count in adata.obs[cluster_key].value_counts().sort_index().items()
    }
    dataset_group_labels: list[str] = []
    dataset_group_sizes: dict[str, int] = {}
    external_group_metrics: dict[str, float] = {}
    root_group = ""
    if dataset_group_key and dataset_group_key in adata_raw.obs:
        dataset_groups = adata_raw.obs[dataset_group_key].astype(str).tolist()
        dataset_group_labels = sorted({str(label) for label in dataset_groups})
        dataset_group_sizes = {
            str(label): int(count)
            for label, count in adata_raw.obs[dataset_group_key].astype(str).value_counts().sort_index().items()
        }
        root_group = str(adata_raw.obs[dataset_group_key].iloc[root_index])
        cluster_labels = adata.obs[cluster_key].astype(str).tolist()
        if len(cluster_labels) == len(dataset_groups) and len(set(dataset_groups)) > 1 and len(set(cluster_labels)) > 1:
            external_group_metrics = {
                "ari": float(adjusted_rand_score(dataset_groups, cluster_labels)),
                "nmi": float(normalized_mutual_info_score(dataset_groups, cluster_labels)),
            }
    pseudotime = adata.obs["dpt_pseudotime"].astype(float)
    root_cluster = str(adata.obs[cluster_key].iloc[root_index])
    connectivities = adata.uns["paga"]["connectivities"]
    nnz = int(getattr(connectivities, "nnz", 0))
    marker_top_n = max(1, int(cfg.get("marker_top_n", 3)))
    dynamic_gene_top_n = max(1, int(cfg.get("dynamic_gene_top_n", 5)))
    marker_gene_summary: dict[str, list[str]] = {}
    gene_names_by_group = adata_log.uns.get("rank_genes_groups", {}).get("names")
    if gene_names_by_group is not None:
        for category in adata_log.obs[cluster_key].cat.categories:
            marker_gene_summary[str(category)] = [
                str(gene)
                for gene in gene_names_by_group[str(category)][:marker_top_n]
                if str(gene).strip()
            ]

    expr = adata_log.X.toarray() if sparse.issparse(adata_log.X) else np.asarray(adata_log.X)
    expr = np.asarray(expr, dtype=float)
    expr_mean = expr.mean(axis=0)
    expr_std = expr.std(axis=0)
    pt = pseudotime.to_numpy(dtype=float)
    pt_centered = pt - pt.mean()
    pt_std = float(pt.std())
    safe_gene_std = np.where(expr_std == 0.0, 1.0, expr_std)
    safe_pt_std = pt_std if pt_std > 0.0 else 1.0
    corr = ((expr - expr_mean).T @ pt_centered) / max(len(pt), 1)
    corr = corr / (safe_gene_std * safe_pt_std)
    corr = np.nan_to_num(corr, nan=0.0, posinf=0.0, neginf=0.0)
    gene_names = np.asarray(adata_log.var_names, dtype=str)
    top_pos_idx = np.argsort(corr)[-dynamic_gene_top_n:][::-1]
    top_neg_idx = np.argsort(corr)[:dynamic_gene_top_n]
    dynamic_gene_summary = {
        "positive": [
            {"gene": str(gene_names[idx]), "correlation": float(corr[idx])}
            for idx in top_pos_idx
        ],
        "negative": [
            {"gene": str(gene_names[idx]), "correlation": float(corr[idx])}
            for idx in top_neg_idx
        ],
        "max_abs_correlation": float(np.max(np.abs(corr))) if corr.size else 0.0,
    }
    gene_trend_panel_genes: list[str] = []
    gene_trend_panel_width = 0
    gene_trend_panel_height = 0
    gene_trend_table_path = output_dir / "generated_dynamic_gene_panel_values.json"
    if gene_trend_figure is not None:
        panel_top_n = max(1, int(cfg.get("dynamic_gene_panel_top_n", 4)))
        selected_panel_genes = [
            item["gene"]
            for item in dynamic_gene_summary["positive"][: max(1, panel_top_n // 2)]
        ] + [
            item["gene"]
            for item in dynamic_gene_summary["negative"][: max(1, panel_top_n // 2)]
        ]
        deduped_panel_genes: list[str] = []
        for gene in selected_panel_genes:
            if gene not in deduped_panel_genes:
                deduped_panel_genes.append(gene)
        if not deduped_panel_genes and gene_names.size:
            deduped_panel_genes = [str(gene_names[0])]
        gene_trend_panel_genes = deduped_panel_genes[:panel_top_n]
        gene_to_index = {str(name): idx for idx, name in enumerate(gene_names.tolist())}
        pt_values = pseudotime.to_numpy(dtype=float)
        pt_order = np.argsort(pt_values)
        ordered_pt = pt_values[pt_order]
        panel_count = len(gene_trend_panel_genes)
        n_cols = 2 if panel_count > 1 else 1
        n_rows = int(np.ceil(panel_count / n_cols))
        trend_figure, axes = plt.subplots(n_rows, n_cols, figsize=(6.5 * n_cols, 3.8 * n_rows), squeeze=False)
        flat_axes = axes.ravel().tolist()
        trend_series_by_gene: dict[str, dict[str, Any]] = {}
        for axis, gene in zip(flat_axes, gene_trend_panel_genes):
            gene_index = gene_to_index.get(gene)
            if gene_index is None:
                axis.set_visible(False)
                continue
            gene_values = expr[:, gene_index][pt_order]
            axis.scatter(ordered_pt, gene_values, s=6, alpha=0.35, color="#355C7D")
            gene_series: dict[str, Any] = {
                "point_count": int(ordered_pt.size),
                "pseudotime_range": [
                    float(ordered_pt.min()) if ordered_pt.size else 0.0,
                    float(ordered_pt.max()) if ordered_pt.size else 0.0,
                ],
            }
            if ordered_pt.size >= 32:
                bins = np.linspace(float(ordered_pt.min()), float(ordered_pt.max()), num=25)
                bin_ids = np.digitize(ordered_pt, bins, right=True)
                centers: list[float] = []
                means: list[float] = []
                counts: list[int] = []
                for bin_id in sorted(set(int(item) for item in bin_ids.tolist())):
                    mask = bin_ids == bin_id
                    if int(mask.sum()) < 3:
                        continue
                    centers.append(float(ordered_pt[mask].mean()))
                    means.append(float(gene_values[mask].mean()))
                    counts.append(int(mask.sum()))
                if centers and means:
                    axis.plot(centers, means, color="#C06C84", linewidth=2.0)
                gene_series["binned_trend"] = {
                    "centers": centers,
                    "mean_expression": means,
                    "counts": counts,
                }
            else:
                gene_series["binned_trend"] = {
                    "centers": [float(value) for value in ordered_pt.tolist()],
                    "mean_expression": [float(value) for value in gene_values.tolist()],
                    "counts": [1 for _ in ordered_pt.tolist()],
                }
            axis.set_title(gene)
            axis.set_xlabel("DPT pseudotime")
            axis.set_ylabel("log-normalized expression")
            trend_series_by_gene[gene] = gene_series
        for axis in flat_axes[panel_count:]:
            axis.set_visible(False)
        trend_figure.suptitle(f"{dataset_label} Dynamic Gene Trends")
        trend_figure.tight_layout()
        trend_figure.savefig(gene_trend_figure, dpi=int(cfg["dpi"]), bbox_inches="tight")
        plt.close(trend_figure)
        trend_image = Image.open(gene_trend_figure)
        gene_trend_panel_width = int(trend_image.size[0])
        gene_trend_panel_height = int(trend_image.size[1])
        gene_trend_table_path.write_text(
            json.dumps(
                {
                    "genes": list(gene_trend_panel_genes),
                    "series": trend_series_by_gene,
                    "x_axis": "DPT pseudotime",
                    "y_axis": "log-normalized expression",
                    "panel_layout": {
                        "width": gene_trend_panel_width,
                        "height": gene_trend_panel_height,
                    },
                },
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )
    dynamic_gene_panel_summary = {
        "plotted_genes": gene_trend_panel_genes,
        "x_axis": "DPT pseudotime",
        "y_axis": "log-normalized expression",
        "panel_layout": {
            "width": gene_trend_panel_width,
            "height": gene_trend_panel_height,
        },
    }
    root_provenance = {
        "strategy": root_strategy,
        "root_index": int(root_index),
        "root_cluster": root_cluster,
        "root_group": root_group,
        "used_dataset_default": bool(root_strategy == "dataset_default" and configured_root is None),
        "pseudotime_method": pseudotime_method,
        "nonfinite_dpt_replaced": bool(pseudotime_method != "dpt"),
        "diffmap_1_value": float(adata.obsm["X_diffmap"][root_index, 0]),
    }
    finite_pt = pt[np.isfinite(pt)]
    summary = {
        "dataset_id": dataset_id,
        "dataset_label": dataset_label,
        "selected_repo": selected_repo,
        "scanpy_version": importlib.metadata.version("scanpy"),
        "output_dir": str(output_dir),
        "figure_path": str(figure_path),
        "paga_figure_path": str(paga_figure_path),
        "gene_trend_figure_path": str(gene_trend_figure) if gene_trend_figure is not None else "",
        "dynamic_gene_panel_table_path": str(gene_trend_table_path) if gene_trend_figure is not None else "",
        "summary_path": str(summary_path),
        "raw_data_path": str(raw_data) if raw_data is not None else "",
        "n_obs": int(adata.n_obs),
        "n_vars": int(adata.n_vars),
        "highly_variable_gene_count": int(adata.n_vars),
        "dataset_group_key": dataset_group_key,
        "dataset_group_count": int(len(dataset_group_labels)),
        "dataset_group_labels": dataset_group_labels,
        "dataset_group_sizes": dataset_group_sizes,
        "external_group_metrics": external_group_metrics,
        "cluster_key": cluster_key,
        "cluster_count": int(adata.obs[cluster_key].nunique()),
        "cluster_sizes": cluster_sizes,
        "pseudotime_range": [
            float(finite_pt.min()) if finite_pt.size else 0.0,
            float(finite_pt.max()) if finite_pt.size else 0.0,
        ],
        "pseudotime_mean": float(finite_pt.mean()) if finite_pt.size else 0.0,
        "root_index": root_index,
        "root_strategy": root_strategy,
        "root_cluster": root_cluster,
        "root_group": root_group,
        "root_provenance": root_provenance,
        "n_pcs_used": int(n_pcs),
        "n_neighbors_used": int(cfg["n_neighbors"]),
        "paga_edge_count": int(nnz // 2 if nnz else 0),
        "paga_connectivity_max": float(connectivities.max()) if nnz else 0.0,
        "figure_width": int(main_image.size[0]),
        "figure_height": int(main_image.size[1]),
        "paga_figure_width": int(paga_image.size[0]),
        "paga_figure_height": int(paga_image.size[1]),
        "gene_trend_figure_width": gene_trend_panel_width,
        "gene_trend_figure_height": gene_trend_panel_height,
        "qc_summary": qc_summary,
        "preprocessing_summary": preprocessing_summary,
        "marker_gene_summary": marker_gene_summary,
        "dynamic_gene_summary": dynamic_gene_summary,
        "dynamic_gene_panel_genes": gene_trend_panel_genes,
        "dynamic_gene_panel_summary": dynamic_gene_panel_summary,
        "analysis_config": cfg,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Paul15 Scanpy trajectory case-study pipeline.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--figure-path", required=True)
    parser.add_argument("--paga-figure-path", required=True)
    parser.add_argument("--gene-trend-figure-path", default="")
    parser.add_argument("--summary-path", required=True)
    parser.add_argument("--raw-data-path", default="")
    parser.add_argument("--selected-repo", default="scanpy")
    parser.add_argument("--config-json", default="")
    parser.add_argument("--title", default="Paul15 Trajectory")
    parser.add_argument("--paga-title", default="Paul15 PAGA Graph")
    args = parser.parse_args(argv)

    config = json.loads(args.config_json) if args.config_json else {}
    summary = run_pipeline(
        output_dir=args.output_dir,
        figure_path=args.figure_path,
        paga_figure_path=args.paga_figure_path,
        gene_trend_figure_path=args.gene_trend_figure_path or None,
        summary_path=args.summary_path,
        raw_data_path=args.raw_data_path or None,
        selected_repo=args.selected_repo,
        config=config,
        figure_title=args.title,
        paga_title=args.paga_title,
    )
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
