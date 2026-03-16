from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np


DEFAULT_ANALYSIS_CONFIG: dict[str, Any] = {
    "color_by": "cluster",
    "shape": "hex",
    "library_key": None,
    "img": True,
    "size": 1.0,
    "dpi": 150,
    "compute_spatial_neighbors": True,
    "compute_nhood_enrichment": False,
    "interaction_top_n": 5,
}


def _save_plot_figure(plot_result: Any, figure_path: Path, *, dpi: int) -> tuple[int, int]:
    import matplotlib.pyplot as plt
    from PIL import Image

    axis = None
    if isinstance(plot_result, np.ndarray):
        axis = plot_result.flat[0] if plot_result.size else None
    elif isinstance(plot_result, (list, tuple)):
        axis = plot_result[0] if plot_result else None
    else:
        axis = plot_result

    figure = getattr(axis, "figure", None) if axis is not None else None
    if figure is None:
        figure = plt.gcf()
    figure.savefig(figure_path, dpi=dpi, bbox_inches="tight")
    plt.close(figure)

    image = Image.open(figure_path)
    return int(image.size[0]), int(image.size[1])


def _cluster_categories(values: Any) -> list[str]:
    cat = getattr(values, "cat", None)
    if cat is not None:
        return [str(item) for item in cat.categories.tolist()]
    return sorted(str(item) for item in values.dropna().unique().tolist())


def _summarize_nhood_enrichment(
    adata: Any,
    *,
    cluster_key: str,
    top_n: int,
) -> dict[str, Any]:
    payload = adata.uns.get(f"{cluster_key}_nhood_enrichment")
    if not isinstance(payload, Mapping):
        raise RuntimeError(f"Expected `{cluster_key}_nhood_enrichment` in adata.uns")

    zscore = payload.get("zscore")
    count = payload.get("count")
    if zscore is None:
        raise RuntimeError("Neighborhood-enrichment payload missing `zscore`")

    zscore_array = np.asarray(zscore, dtype=float)
    count_array = np.asarray(count, dtype=float) if count is not None else None
    if zscore_array.ndim != 2:
        raise RuntimeError("Neighborhood-enrichment zscore must be a 2D matrix")
    finite_zscore = np.nan_to_num(zscore_array, nan=0.0, posinf=0.0, neginf=0.0)

    labels = _cluster_categories(adata.obs[cluster_key])
    if len(labels) != finite_zscore.shape[0]:
        labels = [str(index) for index in range(finite_zscore.shape[0])]

    pair_stats: list[dict[str, Any]] = []
    for i, source in enumerate(labels):
        for j, target in enumerate(labels):
            pair_stats.append(
                {
                    "source": source,
                    "target": target,
                    "zscore": float(finite_zscore[i, j]),
                    "count": float(count_array[i, j]) if count_array is not None else None,
                    "same_cluster": bool(i == j),
                }
            )

    by_abs_zscore = sorted(pair_stats, key=lambda item: abs(float(item["zscore"])), reverse=True)
    top_same = sorted(
        [item for item in pair_stats if item["same_cluster"]],
        key=lambda item: float(item["zscore"]),
        reverse=True,
    )
    top_cross = sorted(
        [item for item in pair_stats if not item["same_cluster"]],
        key=lambda item: float(item["zscore"]),
        reverse=True,
    )

    return {
        "cluster_key": cluster_key,
        "labels": labels,
        "zscore_matrix": finite_zscore.tolist(),
        "shape": [int(finite_zscore.shape[0]), int(finite_zscore.shape[1])],
        "max_abs_zscore": float(np.max(np.abs(finite_zscore))),
        "top_pairs": by_abs_zscore[:top_n],
        "top_same_cluster_pairs": top_same[:top_n],
        "top_cross_cluster_pairs": top_cross[:top_n],
    }


def _render_nhood_enrichment_heatmap(
    interaction_summary: Mapping[str, Any],
    figure_path: Path,
    *,
    dpi: int,
) -> tuple[int, int]:
    import matplotlib.pyplot as plt

    labels = [str(item) for item in interaction_summary.get("labels", [])]
    zscore_matrix = np.asarray(interaction_summary.get("zscore_matrix", []), dtype=float)
    if zscore_matrix.ndim != 2 or zscore_matrix.size == 0:
        raise RuntimeError("Neighborhood-enrichment summary is missing a renderable zscore matrix")

    figure, axis = plt.subplots(
        figsize=(
            max(5.0, min(10.0, 0.6 * max(1, len(labels)))),
            max(4.0, min(9.0, 0.5 * max(1, len(labels)))),
        )
    )
    image = axis.imshow(zscore_matrix, cmap="coolwarm", aspect="auto")
    axis.set_title("Visium H&E Neighborhood Enrichment")
    axis.set_xticks(range(len(labels)))
    axis.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    axis.set_yticks(range(len(labels)))
    axis.set_yticklabels(labels, fontsize=8)
    axis.set_xlabel("Target cluster")
    axis.set_ylabel("Source cluster")
    figure.colorbar(image, ax=axis, fraction=0.046, pad=0.04, label="z-score")
    figure.tight_layout()
    return _save_plot_figure(axis, figure_path, dpi=dpi)


def run_pipeline(
    *,
    output_dir: str | Path,
    figure_path: str | Path,
    summary_path: str | Path,
    interaction_figure_path: str | Path | None = None,
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
    interaction_figure = Path(interaction_figure_path).resolve() if interaction_figure_path else None
    raw_data = Path(raw_data_path).resolve() if raw_data_path else None
    output_dir.mkdir(parents=True, exist_ok=True)
    figure_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    if interaction_figure is not None:
        interaction_figure.parent.mkdir(parents=True, exist_ok=True)
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
    figure_width, figure_height = _save_plot_figure(axes, figure_path, dpi=int(cfg["dpi"]))

    interaction_summary: dict[str, Any] = {}
    interaction_width = 0
    interaction_height = 0
    if bool(cfg.get("compute_nhood_enrichment", False)):
        sq.gr.nhood_enrichment(adata, cluster_key=color_by)
        interaction_summary = _summarize_nhood_enrichment(
            adata,
            cluster_key=color_by,
            top_n=int(cfg.get("interaction_top_n", 5)),
        )
        if interaction_figure is not None:
            interaction_width, interaction_height = _render_nhood_enrichment_heatmap(
                interaction_summary,
                interaction_figure,
                dpi=int(cfg["dpi"]),
            )

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
        "interaction_figure_path": str(interaction_figure) if interaction_figure is not None else "",
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
        "figure_width": figure_width,
        "figure_height": figure_height,
        "has_nhood_enrichment": bool(interaction_summary),
        "interaction_figure_width": interaction_width,
        "interaction_figure_height": interaction_height,
        "nhood_enrichment_summary": interaction_summary,
        "analysis_config": cfg,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Squidpy Visium spatial case-study pipeline.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--figure-path", required=True)
    parser.add_argument("--summary-path", required=True)
    parser.add_argument("--interaction-figure-path", default="")
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
        interaction_figure_path=args.interaction_figure_path or None,
        raw_data_path=args.raw_data_path or None,
        selected_repo=args.selected_repo,
        config=config,
        figure_title=args.title,
    )
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
