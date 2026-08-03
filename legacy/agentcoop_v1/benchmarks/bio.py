"""Case Study 1 — bulk RNA-seq bio utilities.

Implements the three `agentcoop bio ...` CLI commands referenced in
`docs/experiments/case_study.md` §2.5:

- `select-markers` — filter DE results by padj/log2fc and emit typed
  `up_genes.json` / `down_genes.json`.
- `enrich` — lightweight over-representation analysis using a bundled
  tiny pathway table (Hallmark-style). Real runs can swap in g:Profiler.
- `run-node geneagent` — hit the GeneAgent sandbox adapter (dry-run in
  framework phase, returns a canned report).

These commands always run offline.
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from agentcoop.benchmarks.common import REPO_ROOT


# ---------------------------------------------------------------------------
# Tiny pathway table — a stand-in for a real enrichment DB during framework
# testing. Real experiments should plug in g:Profiler / MSigDB / Reactome.
# ---------------------------------------------------------------------------


PATHWAYS: dict[str, dict[str, Any]] = {
    "glucocorticoid_response_MSigDB_H": {
        "source": "MSigDB_Hallmark_glucocorticoid_response (stub)",
        "genes": [
            "DUSP1", "KLF15", "PER1", "FKBP5", "TSC22D3", "ZBTB16",
            "SAA1", "CRISPLD2", "ANGPTL4", "DDIT4", "CEBPB", "STAT1",
        ],
    },
    "airway_inflammation_GO_BP": {
        "source": "GO:BP_inflammatory_response (stub)",
        "genes": [
            "IL6", "IL8", "CXCL1", "MMP1", "MMP3", "PTGS2", "CCL2",
            "IRF1", "FOS", "STAT1", "CEBPB",
        ],
    },
    "circadian_rhythm_GO_BP": {
        "source": "GO:BP_circadian_rhythm (stub)",
        "genes": ["PER1", "ARNTL", "CLOCK", "KLF15", "DBP"],
    },
}


# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------


def _read_de_tsv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        rows = list(reader)
    for r in rows:
        # Coerce numeric columns.
        for k in ("log2FoldChange", "pvalue", "padj", "baseMean"):
            if k in r and r[k] not in (None, ""):
                try:
                    r[k] = float(r[k])
                except ValueError:
                    r[k] = None
    return rows


def select_markers(
    de_path: Path,
    *,
    padj_max: float = 0.05,
    abs_log2fc_min: float = 1.0,
    top_k: int | None = 100,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    rows = _read_de_tsv(de_path)
    up = [
        r for r in rows
        if (r.get("padj") is not None and r["padj"] <= padj_max
            and r.get("log2FoldChange") is not None and r["log2FoldChange"] >= abs_log2fc_min
            and r.get("symbol"))
    ]
    down = [
        r for r in rows
        if (r.get("padj") is not None and r["padj"] <= padj_max
            and r.get("log2FoldChange") is not None and r["log2FoldChange"] <= -abs_log2fc_min
            and r.get("symbol"))
    ]
    # Rank: padj asc then |log2fc| desc.
    up.sort(key=lambda r: (r["padj"], -abs(r["log2FoldChange"])))
    down.sort(key=lambda r: (r["padj"], -abs(r["log2FoldChange"])))

    if top_k is not None:
        up = up[:top_k]
        down = down[:top_k]

    def _gene_obj(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "symbol": r["symbol"],
            "ensembl_id": r.get("ensembl_id"),
            "log2fc": r["log2FoldChange"],
            "padj": r["padj"],
        }

    schema = {
        "organism": "Homo sapiens",
        "gene_id_type": "HGNC_SYMBOL",
        "contrast": "dexamethasone_vs_control",
        "selection_rule": {
            "padj_max": padj_max,
            "abs_log2fc_min": abs_log2fc_min,
            "rank_by": "padj_then_abs_log2fc",
            "top_k": top_k,
        },
    }
    up_doc = {**schema, "direction": "up", "genes": [_gene_obj(r) for r in up]}
    down_doc = {**schema, "direction": "down", "genes": [_gene_obj(r) for r in down]}

    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "up_genes.json").write_text(json.dumps(up_doc, indent=2), encoding="utf-8")
        (out_dir / "down_genes.json").write_text(json.dumps(down_doc, indent=2), encoding="utf-8")
    return {
        "up": up_doc,
        "down": down_doc,
        "n_up": len(up),
        "n_down": len(down),
    }


# ---------------------------------------------------------------------------
# Enrichment (tiny hypergeometric-style approximation using the builtin pathway table)
# ---------------------------------------------------------------------------


def _log_choose(n: int, k: int) -> float:
    import math
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def _hypergeom_pvalue(k: int, K: int, n: int, N: int) -> float:
    """P(X >= k) under hypergeometric(N, K, n)."""
    import math
    if k > min(K, n) or k < 0:
        return 1.0
    log_denom = _log_choose(N, n)
    p = 0.0
    for x in range(k, min(K, n) + 1):
        log_num = _log_choose(K, x) + _log_choose(N - K, n - x)
        p += math.exp(log_num - log_denom)
    return min(max(p, 0.0), 1.0)


def enrich(
    gene_set_path: Path,
    *,
    background_size: int = 20000,
    databases: list[str] | None = None,
    out_path: Path | None = None,
) -> dict[str, Any]:
    doc = json.loads(Path(gene_set_path).read_text(encoding="utf-8"))
    gene_set = {g["symbol"] for g in doc.get("genes", [])}
    n = len(gene_set)
    results: list[dict[str, Any]] = []
    pool = PATHWAYS  # databases arg is accepted for CLI symmetry but ignored in stub
    for name, pathway in pool.items():
        K = len(pathway["genes"])
        overlap = gene_set & set(pathway["genes"])
        if not overlap:
            continue
        k = len(overlap)
        pval = _hypergeom_pvalue(k, K, n, background_size)
        results.append(
            {
                "pathway": name,
                "source": pathway["source"],
                "overlap_size": k,
                "pathway_size": K,
                "set_size": n,
                "background_size": background_size,
                "pvalue": pval,
                "overlap_genes": sorted(overlap),
            }
        )
    results.sort(key=lambda r: r["pvalue"])
    out = {
        "direction": doc.get("direction"),
        "databases_requested": databases or [],
        "results": results,
    }
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# GeneAgent sandbox call (dry-run)
# ---------------------------------------------------------------------------


def run_geneagent(
    gene_set_path: Path,
    *,
    context: str,
    out_path: Path | None = None,
    adapter_path: Path | None = None,
) -> dict[str, Any]:
    adapter = adapter_path or (REPO_ROOT / "agentcoop" / "wrappers" / "geneagent" / "adapter.py")
    if not adapter.exists():
        raise FileNotFoundError(f"GeneAgent adapter missing at {adapter}")

    gene_doc = json.loads(Path(gene_set_path).read_text(encoding="utf-8"))
    request = {
        "command": "analyze_gene_set",
        "params": {
            "gene_symbols": [g["symbol"] for g in gene_doc.get("genes", [])],
            "context": context,
            "organism": gene_doc.get("organism", "Homo sapiens"),
            "direction": gene_doc.get("direction"),
        },
    }
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        req_path = Path(td) / "request.json"
        out_file = Path(td) / "result.json"
        req_path.write_text(json.dumps(request), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(adapter), "--input", str(req_path), "--output", str(out_file)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        result = json.loads(out_file.read_text()) if out_file.exists() else {"ok": False, "errors": [proc.stderr]}
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


__all__ = ["select_markers", "enrich", "run_geneagent", "PATHWAYS"]
