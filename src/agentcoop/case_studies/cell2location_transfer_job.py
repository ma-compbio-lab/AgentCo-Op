from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
import re
from typing import Any, Mapping

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from agentcoop.case_studies.job_utils import ensure_output_dir, load_job_request, write_job_result
from agentcoop.case_studies.spatial_panel_common import load_visium_slide, resolve_mouse_brain_assets, write_json


def _clean_factor_name(name: str) -> str:
    text = str(name).strip()
    text = re.sub(r"^annotation_[^_]+_", "", text)
    text = re.sub(r"^means_cov_effect_annotation_[^_]+_", "", text)
    return text


def _extract_regression_mod_signatures(adata_ref: Any) -> pd.DataFrame | None:
    regression_mod = adata_ref.uns.get("regression_mod", {})
    if not isinstance(regression_mod, Mapping):
        return None
    raw_fact_names = regression_mod.get("fact_names")
    post_sample_means = regression_mod.get("post_sample_means")
    if raw_fact_names is None or not isinstance(post_sample_means, Mapping):
        return None
    gene_factors = post_sample_means.get("gene_factors")
    if gene_factors is None:
        return None

    fact_names = [str(name) for name in list(raw_fact_names)]
    matrix = np.asarray(gene_factors)
    if matrix.ndim != 2 or not fact_names:
        return None
    if matrix.shape[0] != len(fact_names) and matrix.shape[1] == len(fact_names):
        matrix = matrix.T
    if matrix.shape[0] != len(fact_names):
        return None

    selected = [idx for idx, name in enumerate(fact_names) if not str(name).startswith("sample_")]
    if not selected:
        selected = list(range(len(fact_names)))
    selected_names = [_clean_factor_name(fact_names[idx]) for idx in selected]
    raw_var_names = regression_mod.get("var_names")
    var_names = list(raw_var_names) if raw_var_names is not None else []
    if len(var_names) != matrix.shape[1]:
        var_names = list(getattr(adata_ref, "var_names", []))
    if len(var_names) != matrix.shape[1]:
        return None

    inf_aver = pd.DataFrame(matrix[selected, :].T, index=pd.Index([str(v) for v in var_names]), columns=selected_names)
    sample_scaling = np.asarray(post_sample_means.get("sample_scaling")) if post_sample_means.get("sample_scaling") is not None else None
    if sample_scaling is not None and sample_scaling.size:
        inf_aver = inf_aver * float(np.asarray(sample_scaling).mean())
    inf_aver = inf_aver.loc[:, ~inf_aver.columns.duplicated()]
    return inf_aver


def _extract_raw_signature_columns(adata_ref: Any) -> pd.DataFrame | None:
    raw = getattr(adata_ref, "raw", None)
    raw_var = getattr(raw, "var", None)
    if raw_var is None or not hasattr(raw_var, "columns"):
        return None
    columns = [str(col) for col in raw_var.columns if str(col).startswith("means_cov_effect_")]
    if not columns:
        return None
    inf_aver = raw_var.loc[:, columns].copy()
    inf_aver.columns = [_clean_factor_name(column) for column in columns]
    inf_aver = inf_aver.loc[:, ~inf_aver.columns.duplicated()]
    sample_scaling = None
    regression_mod = adata_ref.uns.get("regression_mod", {})
    if isinstance(regression_mod, Mapping):
        post_sample_means = regression_mod.get("post_sample_means")
        if isinstance(post_sample_means, Mapping) and post_sample_means.get("sample_scaling") is not None:
            sample_scaling = np.asarray(post_sample_means.get("sample_scaling"))
    if sample_scaling is not None and sample_scaling.size:
        inf_aver = inf_aver * float(np.asarray(sample_scaling).mean())
    inf_aver.index = pd.Index([str(idx) for idx in inf_aver.index])
    return inf_aver


def _extract_reference_signatures(adata_ref: Any) -> pd.DataFrame:
    factor_names = list(adata_ref.uns.get("mod", {}).get("factor_names", []))
    if not factor_names:
        regression_signatures = _extract_regression_mod_signatures(adata_ref)
        if regression_signatures is not None and not regression_signatures.empty:
            return regression_signatures
        raw_signatures = _extract_raw_signature_columns(adata_ref)
        if raw_signatures is not None and not raw_signatures.empty:
            return raw_signatures
        raise RuntimeError(
            "reference signatures h5ad is missing both adata.uns['mod']['factor_names'] and supported "
            "regression/raw signature structures"
        )
    if "means_per_cluster_mu_fg" in getattr(adata_ref, "varm", {}):
        inf_aver = adata_ref.varm["means_per_cluster_mu_fg"][[f"means_per_cluster_mu_fg_{name}" for name in factor_names]].copy()
    else:
        columns = [f"means_per_cluster_mu_fg_{name}" for name in factor_names]
        inf_aver = adata_ref.var[columns].copy()
    inf_aver.columns = factor_names
    inf_aver.index = adata_ref.var_names.astype(str)
    return inf_aver


