from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd


def _preferred_gene_names(adata: Any) -> pd.Index:
    var = getattr(adata, "var", None)
    if isinstance(var, pd.DataFrame):
        for column in ["SYMBOL", "symbol", "gene_symbol", "gene_symbols", "feature_name", "gene_name"]:
            if column in var.columns:
                values = var[column].astype(str).fillna("").str.strip()
                names = [
                    value if value and value.lower() != "nan" else str(fallback)
                    for value, fallback in zip(values.tolist(), list(adata.var_names))
                ]
                return pd.Index(names)
    return pd.Index([str(item) for item in adata.var_names])


def _build_feature_alias_lookup(adata: Any) -> dict[str, str]:
    lookup: dict[str, str] = {}
    var = getattr(adata, "var", None)
    alias_columns: list[str] = []
    if isinstance(var, pd.DataFrame):
        alias_columns = [
            column
            for column in ["gene_ids", "SYMBOL", "symbol", "gene_symbol", "gene_symbols", "feature_name", "gene_name"]
            if column in var.columns
        ]

    for index, var_name in enumerate([str(item) for item in adata.var_names]):
        aliases = {var_name}
        if isinstance(var, pd.DataFrame):
            for column in alias_columns:
                value = str(var.iloc[index][column]).strip()
                if value and value.lower() != "nan":
                    aliases.add(value)
        for alias in aliases:
            lookup.setdefault(alias.upper(), var_name)
    return lookup


def resolve_mouse_brain_assets(hints: Mapping[str, Any]) -> dict[str, Any]:
    input_assets = dict(hints.get("input_assets", {})) if isinstance(hints.get("input_assets", {}), Mapping) else {}
    bundle = input_assets.get("cell2location_mouse_brain_assets", {})
    if not isinstance(bundle, Mapping) or not bundle:
        raise RuntimeError("cell2location_mouse_brain_assets are required in task.hints.input_assets")
    return dict(bundle)


def choose_batch_column(obs: pd.DataFrame) -> str:
    for candidate in ["Sample", "sample", "method", "Method", "batch", "Batch", "dataset", "technology"]:
        if candidate in obs.columns:
            return candidate
    pseudo = pd.Series(
        np.where(np.arange(obs.shape[0]) % 2 == 0, "pseudo_batch_a", "pseudo_batch_b"),
        index=obs.index,
        name="pseudo_batch",
    )
    obs["pseudo_batch"] = pseudo.astype(str)
    return "pseudo_batch"


def load_reference_with_labels(raw_h5ad_path: str | Path, labels_csv_path: str | Path) -> tuple[Any, str, str]:
    import scanpy as sc

    adata = sc.read_h5ad(str(raw_h5ad_path))
    labels = pd.read_csv(labels_csv_path, index_col=0)
    annotation_col = "annotation_1" if "annotation_1" in labels.columns else str(labels.columns[0])
    shared = adata.obs_names.intersection(labels.index)
    if shared.empty:
        raise RuntimeError("reference h5ad and labels csv do not share observation IDs")
    adata = adata[shared].copy()
    adata.obs["cell_label"] = labels.loc[adata.obs_names, annotation_col].astype(str).values
    adata.obs["cell_label"] = adata.obs["cell_label"].replace({"nan": "unknown"}).fillna("unknown")
    batch_col = choose_batch_column(adata.obs)
    return adata, batch_col, annotation_col


