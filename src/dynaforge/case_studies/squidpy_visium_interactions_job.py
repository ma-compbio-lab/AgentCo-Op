from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np


DEFAULT_ANALYSIS_CONFIG: dict[str, Any] = {
    "dataset_id": "visium_hne_adata_crop",
    "cluster_key": "cluster",
    "dpi": 150,
    "compute_spatial_neighbors": True,
    "compute_nhood_enrichment": True,
    "nhood_top_n": 5,
    "linkage_method": "ward",
    "cmap": "coolwarm",
    "seed": 0,
}


def _load_dataset(squidpy_module: Any, dataset_id: str) -> tuple[Any, dict[str, Any]]:
    normalized = dataset_id.strip().lower() or "visium_hne_adata_crop"
    if normalized in {"visium", "visium_hne", "visium_hne_adata_crop"}:
        return squidpy_module.datasets.visium_hne_adata_crop(), {
            "dataset_id": "visium_hne_adata_crop",
            "dataset_label": "Visium H&E",
            "raw_filename": "visium_hne_adata_crop.h5ad",
            "default_title": "Visium Neighborhood Enrichment",
            "spatial_scatter": {"shape": "hex", "img": True, "size": 1.0},
        }
    if normalized in {"seqfish", "seqfish_mouse_embryo"}:
        return squidpy_module.datasets.seqfish(), {
            "dataset_id": "seqfish",
            "dataset_label": "seqFISH",
            "raw_filename": "seqfish.h5ad",
            "default_title": "seqFISH Neighborhood Enrichment",
            "spatial_scatter": {"img": False, "size": 1.2},
        }
    raise ValueError(f"Unsupported spatial dataset_id: {dataset_id}")


