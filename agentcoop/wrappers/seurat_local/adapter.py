"""Seurat local-Python adapter.

Implements the scRNA-seq marker-discovery workflow specified in
`docs/experiments/case_study_2.md` §10 inside the AgentCo-Op host process when Docker
is unavailable. Mirrors the Seurat R wrapper one-for-one but uses
scanpy + numpy + pandas + scipy under the hood:

- read the RNA count TSV (gene × cell, comma-separated cell IDs);
- normalise cell IDs (comma → dot) so they match the celltype.txt
  `rna.bc` column;
- attach the celltype labels, drop excluded labels and tiny cell
  types (`min_cells_per_type`);
- LogNormalize (scale_factor 10 000) + log1p;
- run `scanpy.tl.rank_genes_groups(method="wilcoxon")` per cell type
  (1-vs-rest);
- export the full marker table, per-cell-type top-N JSON, the cell-
  type distribution, and a `result.json` summary.

Returns a response shaped per the §7.1 agent registry.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import scipy.sparse as sp


# ---------------------------------------------------------------------------
# Adapter entrypoint
# ---------------------------------------------------------------------------


def invoke_seurat_local(req: dict[str, Any]) -> dict[str, Any]:
    out_dir = Path(req.get("output_dir", "runs/case2/shareseq_skin/artifacts/seurat_run"))
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "run.log"
    log = _Logger(log_path)

    inputs = req.get("input", {}) or {}
    params = (inputs.get("parameters") or {}) or {}
    dataset = inputs.get("dataset", {}) or {}
    success_targets = inputs.get("success_targets", {}) or {}

    top_n = int(params.get("top_n") or success_targets.get("top_n") or 50)
    min_cells = int(params.get("min_cells_per_type") or 20)
    remove_cts = list(params.get("remove_cell_types") or ["Mixed", "Mix"])
    rna_method = params.get("rna_marker_method") or {}
    test_use = str(rna_method.get("test_use") or "wilcoxon").lower()
    if test_use == "wilcox":
        test_use = "wilcoxon"
    min_pct = float(rna_method.get("min_pct") or 0.10)
    logfc_threshold = float(rna_method.get("logfc_threshold") or 0.25)
    only_pos = bool(rna_method.get("only_pos", True))

    cache_dir = Path(dataset.get("local_cache_dir", "data/shareseq_skin")).expanduser()
    files = dataset.get("files") or {}
    rna_path = _resolve_data_path(cache_dir, files.get("rna_counts"),
                                   "GSM4156608_skin.late.anagen.rna.counts.txt.gz")
    celltype_path = _resolve_data_path(cache_dir, files.get("celltype_labels"),
                                        "GSM4156597_skin_celltype.txt.gz")

    log.info(f"rna_counts:    {rna_path}")
    log.info(f"celltypes:     {celltype_path}")
    log.info(f"top_n={top_n}  min_cells_per_type={min_cells}  remove={remove_cts}")
    log.info(f"test_use={test_use}  min_pct={min_pct}  logfc_threshold={logfc_threshold}")

    warnings: list[str] = []
    try:
        # ---- load RNA counts as sparse genes×cells -----------------------
        gene_names, cell_ids_raw, X_sparse = _read_dense_tsv_to_sparse(rna_path, log)
        cell_ids = [c.replace(",", ".") for c in cell_ids_raw]
        log.info(f"loaded RNA matrix: {X_sparse.shape} (genes × cells)")

        # ---- celltype labels --------------------------------------------
        labels = pd.read_csv(celltype_path, sep="\t", compression="infer")
        log.info(f"celltype labels: {labels.shape} cols={list(labels.columns)}")
        rna_bc_col = _pick_barcode_column(labels, set(cell_ids), prefer=["rna.bc", "rna_bc", "rna_barcode", "rna"])
        ct_col = _pick_celltype_column(labels)
        log.info(f"barcode_col={rna_bc_col}  celltype_col={ct_col}")

        # Build per-cell-id label lookup.
        labels[rna_bc_col] = labels[rna_bc_col].astype(str)
        labels[ct_col] = labels[ct_col].astype(str)
        cell_to_ct = dict(zip(labels[rna_bc_col], labels[ct_col]))

        keep_idx = [i for i, c in enumerate(cell_ids) if c in cell_to_ct]
        if not keep_idx:
            raise RuntimeError("no RNA cells matched any celltype barcode")
        log.info(f"cells with celltype labels: {len(keep_idx)} / {len(cell_ids)}")

        # ---- filter cells by celltype rules ------------------------------
        kept_cells = [cell_ids[i] for i in keep_idx]
        kept_cts = [cell_to_ct[c] for c in kept_cells]
        # Remove excluded celltypes.
        sel = [i for i, ct in enumerate(kept_cts) if ct not in remove_cts]
        keep_idx = [keep_idx[i] for i in sel]
        kept_cells = [kept_cells[i] for i in sel]
        kept_cts = [kept_cts[i] for i in sel]
        # Drop tiny celltypes.
        ct_counts = pd.Series(kept_cts).value_counts()
        valid = set(ct_counts[ct_counts >= min_cells].index.tolist())
        sel = [i for i, ct in enumerate(kept_cts) if ct in valid]
        keep_idx = [keep_idx[i] for i in sel]
        kept_cells = [kept_cells[i] for i in sel]
        kept_cts = [kept_cts[i] for i in sel]
        log.info(f"after filters: {len(kept_cells)} cells × {len(set(kept_cts))} celltypes")

        # Slice sparse matrix to kept cells; transpose to cells × genes.
        X_sub = X_sparse[:, keep_idx].T.tocsr()  # cells × genes

        # ---- build AnnData & run scanpy ----------------------------------
        # We import here so the EnvManager has a chance to install scanpy
        # before we touch it (the wrappers package import side-effects fire
        # before adapters run).
        import anndata as ad  # type: ignore
        import scanpy as sc  # type: ignore

        sc.settings.verbosity = 1
        adata = ad.AnnData(X=X_sub.astype(np.float32))
        adata.obs_names = kept_cells
        adata.var_names = list(gene_names)
        adata.obs["cell_type"] = pd.Categorical(kept_cts)

        log.info("running normalize_total(target_sum=1e4) + log1p ...")
        sc.pp.normalize_total(adata, target_sum=1e4)
        sc.pp.log1p(adata)

        log.info(f"running rank_genes_groups (method={test_use}, 1-vs-rest) ...")
        sc.tl.rank_genes_groups(
            adata, groupby="cell_type", method=test_use, n_genes=adata.shape[1],
        )

        # ---- assemble all-markers table ---------------------------------
        names = adata.uns["rank_genes_groups"]["names"]
        pvals = adata.uns["rank_genes_groups"]["pvals"]
        pvals_adj = adata.uns["rank_genes_groups"]["pvals_adj"]
        logfc = adata.uns["rank_genes_groups"]["logfoldchanges"]
        # `pts` carries per-group expression frequency; available in scanpy ≥ 1.7.
        pts = adata.uns["rank_genes_groups"].get("pts")

        cell_types = list(names.dtype.names)
        all_rows: list[pd.DataFrame] = []
        for ct in cell_types:
            df = pd.DataFrame({
                "gene": [str(g) for g in names[ct]],
                "p_val": pvals[ct],
                "p_val_adj": pvals_adj[ct],
                "avg_log2FC": logfc[ct],
                "cluster": ct,
            })
            if pts is not None and ct in pts.columns:
                df["pct.1"] = df["gene"].map(lambda g: float(pts.loc[g, ct]) if g in pts.index else np.nan)
            df = df.dropna(subset=["p_val_adj"])
            if only_pos:
                df = df[df["avg_log2FC"] > 0]
            df = df[df["avg_log2FC"] >= logfc_threshold]
            df = df.sort_values(["p_val_adj", "avg_log2FC"], ascending=[True, False])
            all_rows.append(df)
        markers = pd.concat(all_rows, ignore_index=True) if all_rows else pd.DataFrame()
        markers_csv = out_dir / "seurat_rna_markers_all.csv"
        markers.to_csv(markers_csv, index=False)
        log.info(f"wrote {markers_csv} ({len(markers)} rows)")

        # Top-N per cell type.
        top: dict[str, list[str]] = {}
        for ct in cell_types:
            sub = markers[markers["cluster"] == ct].head(top_n)
            top[ct] = sub["gene"].astype(str).drop_duplicates().tolist()
        top_path = out_dir / "rna_top_markers_by_celltype.json"
        top_path.write_text(json.dumps(top, indent=2), encoding="utf-8")
        log.info(f"wrote {top_path} (top_n={top_n} per cell type)")

        # Celltype distribution.
        dist = pd.DataFrame({
            "cell_type": list(ct_counts.index),
            "n_cells": ct_counts.values.astype(int),
        }).sort_values("n_cells", ascending=False)
        dist_path = out_dir / "celltype_distribution.csv"
        dist.to_csv(dist_path, index=False)

        result = {
            "status": "success",
            "summary": (
                f"Seurat-equivalent RNA marker discovery on {len(kept_cells)} cells × "
                f"{X_sub.shape[1]} genes; {len(cell_types)} cell types; top_n={top_n}; "
                f"{len(markers)} marker rows total."
            ),
            "warnings": warnings,
            "artifacts": {
                "rna_markers_all_csv": str(markers_csv),
                "rna_top_markers_json": str(top_path),
                "celltype_distribution_csv": str(dist_path),
                "run_log": str(log_path),
            },
            "main_results": {
                "n_cells": int(len(kept_cells)),
                "n_genes": int(X_sub.shape[1]),
                "n_cell_types": int(len(cell_types)),
                "top_n": top_n,
                "n_marker_rows": int(len(markers)),
                "barcode_column": rna_bc_col,
                "celltype_column": ct_col,
            },
        }
        (out_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result
    except Exception as exc:
        log.error(f"adapter failed: {type(exc).__name__}: {exc}")
        return {
            "status": "failed",
            "summary": f"Seurat adapter failed: {type(exc).__name__}: {exc}",
            "artifacts": {"run_log": str(log_path)},
            "warnings": warnings + [str(exc)],
        }


def register() -> None:
    from agentcoop.core.repo_collaboration import register_local_adapter

    register_local_adapter("Seurat")(invoke_seurat_local)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Logger:
    """Tiny dual-output logger (file + stderr)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")

    def _write(self, level: str, msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] [{level}] {msg}\n"
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line)

    def info(self, msg: str) -> None:
        self._write("INFO", msg)

    def error(self, msg: str) -> None:
        self._write("ERROR", msg)