def build_panel_design(
    adata: Any,
    *,
    label_col: str,
    batch_col: str,
    panel_size: int,
    backup_gene_count: int,
    excluded_genes: Sequence[str] | None = None,
    preferred_genes: Sequence[str] | None = None,
    min_cells_per_label: int = 25,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    from scipy import sparse

    excluded = {str(g).upper() for g in (excluded_genes or []) if str(g).strip()}
    counts_by_label = adata.obs[label_col].astype(str).value_counts()
    kept_labels = counts_by_label[counts_by_label >= min_cells_per_label].index.tolist()
    adata = adata[adata.obs[label_col].astype(str).isin(kept_labels)].copy()

    if sparse.issparse(adata.X):
        X = adata.X.tocsr().astype(np.float32)
    else:
        X = np.asarray(adata.X, dtype=np.float32)

    var_names = _preferred_gene_names(adata)
    label_series = adata.obs[label_col].astype(str)
    batch_series = adata.obs[batch_col].astype(str)

    label_means: dict[str, np.ndarray] = {}
    label_batch_support: dict[str, np.ndarray] = {}
    global_mean = np.asarray(X.mean(axis=0)).ravel()

    for label in kept_labels:
        mask = label_series == label
        label_matrix = X[mask.values]
        label_means[label] = np.asarray(label_matrix.mean(axis=0)).ravel()

        support = np.zeros(var_names.shape[0], dtype=np.float32)
        batches = batch_series[mask].unique().tolist()
        for batch in batches:
            batch_mask = mask & (batch_series == batch)
            batch_matrix = X[batch_mask.values]
            batch_mean = np.asarray(batch_matrix.mean(axis=0)).ravel()
            support += (batch_mean > 0).astype(np.float32)
        if batches:
            support /= float(len(batches))
        label_batch_support[label] = support

    records: list[dict[str, Any]] = []
    mean_stack = np.vstack([label_means[label] for label in kept_labels])
    for label_index, label in enumerate(kept_labels):
        this_mean = mean_stack[label_index]
        other_mean = np.max(np.delete(mean_stack, label_index, axis=0), axis=0) if len(kept_labels) > 1 else np.zeros_like(this_mean)
        specificity = np.log1p(this_mean) - np.log1p(other_mean + 1e-8)
        stability = label_batch_support[label]
        score = specificity + 0.25 * np.log1p(this_mean + 1e-8) + 0.35 * stability
        for gene_idx, gene in enumerate(var_names):
            gene_name = str(gene)
            if gene_name.upper() in excluded:
                continue
            records.append(
                {
                    "gene": gene_name,
                    "target_label": label,
                    "mean_in_label": float(this_mean[gene_idx]),
                    "max_other_mean": float(other_mean[gene_idx]),
                    "global_mean": float(global_mean[gene_idx]),
                    "specificity_score": float(specificity[gene_idx]),
                    "batch_support": float(stability[gene_idx]),
                    "design_score": float(score[gene_idx]),
                }
            )

    candidates = pd.DataFrame.from_records(records)
    if candidates.empty:
        raise RuntimeError("panel design produced no candidate markers")
    candidates = candidates.sort_values(["design_score", "specificity_score", "mean_in_label"], ascending=False).reset_index(drop=True)
    candidates = candidates.drop_duplicates(subset=["gene"], keep="first").reset_index(drop=True)

    selected_rows: list[pd.Series] = []
    selected_genes: set[str] = set()
    for label in kept_labels:
        label_rows = candidates[candidates["target_label"] == label]
        for _, row in label_rows.iterrows():
            gene = str(row["gene"])
            if gene in selected_genes:
                continue
            selected_rows.append(row)
            selected_genes.add(gene)
            break
        if len(selected_rows) >= panel_size:
            break

    if len(selected_rows) < panel_size:
        for _, row in candidates.iterrows():
            gene = str(row["gene"])
            if gene in selected_genes:
                continue
            selected_rows.append(row)
            selected_genes.add(gene)
            if len(selected_rows) >= panel_size:
                break

    panel_df = pd.DataFrame(selected_rows).reset_index(drop=True)
    preferred = [str(gene).strip() for gene in (preferred_genes or []) if str(gene).strip()]
    if preferred and not panel_df.empty:
        preferred_lookup = {gene.upper(): gene for gene in preferred}
        selected_lookup = {str(gene).upper() for gene in panel_df["gene"].astype(str)}
        for preferred_gene_upper in preferred_lookup:
            if preferred_gene_upper in selected_lookup:
                continue
            preferred_rows = candidates[candidates["gene"].astype(str).str.upper() == preferred_gene_upper]
            if preferred_rows.empty:
                continue
            replacement_row = preferred_rows.iloc[0]
            nonpreferred = panel_df[~panel_df["gene"].astype(str).str.upper().isin(preferred_lookup)]
            if nonpreferred.empty:
                continue
            replace_idx = nonpreferred["design_score"].astype(float).idxmin()
            panel_df.loc[replace_idx] = replacement_row
            selected_lookup = {str(gene).upper() for gene in panel_df["gene"].astype(str)}
        panel_df = panel_df.sort_values(["design_score", "specificity_score", "mean_in_label"], ascending=False).reset_index(drop=True)

    backup_df = candidates[~candidates["gene"].isin(panel_df["gene"])].head(max(backup_gene_count, 0)).reset_index(drop=True)
    panel_df = _annotate_probe_feasibility(panel_df)
    backup_df = _annotate_probe_feasibility(backup_df)
    summary = {
        "panel_size_requested": int(panel_size),
        "panel_size_actual": int(panel_df.shape[0]),
        "backup_gene_count": int(backup_df.shape[0]),
        "label_count": int(len(kept_labels)),
        "labels_covered": sorted(panel_df["target_label"].astype(str).unique().tolist()),
        "batch_column": batch_col,
        "excluded_gene_count": int(len(excluded)),
        "gene_identifier_source": "symbol_preferred",
        "probe_pass_count": int(panel_df["probe_feasibility_pass"].sum()) if not panel_df.empty else 0,
        "probe_fail_count": int((~panel_df["probe_feasibility_pass"]).sum()) if not panel_df.empty else 0,
    }
    return panel_df, backup_df, summary


def _annotate_probe_feasibility(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    annotated = df.copy()
    mean_scale = float(max(annotated["mean_in_label"].max(), 1e-6))
    global_scale = float(max(annotated["global_mean"].max(), 1e-6))
    specificity_scale = float(max(abs(float(annotated["specificity_score"].min())), abs(float(annotated["specificity_score"].max())), 1e-6))

    detection_proxy = np.clip(np.log1p(annotated["mean_in_label"].astype(float)) / np.log1p(mean_scale + 1.0), 0.0, 1.0)
    ubiquity_penalty = np.clip(np.log1p(annotated["global_mean"].astype(float)) / np.log1p(global_scale + 1.0), 0.0, 1.0)
    specificity_proxy = np.clip((annotated["specificity_score"].astype(float) / specificity_scale + 1.0) / 2.0, 0.0, 1.0)
    stability_proxy = np.clip(annotated["batch_support"].astype(float), 0.0, 1.0)
    feasibility_score = 0.45 * stability_proxy + 0.30 * detection_proxy + 0.20 * specificity_proxy - 0.10 * ubiquity_penalty
    feasibility_score = np.clip(feasibility_score, 0.0, 1.0)

    reasons: list[str] = []
    feasibility_pass: list[bool] = []
    for _, row in annotated.iterrows():
        row_reasons: list[str] = []
        if float(row.get("batch_support", 0.0)) < 0.35:
            row_reasons.append("low_batch_support")
        if float(row.get("mean_in_label", 0.0)) < 0.05:
            row_reasons.append("low_label_expression")
        if float(row.get("specificity_score", 0.0)) < 0.12:
            row_reasons.append("weak_specificity")
        reason = ";".join(row_reasons)
        reasons.append(reason)
        feasibility_pass.append(not row_reasons)

    annotated["probe_feasibility_score"] = feasibility_score.astype(float)
    annotated["probe_failure_reason"] = reasons
    annotated["probe_feasibility_pass"] = feasibility_pass
    return annotated


def load_visium_slide(slide_dir: str | Path, *, sample_id: str, manual_label_path: str | Path | None = None) -> Any:
    import scanpy as sc

    slide_path = Path(slide_dir).resolve()
    adata = sc.read_visium(str(slide_path), count_file="filtered_feature_bc_matrix.h5")
    adata.var_names_make_unique()
    adata.obs["sample"] = sample_id
    if manual_label_path:
        labels = pd.read_csv(manual_label_path)
        barcode_col = "Barcode" if "Barcode" in labels.columns else str(labels.columns[0])
        label_col = "SSp" if "SSp" in labels.columns else str(labels.columns[-1])
        labels = labels[[barcode_col, label_col]].rename(columns={barcode_col: "barcode", label_col: "manual_layer"})
        labels["barcode"] = labels["barcode"].astype(str)
        labels["manual_layer"] = labels["manual_layer"].fillna("").astype(str)
        adata.obs = adata.obs.join(labels.set_index("barcode"), how="left")
        adata.obs["manual_layer"] = adata.obs["manual_layer"].fillna("").astype(str)
    return adata


def validate_panel_on_slide(
    adata: Any,
    *,
    panel_genes: Sequence[str],
    backup_genes: Sequence[str],
    resolution: float = 0.65,
    seed: int = 0,
) -> dict[str, Any]:
    import scanpy as sc
    from scipy import sparse
    from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score

    requested_panel = [str(gene) for gene in panel_genes if str(gene).strip()]
    backup = [str(gene) for gene in backup_genes if str(gene).strip()]
    alias_lookup = _build_feature_alias_lookup(adata)
    shared_pairs: list[tuple[str, str]] = []
    missing_genes: list[str] = []
    seen_actual: set[str] = set()
    for gene in requested_panel:
        actual = alias_lookup.get(gene.upper())
        if actual:
            if actual not in seen_actual:
                shared_pairs.append((gene, actual))
                seen_actual.add(actual)
            continue
        missing_genes.append(gene)
    shared_requested_genes = [requested for requested, _ in shared_pairs]
    shared_actual_genes = [actual for _, actual in shared_pairs]

    gene_detection: list[dict[str, Any]] = []
    if shared_actual_genes:
        X = adata[:, shared_actual_genes].X
        if sparse.issparse(X):
            detected = np.asarray((X > 0).sum(axis=0)).ravel()
            mean_expr = np.asarray(X.mean(axis=0)).ravel()
        else:
            dense = np.asarray(X, dtype=np.float32)
            detected = (dense > 0).sum(axis=0)
            mean_expr = dense.mean(axis=0)
        n_obs = max(int(adata.n_obs), 1)
        gene_detection = [
            {
                "gene": requested_gene,
                "matched_var_name": actual_gene,
                "spot_detection_rate": float(detected[idx] / float(n_obs)),
                "mean_expression": float(mean_expr[idx]),
            }
            for idx, (requested_gene, actual_gene) in enumerate(shared_pairs)
        ]
    detection_lookup = {item["gene"]: item for item in gene_detection}
    proxy_failed = [
        gene
        for gene in shared_requested_genes
        if detection_lookup.get(gene, {}).get("spot_detection_rate", 0.0) < 0.01
        or detection_lookup.get(gene, {}).get("mean_expression", 0.0) <= 0.0
    ]
    failed_genes = sorted(set(missing_genes + proxy_failed))
    usable_pairs = [(requested, actual) for requested, actual in shared_pairs if requested not in failed_genes]
    usable_requested_genes = [requested for requested, _ in usable_pairs]
    usable_actual_genes = [actual for _, actual in usable_pairs]

    analysis = adata[:, usable_actual_genes].copy() if usable_actual_genes else adata[:, shared_actual_genes].copy()
    if analysis.n_vars >= 2 and analysis.n_obs >= 5:
        sc.pp.normalize_total(analysis, target_sum=1e4)
        sc.pp.log1p(analysis)
        sc.pp.scale(analysis, max_value=10.0)
        n_comps = max(2, min(20, int(analysis.n_vars), int(analysis.n_obs) - 1))
        sc.tl.pca(analysis, svd_solver="arpack", n_comps=n_comps)
        sc.pp.neighbors(analysis, n_neighbors=min(12, max(3, int(analysis.n_obs) - 1)), n_pcs=min(n_comps, 15))
        sc.tl.umap(analysis, random_state=int(seed))
        sc.tl.leiden(
            analysis,
            resolution=float(resolution),
            random_state=int(seed),
            key_added="panel_cluster",
            flavor="igraph",
            n_iterations=2,
            directed=False,
        )
    else:
        analysis.obs["panel_cluster"] = "0"

    ari = 0.0
    nmi = 0.0
    labeled_mask = None
    if "manual_layer" in analysis.obs:
        manual = analysis.obs["manual_layer"].fillna("").astype(str)
        labeled_mask = manual != ""
        if int(labeled_mask.sum()) >= 10 and manual[labeled_mask].nunique() >= 2 and analysis.obs.loc[labeled_mask, "panel_cluster"].nunique() >= 2:
            ari = float(adjusted_rand_score(manual[labeled_mask], analysis.obs.loc[labeled_mask, "panel_cluster"].astype(str)))
            nmi = float(
                normalized_mutual_info_score(
                    manual[labeled_mask],
                    analysis.obs.loc[labeled_mask, "panel_cluster"].astype(str),
                )
            )

    replacement_candidates = [
        gene
        for gene in backup
        if alias_lookup.get(gene.upper()) and gene not in usable_requested_genes
    ][: max(len(failed_genes), 5)]
    score = 0.6 * ari + 0.4 * nmi
    return {
        "analysis_adata": analysis,
        "usable_genes": usable_requested_genes,
        "failed_genes": failed_genes,
        "replacement_candidates": replacement_candidates,
        "ari": float(ari),
        "nmi": float(nmi),
        "validation_score": float(score),
        "gene_detection": gene_detection,
        "requested_panel_size": int(len(requested_panel)),
        "usable_panel_size": int(len(usable_requested_genes)),
        "labeled_spot_count": int(labeled_mask.sum()) if labeled_mask is not None else 0,
    }


def write_table(df: pd.DataFrame, path: str | Path) -> str:
    table_path = Path(path).resolve()
    table_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(table_path, index=False)
    return str(table_path)


def write_json(payload: Mapping[str, Any], path: str | Path) -> str:
    output_path = Path(path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(dict(payload), ensure_ascii=True, indent=2), encoding="utf-8")
    return str(output_path)