def _top_pairs(labels: list[str], zscores: np.ndarray, counts: np.ndarray, *, top_n: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    pairs: list[dict[str, Any]] = []
    size = min(len(labels), int(zscores.shape[0]), int(zscores.shape[1]))
    for i in range(size):
        for j in range(i + 1, size):
            pairs.append(
                {
                    "source": labels[i],
                    "target": labels[j],
                    "zscore": float(zscores[i, j]),
                    "count": int(counts[i, j]),
                }
            )
    enriched = sorted(pairs, key=lambda item: item["zscore"], reverse=True)[:top_n]
    depleted = sorted(pairs, key=lambda item: item["zscore"])[:top_n]
    return enriched, depleted


def _render_spatial_layout(
    *,
    adata: Any,
    cluster_key: str,
    figure_path: Path,
    dpi: int,
    title: str,
) -> None:
    import matplotlib.pyplot as plt

    if "spatial" not in adata.obsm:
        raise RuntimeError("spatial coordinates missing from adata.obsm['spatial']")
    coords = np.asarray(adata.obsm["spatial"], dtype=float)
    if coords.ndim != 2 or coords.shape[1] < 2:
        raise RuntimeError("unexpected spatial coordinate shape")

    labels = adata.obs[cluster_key].astype(str).tolist()
    unique_labels = list(dict.fromkeys(labels))
    palette = plt.get_cmap("tab20")
    fig, ax = plt.subplots(figsize=(6.5, 5.5))
    for idx, label in enumerate(unique_labels):
        mask = np.asarray([item == label for item in labels], dtype=bool)
        ax.scatter(
            coords[mask, 0],
            coords[mask, 1],
            s=10,
            alpha=0.85,
            label=label,
            color=palette(idx % 20),
            linewidths=0.0,
        )
    ax.set_title(title)
    ax.set_xlabel("spatial_x")
    ax.set_ylabel("spatial_y")
    ax.invert_yaxis()
    if len(unique_labels) <= 12:
        ax.legend(loc="best", fontsize=7, frameon=False)
    fig.tight_layout()
    fig.savefig(figure_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def run_pipeline(
    *,
    output_dir: str | Path,
    figure_path: str | Path,
    interaction_figure_path: str | Path | None = None,
    summary_path: str | Path,
    raw_data_path: str | Path | None = None,
    input_h5ad_path: str | Path | None = None,
    selected_repo: str = "squidpy",
    config: Mapping[str, Any] | None = None,
    figure_title: str = "Spatial Neighborhood Enrichment",
) -> dict[str, Any]:
    import matplotlib.pyplot as plt
    import scanpy as sc
    import squidpy as sq
    from PIL import Image

    cfg = dict(DEFAULT_ANALYSIS_CONFIG)
    if config:
        cfg.update(dict(config))
    dataset_id = str(cfg.get("dataset_id", "visium_hne_adata_crop")).strip().lower() or "visium_hne_adata_crop"

    output_dir = Path(output_dir).resolve()
    figure_path = Path(figure_path).resolve()
    interaction_figure = Path(interaction_figure_path).resolve() if interaction_figure_path else None
    summary_path = Path(summary_path).resolve()
    raw_data = Path(raw_data_path).resolve() if raw_data_path else None
    input_h5ad = Path(input_h5ad_path).resolve() if input_h5ad_path else None
    output_dir.mkdir(parents=True, exist_ok=True)
    figure_path.parent.mkdir(parents=True, exist_ok=True)
    if interaction_figure is not None:
        interaction_figure.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    if raw_data is not None:
        raw_data.parent.mkdir(parents=True, exist_ok=True)

    dataset_meta: dict[str, Any] = {}
    if input_h5ad is not None and input_h5ad.exists():
        adata = sc.read_h5ad(str(input_h5ad))
        dataset_meta = {
            "dataset_id": dataset_id,
            "dataset_label": "seqFISH" if dataset_id == "seqfish" else "Visium H&E",
            "default_title": figure_title,
            "spatial_scatter": {"img": bool(adata.uns.get("spatial")), "shape": "hex" if adata.uns.get("spatial") else None, "size": 1.0},
        }
    else:
        adata, dataset_meta = _load_dataset(sq, dataset_id)
        if raw_data is not None:
            adata.write_h5ad(str(raw_data))

    cluster_key = str(cfg.get("cluster_key", "cluster"))
    if cluster_key not in adata.obs:
        raise RuntimeError(f"cluster key '{cluster_key}' missing from adata.obs")

    if bool(cfg.get("compute_spatial_neighbors", True)):
        sq.gr.spatial_neighbors(adata)
    if bool(cfg.get("compute_nhood_enrichment", True)):
        sq.gr.nhood_enrichment(adata, cluster_key=cluster_key, seed=int(cfg.get("seed", 0)))

    enrichment_key = f"{cluster_key}_nhood_enrichment"
    enrichment = adata.uns.get(enrichment_key)
    if not isinstance(enrichment, Mapping):
        raise RuntimeError(f"missing Squidpy enrichment payload at adata.uns['{enrichment_key}']")

    zscores = np.asarray(enrichment.get("zscore"))
    counts_payload = enrichment.get("count", enrichment.get("counts"))
    counts = np.asarray(counts_payload)
    if zscores.ndim != 2 or counts.ndim != 2:
        raise RuntimeError("unexpected enrichment matrix shape")

    labels = [str(item) for item in adata.obs[cluster_key].astype("category").cat.categories.tolist()]
    top_n = max(1, int(cfg.get("nhood_top_n", 5)))
    top_enriched_pairs, top_depleted_pairs = _top_pairs(labels, zscores, counts, top_n=top_n)

    spatial_title = str(cfg.get("spatial_title", f"{dataset_meta.get('dataset_label', 'Spatial')} Layout"))
    if bool(adata.uns.get("spatial")):
        scatter_kwargs = dict(dataset_meta.get("spatial_scatter", {}))
        scatter_kwargs["title"] = spatial_title
        scatter_kwargs["return_ax"] = True
        scatter_kwargs["color"] = cluster_key
        axes = sq.pl.spatial_scatter(adata, **scatter_kwargs)
        spatial_axis = axes[0] if isinstance(axes, (list, tuple)) else axes
        if spatial_axis is None:
            raise RuntimeError("squidpy.pl.spatial_scatter did not return matplotlib axes")
        spatial_figure = spatial_axis.figure
        spatial_figure.savefig(figure_path, dpi=int(cfg.get("dpi", 150)), bbox_inches="tight")
        plt.close(spatial_figure)
    else:
        _render_spatial_layout(
            adata=adata,
            cluster_key=cluster_key,
            figure_path=figure_path,
            dpi=int(cfg.get("dpi", 150)),
            title=spatial_title,
        )

    interaction_plot_path = interaction_figure or figure_path
    finite_zscores = np.nan_to_num(zscores, nan=0.0, posinf=0.0, neginf=0.0)
    heatmap_figure, axis = plt.subplots(figsize=(7.0, 6.0))
    image_artist = axis.imshow(finite_zscores, cmap=str(cfg.get("cmap", "coolwarm")), aspect="auto")
    axis.set_title(figure_title)
    axis.set_xticks(range(len(labels)))
    axis.set_yticks(range(len(labels)))
    axis.set_xticklabels(labels, rotation=45, ha="right")
    axis.set_yticklabels(labels)
    heatmap_figure.colorbar(image_artist, ax=axis, fraction=0.046, pad=0.04, label="Neighborhood enrichment z-score")
    heatmap_figure.tight_layout()
    heatmap_figure.savefig(interaction_plot_path, dpi=int(cfg.get("dpi", 150)), bbox_inches="tight")
    plt.close(heatmap_figure)

    image = Image.open(figure_path)
    interaction_image = Image.open(interaction_plot_path)
    cluster_sizes = {
        str(label): int(count)
        for label, count in adata.obs[cluster_key].value_counts().sort_index().items()
    }
    spatial_connectivities = adata.obsp.get("spatial_connectivities")
    summary = {
        "dataset_id": str(dataset_meta.get("dataset_id", dataset_id)),
        "dataset_label": str(dataset_meta.get("dataset_label", dataset_id)),
        "selected_repo": selected_repo,
        "squidpy_version": importlib.metadata.version("squidpy"),
        "output_dir": str(output_dir),
        "figure_path": str(figure_path),
        "interaction_figure_path": str(interaction_plot_path),
        "summary_path": str(summary_path),
        "raw_data_path": str(raw_data) if raw_data is not None else "",
        "input_h5ad_path": str(input_h5ad) if input_h5ad is not None else "",
        "n_obs": int(adata.n_obs),
        "n_vars": int(adata.n_vars),
        "cluster_key": cluster_key,
        "cluster_labels": labels,
        "cluster_count": int(adata.obs[cluster_key].nunique()),
        "cluster_sizes": cluster_sizes,
        "has_spatial_image": bool(adata.uns.get("spatial")),
        "spatial_connectivities_nnz": int(getattr(spatial_connectivities, "nnz", 0)),
        "has_nhood_enrichment": True,
        "nhood_enrichment_shape": [int(zscores.shape[0]), int(zscores.shape[1])],
        "nhood_counts_total": int(np.asarray(counts).sum()),
        "nhood_zscore_max": float(np.max(finite_zscores)),
        "nhood_zscore_min": float(np.min(finite_zscores)),
        "nhood_zscore_abs_mean": float(np.mean(np.abs(finite_zscores))),
        "top_enriched_pairs": top_enriched_pairs,
        "top_depleted_pairs": top_depleted_pairs,
        "nhood_enrichment_summary": {
            "shape": [int(zscores.shape[0]), int(zscores.shape[1])],
            "counts_total": int(np.asarray(counts).sum()),
            "zscore_max": float(np.max(finite_zscores)),
            "zscore_min": float(np.min(finite_zscores)),
            "zscore_abs_mean": float(np.mean(np.abs(finite_zscores))),
            "top_enriched_pairs": top_enriched_pairs,
            "top_depleted_pairs": top_depleted_pairs,
        },
        "figure_width": int(image.size[0]),
        "figure_height": int(image.size[1]),
        "interaction_figure_width": int(interaction_image.size[0]),
        "interaction_figure_height": int(interaction_image.size[1]),
        "analysis_config": cfg,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Squidpy spatial neighborhood-enrichment case-study pipeline.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--figure-path", required=True)
    parser.add_argument("--interaction-figure-path", default="")
    parser.add_argument("--summary-path", required=True)
    parser.add_argument("--raw-data-path", default="")
    parser.add_argument("--input-h5ad-path", default="")
    parser.add_argument("--selected-repo", default="squidpy")
    parser.add_argument("--config-json", default="")
    parser.add_argument("--title", default="Spatial Neighborhood Enrichment")
    args = parser.parse_args(argv)

    config = json.loads(args.config_json) if args.config_json else {}
    summary = run_pipeline(
        output_dir=args.output_dir,
        figure_path=args.figure_path,
        interaction_figure_path=args.interaction_figure_path or None,
        summary_path=args.summary_path,
        raw_data_path=args.raw_data_path or None,
        input_h5ad_path=args.input_h5ad_path or None,
        selected_repo=args.selected_repo,
        config=config,
        figure_title=args.title,
    )
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