def _resolve_data_path(
    cache_dir: Path,
    declared: str | None,
    canonical_filename: str,
) -> Path:
    """Find the file on disk. Honour a declared filename, then fall back
    to the canonical GEO filename, then a glob in the cache directory."""
    candidates: list[Path] = []
    if declared:
        # The user can declare a URL (CS1 style) or a local relative path.
        if not declared.startswith(("http://", "https://", "ftp://")):
            p = Path(declared)
            if not p.is_absolute():
                p = cache_dir / p
            candidates.append(p)
    candidates.append(cache_dir / canonical_filename)
    # Wildcard fallback in case the user renamed slightly.
    matches = sorted(cache_dir.glob(canonical_filename.replace(".gz", "*")))
    candidates.extend(matches)
    for c in candidates:
        if c.is_file():
            return c
    raise FileNotFoundError(
        f"could not find {canonical_filename} under {cache_dir} (tried {candidates})"
    )


def _read_dense_tsv_to_sparse(
    path: Path,
    log: "_Logger",
    chunk_rows: int = 2000,
) -> tuple[list[str], list[str], sp.csr_matrix]:
    """Stream a gene × cell dense TSV (gzip-compressed) into a sparse
    CSR matrix without ever materialising the full dense block in
    memory. Returns (gene_names, cell_ids, sparse_genes_by_cells).
    """
    open_fn = gzip.open if str(path).endswith(".gz") else open
    with open_fn(path, "rt") as f:
        header = f.readline().rstrip("\n").split("\t")
    gene_col, *cell_ids = header
    log.info(f"  TSV header: gene_col={gene_col!r}, n_cells={len(cell_ids)}")

    data_blocks: list[sp.csr_matrix] = []
    gene_names: list[str] = []
    n_chunks = 0
    for chunk in pd.read_csv(
        path,
        sep="\t",
        header=0,
        chunksize=chunk_rows,
        compression="infer",
        dtype={gene_col: str},
        engine="c",
        memory_map=False,
    ):
        gene_names.extend(chunk[gene_col].astype(str).tolist())
        block = chunk.iloc[:, 1:].to_numpy(dtype=np.float32, copy=False)
        data_blocks.append(sp.csr_matrix(block))
        n_chunks += 1
        if n_chunks % 5 == 0:
            log.info(f"  …read chunk {n_chunks} (rows so far: {len(gene_names)})")
    X = sp.vstack(data_blocks, format="csr") if data_blocks else sp.csr_matrix((0, len(cell_ids)))
    log.info(f"  TSV → sparse: {X.shape} ({X.nnz} nonzeros)")
    return gene_names, cell_ids, X