def _top_cell_types(adata_vis: Any, top_n: int = 6) -> list[str]:
    abundance_key = _resolve_abundance_key(adata_vis)
    abundance = adata_vis.obsm[abundance_key]
    if hasattr(abundance, "mean"):
        means = np.asarray(abundance.mean(axis=0)).ravel()
    else:
        means = np.mean(np.asarray(abundance), axis=0)
    factor_names = list(getattr(abundance, "columns", [])) or list(adata_vis.uns.get("mod", {}).get("factor_names", []))
    ranked = sorted(zip(factor_names, means), key=lambda item: float(item[1]), reverse=True)
    return [name for name, _ in ranked[:top_n]]


def _align_spatial_and_reference_genes(adata_vis: Any, cell_state_df: pd.DataFrame) -> tuple[Any, pd.DataFrame, int]:
    reference_index = pd.Index([str(idx) for idx in cell_state_df.index])
    shared_by_name = np.intersect1d(np.asarray(adata_vis.var_names, dtype=str), reference_index.to_numpy(dtype=str))
    if shared_by_name.size > 0:
        aligned = adata_vis[:, shared_by_name].copy()
        return aligned, cell_state_df.loc[shared_by_name, :].copy(), int(shared_by_name.size)

    gene_ids = None
    if hasattr(adata_vis, "var") and "gene_ids" in getattr(adata_vis, "var", {}):
        gene_ids = adata_vis.var["gene_ids"].astype(str)
    if gene_ids is not None:
        mask = gene_ids.isin(reference_index)
        shared_gene_ids = gene_ids.loc[mask]
        if int(mask.sum()) > 0:
            aligned = adata_vis[:, mask.to_numpy()].copy()
            aligned.var["symbol_var_name"] = np.asarray(aligned.var_names, dtype=str)
            aligned.var_names = pd.Index(shared_gene_ids.tolist())
            aligned.var_names_make_unique()
            intersect = np.intersect1d(np.asarray(aligned.var_names, dtype=str), reference_index.to_numpy(dtype=str))
            if intersect.size > 0:
                aligned = aligned[:, intersect].copy()
                return aligned, cell_state_df.loc[intersect, :].copy(), int(intersect.size)

    return adata_vis, cell_state_df, int(shared_by_name.size)


def _resolve_abundance_key(adata_vis: Any) -> str:
    for key in ("q05_cell_abundance_w_sf", "means_cell_abundance_w_sf", "q50_cell_abundance_w_sf"):
        if key in getattr(adata_vis, "obsm", {}):
            return key
    raise RuntimeError("cell2location posterior export did not produce a usable cell_abundance_w_sf matrix")


