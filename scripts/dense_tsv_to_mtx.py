#!/usr/bin/env python
"""Convert a dense gzipped genes-by-cells TSV (GEO-style) into a sparse
10X-style MTX trio (matrix.mtx.gz, features.tsv.gz, barcodes.tsv.gz).

Streaming row-by-row so the dense matrix never materialises in memory —
this is what unblocks R-based Seurat::CreateSeuratObject on hosts with
small Docker memory budgets (case_study_2.md §10.2 Seurat wrapper +
case_study_2.md §11.3 Signac wrapper both call ReadMtx instead of
fread on the dense input).

Usage:
  python scripts/dense_tsv_to_mtx.py <input.tsv.gz> <out_dir>

Output (10X v3 layout):
  <out_dir>/matrix.mtx.gz
  <out_dir>/features.tsv.gz   (gene_id\tgene_id\t"Gene Expression")
  <out_dir>/barcodes.tsv.gz
"""

from __future__ import annotations

import gzip
import io
import sys
from pathlib import Path

import numpy as np
import scipy.sparse as sp
from scipy.io import mmwrite


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    src = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)

    if not src.is_file():
        print(f"input not found: {src}", file=sys.stderr)
        return 1

    print(f"reading {src} ...", flush=True)
    opener = gzip.open if str(src).endswith(".gz") else open
    feature_ids: list[str] = []
    rows: list[int] = []
    cols: list[int] = []
    vals: list[int] = []
    barcodes: list[str] | None = None
    n_cells = 0
    with opener(src, mode="rt", encoding="utf-8") as f:
        header = f.readline().rstrip("\n").split("\t")
        # SHARE-seq cell IDs in the GEO RNA file are comma-separated
        # tokens like `R1.32,R2.61,...` — celltype.txt uses dots, so we
        # normalise here to keep column names matching downstream.
        barcodes = [b.replace(",", ".") for b in header[1:]]
        n_cells = len(barcodes)
        for row_idx, line in enumerate(f):
            tokens = line.rstrip("\n").split("\t")
            feature_ids.append(tokens[0])
            for col_idx, v in enumerate(tokens[1:]):
                if v in ("0", "0.0", ""):
                    continue
                try:
                    iv = int(v)
                except ValueError:
                    iv = int(float(v))
                if iv == 0:
                    continue
                rows.append(row_idx)
                cols.append(col_idx)
                vals.append(iv)
            if (row_idx + 1) % 2500 == 0:
                print(f"  ...row {row_idx + 1} (nnz={len(vals)})", flush=True)

    n_features = len(feature_ids)
    print(f"matrix shape: {n_features} features x {n_cells} cells, "
          f"nnz={len(vals)}", flush=True)

    M = sp.coo_matrix(
        (np.asarray(vals, dtype=np.int32),
         (np.asarray(rows, dtype=np.int32), np.asarray(cols, dtype=np.int32))),
        shape=(n_features, n_cells),
    ).tocsc()

    print("writing matrix.mtx.gz ...", flush=True)
    buf = io.BytesIO()
    mmwrite(buf, M, field="integer", precision=0, symmetry="general")
    with gzip.open(out_dir / "matrix.mtx.gz", "wb") as f:
        f.write(buf.getvalue())
    print("writing features.tsv.gz ...", flush=True)
    with gzip.open(out_dir / "features.tsv.gz", "wt", encoding="utf-8") as f:
        for fid in feature_ids:
            f.write(f"{fid}\t{fid}\tGene Expression\n")
    print("writing barcodes.tsv.gz ...", flush=True)
    with gzip.open(out_dir / "barcodes.tsv.gz", "wt", encoding="utf-8") as f:
        for b in barcodes or []:
            f.write(f"{b}\n")
    print(f"done -> {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