def _pick_barcode_column(
    labels: pd.DataFrame,
    valid_set: set[str],
    *,
    prefer: Iterable[str],
) -> str:
    """Pick the column in `labels` whose values overlap most with
    `valid_set`. Honour `prefer` for ties."""
    counts: dict[str, int] = {}
    for col in labels.columns:
        try:
            ser = labels[col].astype(str)
        except Exception:
            continue
        counts[col] = int(ser.isin(valid_set).sum())
    # Prefer human-friendly names first.
    for p in prefer:
        if counts.get(p, 0) > 0:
            return p
    if not counts:
        raise RuntimeError("no columns in celltype table")
    return max(counts, key=counts.get)


def _pick_celltype_column(labels: pd.DataFrame) -> str:
    """Pick a low-cardinality string column with biologically plausible
    labels. Defaults to a column named `celltype` / `cell_type`."""
    for hint in ("celltype", "cell_type", "cluster", "label", "annotation"):
        for col in labels.columns:
            if col.lower().replace("_", "").replace(".", "") == hint.replace("_", "").replace(".", ""):
                return col
    # Otherwise pick a column whose unique cardinality is in [3, 200] —
    # a typical celltype range.
    cands = [(col, labels[col].astype(str).nunique()) for col in labels.columns]
    cands = [(col, n) for col, n in cands if 3 <= n <= 200]
    if not cands:
        raise RuntimeError("could not detect a celltype column")
    cands.sort(key=lambda kv: kv[1])
    return cands[0][0]


__all__ = ["invoke_seurat_local", "register"]