def _render_training_free_figures(adata_vis: Any, output_dir: Path, *, slide_id: str) -> dict[str, str]:
    import scanpy as sc

    abundance_key = _resolve_abundance_key(adata_vis)
    abundance = adata_vis.obsm[abundance_key]
    factor_names = list(getattr(abundance, "columns", [])) or list(adata_vis.uns.get("mod", {}).get("factor_names", []))
    for cell_type in factor_names:
        adata_vis.obs[str(cell_type)] = abundance[str(cell_type)]

    top_types = _top_cell_types(adata_vis, top_n=6)
    abundance_path = output_dir / "cell_abundance_panel.png"
    sc.pl.spatial(adata_vis, color=top_types, spot_size=1.1, ncols=3, show=False)
    fig = plt.gcf()
    fig.suptitle(f"cell2location abundance maps: {slide_id}", y=1.02)
    fig.savefig(abundance_path, dpi=160, bbox_inches="tight")
    plt.close(fig)

    sc.pp.neighbors(adata_vis, use_rep=abundance_key, n_neighbors=min(15, max(3, adata_vis.n_obs - 1)))
    sc.tl.leiden(adata_vis, resolution=1.0, key_added="region_cluster", flavor="igraph", n_iterations=2, directed=False)
    leiden_path = output_dir / "leiden_regions.png"
    sc.pl.spatial(adata_vis, color="region_cluster", spot_size=1.1, show=False)
    fig = plt.gcf()
    fig.suptitle(f"cell2location Leiden regions: {slide_id}", y=1.02)
    fig.savefig(leiden_path, dpi=160, bbox_inches="tight")
    plt.close(fig)

    if "total_counts" not in getattr(adata_vis, "obs", {}):
        matrix = getattr(adata_vis, "X", None)
        if matrix is None:
            raise RuntimeError("cell2location figure rendering requires adata_vis.X or adata_vis.obs['total_counts']")
        try:
            from scipy import sparse  # type: ignore

            total_counts = np.asarray(matrix.sum(axis=1)).ravel() if sparse.issparse(matrix) else np.asarray(matrix).sum(axis=1)
        except Exception:
            total_counts = np.asarray(matrix).sum(axis=1)
        adata_vis.obs["total_counts"] = np.asarray(total_counts, dtype=float)

    qc_path = output_dir / "qc_spatial.png"
    sc.pl.spatial(adata_vis, color="total_counts", spot_size=1.1, show=False)
    fig = plt.gcf()
    fig.suptitle(f"Visium QC counts: {slide_id}", y=1.02)
    fig.savefig(qc_path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return {
        "cell_abundance_panel_path": str(abundance_path),
        "leiden_regions_path": str(leiden_path),
        "qc_spatial_path": str(qc_path),
    }


def _render_nmf_compartments(adata_vis: Any, output_dir: Path, *, slide_id: str, n_components: int = 4) -> dict[str, Any]:
    from sklearn.decomposition import NMF

    abundance_key = _resolve_abundance_key(adata_vis)
    abundance = adata_vis.obsm[abundance_key]
    factor_names = list(getattr(abundance, "columns", [])) or list(adata_vis.uns.get("mod", {}).get("factor_names", []))
    matrix = np.asarray(abundance.values if hasattr(abundance, "values") else abundance, dtype=float)
    matrix = np.clip(matrix, 0.0, None)
    component_count = max(2, min(int(n_components), matrix.shape[0], matrix.shape[1]))
    if component_count < 2:
        raise RuntimeError("cell2location NMF rendering requires at least 2 usable factors")

    nmf = NMF(n_components=component_count, init="nndsvda", random_state=0, max_iter=500)
    W = nmf.fit_transform(matrix)
    H = nmf.components_
    dominant = np.argmax(W, axis=1).astype(int)
    adata_vis.obs["nmf_compartment"] = [f"compartment_{index}" for index in dominant.tolist()]

    import scanpy as sc

    compartments_path = output_dir / "nmf_compartments.png"
    sc.pl.spatial(adata_vis, color="nmf_compartment", spot_size=1.1, show=False)
    fig = plt.gcf()
    fig.suptitle(f"cell2location NMF compartments: {slide_id}", y=1.02)
    fig.savefig(compartments_path, dpi=160, bbox_inches="tight")
    plt.close(fig)

    rows: list[dict[str, Any]] = []
    for idx in range(component_count):
        ranking = np.argsort(H[idx])[::-1]
        top_cell_types = [str(factor_names[position]) for position in ranking[: min(6, len(ranking))]]
        rows.append(
            {
                "component": f"compartment_{idx}",
                "top_cell_types": top_cell_types,
                "mean_weight": float(np.mean(W[:, idx])),
            }
        )
    nmf_summary = {
        "component_count": component_count,
        "components": rows,
        "reconstruction_err": float(nmf.reconstruction_err_),
        "nmf_compartments_path": str(compartments_path),
    }
    summary_path = output_dir / "nmf_compartments_summary.json"
    summary_path.write_text(json.dumps(nmf_summary, ensure_ascii=True, indent=2), encoding="utf-8")
    return {
        "nmf_compartments_path": str(compartments_path),
        "nmf_summary_path": str(summary_path),
        "nmf_component_count": component_count,
        "nmf_reconstruction_err": float(nmf.reconstruction_err_),
    }


def _train_cell2location_model(model: Any, *, max_epochs: int) -> None:
    train_kwargs = {"max_epochs": max_epochs, "train_size": 1.0, "batch_size": None}
    try:
        model.train(use_gpu=False, **train_kwargs)
        return
    except TypeError as exc:
        if "use_gpu" not in str(exc):
            raise
    model.train(accelerator="cpu", **train_kwargs)


def _export_cell2location_posterior(model: Any, adata_vis: Any, *, num_samples: int) -> Any:
    sample_kwargs = {"num_samples": num_samples, "batch_size": min(adata_vis.n_obs, 256), "use_gpu": False}
    attempts = [
        {"sample_kwargs": dict(sample_kwargs), "add_to_obsm": ["q05", "q50"], "use_quantiles": True},
        {
            "sample_kwargs": {key: value for key, value in sample_kwargs.items() if key != "use_gpu"},
            "add_to_obsm": ["q05", "q50"],
            "use_quantiles": True,
        },
        {
            "sample_kwargs": {key: value for key, value in sample_kwargs.items() if key != "use_gpu"},
            "add_to_obsm": ["means"],
            "use_quantiles": False,
        },
    ]
    last_error: Exception | None = None
    for attempt in attempts:
        try:
            return model.export_posterior(
                adata_vis,
                sample_kwargs=attempt["sample_kwargs"],
                add_to_obsm=attempt["add_to_obsm"],
                use_quantiles=attempt["use_quantiles"],
            )
        except TypeError as exc:
            last_error = exc
            message = str(exc)
            if "use_gpu" in message or "num_samples" in message:
                continue
            raise
    if last_error is not None:
        raise last_error
    raise RuntimeError("cell2location posterior export failed without an exception")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="cell2location repo-transfer job on a held-out mouse brain slide.")
    parser.add_argument("--input-json", required=True)
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args(argv)

    _, inputs, _, hints = load_job_request(args.input_json)
    output_dir = ensure_output_dir(hints, args.output_json)
    assets = resolve_mouse_brain_assets(hints)

    analysis_config = dict(inputs.get("analysis_config", {})) if isinstance(inputs.get("analysis_config", {}), Mapping) else {}
    selected_repo = str(inputs.get("selected_repo", "cell2location")).strip() or "cell2location"
    holdout_slide = str(analysis_config.get("holdout_slide", hints.get("input_assets", {}).get("holdout_slide", "ST8059050"))).strip() or "ST8059050"
    n_cells_per_location = float(analysis_config.get("N_cells_per_location", 8.0))
    detection_alpha = float(analysis_config.get("detection_alpha", 20.0))
    max_epochs = int(analysis_config.get("max_epochs", 120))
    num_samples = int(analysis_config.get("num_posterior_samples", 50))

    import cell2location
    import scanpy as sc
    from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score

    reference_signatures = sc.read_h5ad(assets["reference_signatures_h5ad_path"])
    cell_state_df = _extract_reference_signatures(reference_signatures)
    slide_dir = Path(assets["spatial_root"]) / "rawdata" / holdout_slide
    manual_layer_paths = dict(assets.get("manual_layer_paths", {}))
    adata_vis = load_visium_slide(slide_dir, sample_id=holdout_slide, manual_label_path=manual_layer_paths.get(holdout_slide))
    adata_vis.var_names = adata_vis.var_names.astype(str)

    adata_vis, cell_state_df, shared_gene_count = _align_spatial_and_reference_genes(adata_vis, cell_state_df)
    if shared_gene_count < 200:
        raise RuntimeError(f"Too few shared genes for cell2location transfer: {shared_gene_count}")

    cell2location.models.Cell2location.setup_anndata(adata=adata_vis, batch_key="sample")
    model = cell2location.models.Cell2location(
        adata_vis,
        cell_state_df=cell_state_df,
        N_cells_per_location=n_cells_per_location,
        detection_alpha=detection_alpha,
    )

    started = time.perf_counter()
    _train_cell2location_model(model, max_epochs=max_epochs)
    adata_vis = _export_cell2location_posterior(model, adata_vis, num_samples=num_samples)
    runtime_s = float(time.perf_counter() - started)

    figure_paths = _render_training_free_figures(adata_vis, output_dir, slide_id=holdout_slide)
    nmf_outputs = _render_nmf_compartments(
        adata_vis,
        output_dir,
        slide_id=holdout_slide,
        n_components=int(analysis_config.get("nmf_components", 4)),
    )
    mapped_path = output_dir / "sp.h5ad"
    adata_vis.write_h5ad(mapped_path)

    ari = 0.0
    nmi = 0.0
    if "manual_layer" in adata_vis.obs:
        mask = adata_vis.obs["manual_layer"].fillna("").astype(str) != ""
        if int(mask.sum()) >= 10 and adata_vis.obs.loc[mask, "manual_layer"].nunique() >= 2 and adata_vis.obs.loc[mask, "region_cluster"].nunique() >= 2:
            ari = float(adjusted_rand_score(adata_vis.obs.loc[mask, "manual_layer"].astype(str), adata_vis.obs.loc[mask, "region_cluster"].astype(str)))
            nmi = float(normalized_mutual_info_score(adata_vis.obs.loc[mask, "manual_layer"].astype(str), adata_vis.obs.loc[mask, "region_cluster"].astype(str)))

    mapping_summary = {
        "selected_repo": selected_repo,
        "heldout_slide": holdout_slide,
        "reference_source": assets["reference_signatures_h5ad_path"],
        "shared_gene_count": shared_gene_count,
        "cell_type_count": int(cell_state_df.shape[1]),
        "N_cells_per_location": n_cells_per_location,
        "detection_alpha": detection_alpha,
        "max_epochs": max_epochs,
        "num_posterior_samples": num_samples,
        "runtime_s": runtime_s,
        "ari_manual_layer_vs_region_cluster": ari,
        "nmi_manual_layer_vs_region_cluster": nmi,
        **figure_paths,
        **nmf_outputs,
        "mapped_h5ad_path": str(mapped_path),
    }
    summary_path = Path(write_json(mapping_summary, output_dir / "mapping_summary.json"))
    report_path = output_dir / "transfer_report.md"
    report_path.write_text(
        "\n".join(
            [
                "# cell2location Repo Transfer",
                "",
                f"- Repo: `{selected_repo}`",
                f"- Held-out slide: `{holdout_slide}`",
                f"- Shared genes: `{shared_gene_count}`",
                f"- Cell types in reference: `{int(cell_state_df.shape[1])}`",
                f"- Runtime (s): `{runtime_s:.2f}`",
                f"- N_cells_per_location: `{n_cells_per_location}`",
                f"- detection_alpha: `{detection_alpha}`",
                f"- Manual-layer ARI: `{ari:.4f}`",
                f"- Manual-layer NMI: `{nmi:.4f}`",
                "",
                "## Transfer Notes",
                "- This run uses the official cell2location repo and the official precomputed mouse-brain reference signatures.",
                "- The transfer target is a held-out Visium mouse-brain slide not used in the short demo headline pair.",
                "- The implementation deliberately uses reduced CPU-friendly training and posterior-export settings, but still restores a downstream NMF compartment view.",
                "",
                "## Artifacts",
                f"- mapped AnnData: `{mapped_path}`",
                f"- mapping summary: `{summary_path}`",
                f"- cell abundance panel: `{figure_paths['cell_abundance_panel_path']}`",
                f"- Leiden regions: `{figure_paths['leiden_regions_path']}`",
                f"- NMF compartments: `{nmf_outputs['nmf_compartments_path']}`",
                f"- QC spatial: `{figure_paths['qc_spatial_path']}`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    outputs = {
        "selected_repo": selected_repo,
        "heldout_slide": holdout_slide,
        "mapping_summary_path": str(summary_path),
        "transfer_report_path": str(report_path),
        "mapped_h5ad_path": str(mapped_path),
        "shared_gene_count": shared_gene_count,
        "ari": ari,
        "nmi": nmi,
        "nmf_compartments_path": str(nmf_outputs["nmf_compartments_path"]),
        "nmf_summary_path": str(nmf_outputs["nmf_summary_path"]),
        "summary": "cell2location repo transfer completed.",
    }
    outputs.update(figure_paths)
    outputs.update(
        {
            "nmf_compartments_path": str(nmf_outputs["nmf_compartments_path"]),
            "nmf_summary_path": str(nmf_outputs["nmf_summary_path"]),
        }
    )
    artifacts = [
        {"path": str(mapped_path), "mime": "application/x-hdf5"},
        {"path": str(summary_path), "mime": "application/json"},
        {"path": str(report_path), "mime": "text/markdown"},
        {"path": figure_paths["cell_abundance_panel_path"], "mime": "image/png"},
        {"path": figure_paths["leiden_regions_path"], "mime": "image/png"},
        {"path": str(nmf_outputs["nmf_compartments_path"]), "mime": "image/png"},
        {"path": str(nmf_outputs["nmf_summary_path"]), "mime": "application/json"},
        {"path": figure_paths["qc_spatial_path"], "mime": "image/png"},
    ]
    trace = {
        "cell2location_transfer": True,
        "selected_repo": selected_repo,
        "heldout_slide": holdout_slide,
        "runtime_s": runtime_s,
    }
    write_job_result(
        output_json=args.output_json,
        outputs=outputs,
        summary={
            "mapping_summary_path": str(summary_path),
            "transfer_report_path": str(report_path),
            **figure_paths,
            "nmf_compartments_path": str(nmf_outputs["nmf_compartments_path"]),
            "nmf_summary_path": str(nmf_outputs["nmf_summary_path"]),
        },
        artifacts=artifacts,
        trace=trace,
        confidence=min(0.95, max(0.5, 0.55 + 0.2 * float(ari + nmi))),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
