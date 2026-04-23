"""Generate a synthetic DE-results TSV so the downstream bio CLI can be
tested without R / DESeq2 installed. Produces the same column set as
`scripts/case1_airway_de.R`.

Usage:
    python scripts/case1_synthetic_de.py [output_dir]
"""

from __future__ import annotations

import csv
import random
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    out_dir = Path(argv[0]) if argv else Path("artifacts/case1")
    out_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(42)

    # A plausible glucocorticoid-responsive gene list for the airway dataset.
    up_genes = [
        "DUSP1", "KLF15", "PER1", "FKBP5", "TSC22D3", "ZBTB16", "SAA1", "CRISPLD2",
        "ANGPTL4", "FOS", "DDIT4", "MT1X", "GADD45B", "IGFBP1", "CEBPB", "STAT1",
    ]
    down_genes = ["IL6", "IL8", "CXCL1", "MMP1", "MMP3", "PTGS2", "CCL2", "IRF1"]
    noise_genes = [f"GENE_{i}" for i in range(100)]

    rows = []
    for sym in up_genes:
        rows.append(
            {
                "ensembl_id": f"ENSG{rng.randint(10**10, 10**11):011d}",
                "symbol": sym,
                "log2FoldChange": round(rng.uniform(1.2, 4.0), 3),
                "pvalue": rng.uniform(1e-30, 1e-5),
                "padj": rng.uniform(1e-30, 1e-4),
                "baseMean": round(rng.uniform(100, 5000), 2),
            }
        )
    for sym in down_genes:
        rows.append(
            {
                "ensembl_id": f"ENSG{rng.randint(10**10, 10**11):011d}",
                "symbol": sym,
                "log2FoldChange": round(rng.uniform(-4.0, -1.2), 3),
                "pvalue": rng.uniform(1e-20, 1e-4),
                "padj": rng.uniform(1e-20, 1e-3),
                "baseMean": round(rng.uniform(100, 3000), 2),
            }
        )
    for sym in noise_genes:
        rows.append(
            {
                "ensembl_id": f"ENSG{rng.randint(10**10, 10**11):011d}",
                "symbol": sym,
                "log2FoldChange": round(rng.uniform(-0.5, 0.5), 3),
                "pvalue": rng.uniform(0.1, 1.0),
                "padj": rng.uniform(0.2, 1.0),
                "baseMean": round(rng.uniform(10, 500), 2),
            }
        )

    path = out_dir / "de_results.tsv"
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["ensembl_id", "symbol", "log2FoldChange", "pvalue", "padj", "baseMean"],
            delimiter="\t",
        )
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    print(f"[case1-synthetic] wrote {len(rows)} rows → {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
