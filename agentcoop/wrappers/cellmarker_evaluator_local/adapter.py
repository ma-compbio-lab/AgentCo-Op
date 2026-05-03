"""CellMarker evaluator (join-agent) — local-Python adapter."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# ---------------------------------------------------------------------------
# Adapter entrypoint
# ---------------------------------------------------------------------------


def invoke_cellmarker_evaluator_local(req: dict[str, Any]) -> dict[str, Any]:
    out_dir = Path(req.get("output_dir",
                            "runs/case2/shareseq_skin/artifacts/cellmarker_evaluator_run"))
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "run.log"
    log = _Logger(log_path)

    inputs = req.get("input", {}) or {}
    branches = inputs.get("branch_responses") or {}
    join_inputs = inputs.get("join_inputs") or {}

    # Locate the two top-N marker JSONs from the registered branch responses.
    rna_json_path = _find_artifact(branches, "rna_top_markers_json", log)
    atac_json_path = _find_artifact(branches, "atac_top_marker_genes_json", log)

    cellmarker_path = _resolve_cellmarker_file(join_inputs, log)
    organism = str(join_inputs.get("organism", "Mouse"))
    primary_filter = list(join_inputs.get("tissue_filter_primary") or ["Skin"])
    extended_filter = list(join_inputs.get("tissue_filter_extended") or [
        "Skin", "Epidermis", "Dermis", "Hair follicle", "Hair",
    ])
    aliases = (join_inputs.get("aliases") or _DEFAULT_ALIASES)

    warnings: list[str] = []
    try:
        rna = _load_marker_json(rna_json_path, log) if rna_json_path else {}
        atac = _load_marker_json(atac_json_path, log) if atac_json_path else {}
        if not rna and not atac:
            raise RuntimeError("could not locate either RNA or ATAC top-marker JSON in branch_responses")

        # ---- per-cell-type set operations --------------------------------
        all_cts = sorted(set(rna) | set(atac))
        set_ops: dict[str, dict[str, Any]] = {}
        size_rows: list[dict[str, Any]] = []
        for ct in all_cts:
            R = set(rna.get(ct, []))
            A = set(atac.get(ct, []))
            I = R & A
            U = R | A
            set_ops[ct] = {
                "rna": sorted(R),
                "atac": sorted(A),
                "intersection": sorted(I),
                "union": sorted(U),
                "n_rna": len(R),
                "n_atac": len(A),
                "n_intersection": len(I),
                "n_union": len(U),
                "jaccard_rna_atac": (len(I) / len(U)) if U else None,
            }
            size_rows.append({
                "cell_type": ct,
                "n_rna": len(R),
                "n_atac": len(A),
                "n_intersection": len(I),
                "n_union": len(U),
                "jaccard_rna_atac": set_ops[ct]["jaccard_rna_atac"],
            })
        (out_dir / "marker_set_operations_by_celltype.json").write_text(
            json.dumps(set_ops, indent=2), encoding="utf-8"
        )
        size_df = pd.DataFrame(size_rows).sort_values("cell_type")
        size_df.to_csv(out_dir / "marker_set_sizes_by_celltype.csv", index=False)

        # ---- harmonisation report ----------------------------------------
        harm = {
            "rule": "upper(strip(symbol))",
            "n_genes_input_rna": int(sum(len(v) for v in rna.values())),
            "n_genes_input_atac": int(sum(len(v) for v in atac.values())),
            "duplicates_removed_within_cell_type": True,
            "alias_table_used": "manual mouse-skin aliases (case_study_2.md §14.2)",
        }
        (out_dir / "gene_symbol_harmonization_report.json").write_text(
            json.dumps(harm, indent=2), encoding="utf-8"
        )

        # ---- CellMarker parsing + gold sets ------------------------------
        cm_raw = _read_cellmarker_file(cellmarker_path, log)
        log.info(f"CellMarker rows: {len(cm_raw)}")
        # Persist a tab-separated copy of the raw mouse marker rows for
        # reproducibility.
        raw_tsv = out_dir / "cellmarker_raw_mouse_markers.tsv"
        cm_raw.to_csv(raw_tsv, sep="\t", index=False)

        gold_primary, label_map_primary = _build_gold_sets(
            cm_raw, organism=organism, tissue_filter=primary_filter,
            dataset_cell_types=all_cts, aliases=aliases, log=log,
        )
        gold_extended, label_map_extended = _build_gold_sets(
            cm_raw, organism=organism, tissue_filter=extended_filter,
            dataset_cell_types=all_cts, aliases=aliases, log=log,
        )

        # If the strict skin filter is sparse, fall back on extended for
        # the *primary* gold-set; record both.
        gold = gold_primary if any(len(v) > 0 for v in gold_primary.values()) else gold_extended
        label_map = label_map_primary if gold is gold_primary else label_map_extended
        filter_mode = "skin" if gold is gold_primary else "skin_extended"
        log.info(f"using cellmarker_filter_mode={filter_mode}")

        gold_path = out_dir / "cellmarker_gold_markers_by_celltype.json"
        gold_path.write_text(
            json.dumps({k: sorted(v) for k, v in gold.items()}, indent=2),
            encoding="utf-8",
        )
        label_map_csv = out_dir / "cellmarker_label_mapping.csv"
        pd.DataFrame(label_map).to_csv(label_map_csv, index=False)

        # ---- precision / recall ------------------------------------------
        rows: list[dict[str, Any]] = []
        for ct in all_cts:
            R = set(_norm_symbol(g) for g in rna.get(ct, []) if _norm_symbol(g))
            A = set(_norm_symbol(g) for g in atac.get(ct, []) if _norm_symbol(g))
            I = R & A
            U = R | A
            G = set(gold.get(ct, []))
            row = {
                "cell_type": ct,
                "mapped_to_cellmarker": ct in gold and len(G) > 0,
                "n_gold": len(G),
                "n_rna": len(R),
                "n_atac": len(A),
                "n_intersection": len(I),
                "n_union": len(U),
            }
            if G:
                row.update({
                    "precision_rna": _precision(R, G),
                    "precision_atac": _precision(A, G),
                    "precision_intersection": _precision(I, G),
                    "recall_rna": _recall(R, G),
                    "recall_atac": _recall(A, G),
                    "recall_union": _recall(U, G),
                    "hits_rna": len(R & G),
                    "hits_atac": len(A & G),
                    "hits_intersection": len(I & G),
                    "hits_union": len(U & G),
                })
            rows.append(row)
        df = pd.DataFrame(rows)
        df.to_csv(out_dir / "precision_recall_by_celltype.csv", index=False)

        metric_cols = [
            "precision_rna", "precision_atac", "precision_intersection",
            "recall_rna", "recall_atac", "recall_union",
        ]
        mapped = df[df["mapped_to_cellmarker"] == True].copy()
        summary: list[dict[str, Any]] = []
        for metric in metric_cols:
            vals = pd.to_numeric(mapped[metric], errors="coerce").dropna()
            summary.append({
                "metric": metric,
                "mean": float(vals.mean()) if len(vals) else None,
                "median": float(vals.median()) if len(vals) else None,
                "std": float(vals.std()) if len(vals) else None,
                "n_cell_types": int(vals.shape[0]),
                "cellmarker_filter_mode": filter_mode,
            })
        summary_df = pd.DataFrame(summary)
        summary_df.to_csv(out_dir / "precision_recall_summary.csv", index=False)

        # ---- collaboration-gain table ------------------------------------
        gain_rows: list[dict[str, Any]] = []
        for _, row in mapped.iterrows():
            gain_rows.append({
                "cell_type": row["cell_type"],
                "intersection_precision_gain_over_rna": _safe_sub(row.get("precision_intersection"), row.get("precision_rna")),
                "intersection_precision_gain_over_atac": _safe_sub(row.get("precision_intersection"), row.get("precision_atac")),
                "union_recall_gain_over_rna": _safe_sub(row.get("recall_union"), row.get("recall_rna")),
                "union_recall_gain_over_atac": _safe_sub(row.get("recall_union"), row.get("recall_atac")),
                "intersection_strict_win": _strict_win(row.get("precision_intersection"),
                                                        row.get("precision_rna"),
                                                        row.get("precision_atac")),
                "union_strict_win": _strict_win(row.get("recall_union"),
                                                row.get("recall_rna"),
                                                row.get("recall_atac")),
            })
        gain_df = pd.DataFrame(gain_rows)
        gain_df.to_csv(out_dir / "collaboration_gain_table.csv", index=False)
        n_strict_int = int(gain_df["intersection_strict_win"].fillna(False).sum()) if "intersection_strict_win" in gain_df else 0
        n_strict_uni = int(gain_df["union_strict_win"].fillna(False).sum()) if "union_strict_win" in gain_df else 0
        n_mapped = int(len(mapped))

        # ---- figures ------------------------------------------------------
        fig_paths: list[str] = []
        try:
            fig1 = out_dir / "marker_overlap_heatmap.png"
            _draw_overlap_heatmap(set_ops, fig1)
            fig_paths.append(str(fig1))
        except Exception as exc:
            warnings.append(f"overlap heatmap failed: {exc}")
        try:
            fig2 = out_dir / "precision_recall_barplot.png"
            _draw_pr_barplot(df, fig2)
            fig_paths.append(str(fig2))
        except Exception as exc:
            warnings.append(f"PR barplot failed: {exc}")

        # ---- PanglaoDB independent evaluation (additive per case_study_2.md
        # user-prose addendum: "CellMarker 2.0 + PanglaoDB independently
        # filter for mouse skin markers and report precision/recall
        # separately rather than aggregated"). Skips silently if no
        # panglaodb_file is supplied or the file isn't readable.
        panglaodb_metrics: dict[str, Any] | None = None
        panglaodb_artifacts: dict[str, str] = {}
        panglaodb_path = _resolve_panglaodb_file(join_inputs, log)
        if panglaodb_path is not None:
            try:
                pdb_raw = _read_panglaodb_file(panglaodb_path, log)
                gold_pdb, label_map_pdb = _build_panglaodb_gold_sets(
                    pdb_raw, organism=organism,
                    dataset_cell_types=all_cts, aliases=aliases, log=log,
                )
                gold_pdb_path = out_dir / "panglaodb_gold_markers_by_celltype.json"
                gold_pdb_path.write_text(
                    json.dumps({k: sorted(v) for k, v in gold_pdb.items()}, indent=2),
                    encoding="utf-8",
                )
                pdb_label_csv = out_dir / "panglaodb_label_mapping.csv"
                pd.DataFrame(label_map_pdb).to_csv(pdb_label_csv, index=False)
                pdb_rows, pdb_summary, pdb_int_w, pdb_uni_w, pdb_n_mapped = \
                    _eval_against_gold(rna, atac, gold_pdb, all_cts, db_label="panglaodb")
                pdb_pr_csv = out_dir / "precision_recall_panglaodb_by_celltype.csv"
                pd.DataFrame(pdb_rows).to_csv(pdb_pr_csv, index=False)
                pdb_summary_csv = out_dir / "precision_recall_panglaodb_summary.csv"
                pd.DataFrame(pdb_summary).to_csv(pdb_summary_csv, index=False)
                panglaodb_artifacts = {
                    "panglaodb_gold_markers_by_celltype_json": str(gold_pdb_path),
                    "panglaodb_label_mapping_csv": str(pdb_label_csv),
                    "precision_recall_panglaodb_by_celltype_csv": str(pdb_pr_csv),
                    "precision_recall_panglaodb_summary_csv": str(pdb_summary_csv),
                }
                panglaodb_metrics = {
                    "n_cell_types_mapped": pdb_n_mapped,
                    "n_intersection_strict_wins": pdb_int_w,
                    "n_union_strict_wins": pdb_uni_w,
                    "summary_metrics": pdb_summary,
                }
                log.info(f"PanglaoDB eval: mapped={pdb_n_mapped}, "
                          f"int_wins={pdb_int_w}, uni_wins={pdb_uni_w}")
            except Exception as exc:
                warnings.append(f"PanglaoDB evaluation skipped: {type(exc).__name__}: {exc}")
                log.error(f"PanglaoDB eval failed: {type(exc).__name__}: {exc}")

        pdb_summary_msg = ""
        if panglaodb_metrics:
            pm = panglaodb_metrics["summary_metrics"]
            pdb_summary_msg = (
                f" | PanglaoDB: {panglaodb_metrics['n_cell_types_mapped']} mapped, "
                f"int_wins={panglaodb_metrics['n_intersection_strict_wins']}, "
                f"uni_wins={panglaodb_metrics['n_union_strict_wins']}; "
                f"means: P_rna={pm[0]['mean']!r}, P_atac={pm[1]['mean']!r}, "
                f"P_int={pm[2]['mean']!r}, R_rna={pm[3]['mean']!r}, "
                f"R_atac={pm[4]['mean']!r}, R_union={pm[5]['mean']!r}"
            )

        result = {
            "status": "success",
            "summary": (
                f"CellMarker eval ({filter_mode}): {n_mapped} mapped cell types; "
                f"intersection_strict_wins={n_strict_int}, union_strict_wins={n_strict_uni}; "
                f"means: P_rna={summary[0]['mean']!r}, P_atac={summary[1]['mean']!r}, "
                f"P_int={summary[2]['mean']!r}, R_rna={summary[3]['mean']!r}, "
                f"R_atac={summary[4]['mean']!r}, R_union={summary[5]['mean']!r}."
                f"{pdb_summary_msg}"
            ),
            "warnings": warnings,
            "artifacts": {
                "marker_set_operations_by_celltype_json": str(out_dir / "marker_set_operations_by_celltype.json"),
                "marker_set_sizes_by_celltype_csv": str(out_dir / "marker_set_sizes_by_celltype.csv"),
                "gene_symbol_harmonization_report_json": str(out_dir / "gene_symbol_harmonization_report.json"),
                "cellmarker_raw_mouse_markers_tsv": str(raw_tsv),
                "cellmarker_gold_markers_by_celltype_json": str(gold_path),
                "cellmarker_label_mapping_csv": str(label_map_csv),
                "precision_recall_by_celltype_csv": str(out_dir / "precision_recall_by_celltype.csv"),
                "precision_recall_summary_csv": str(out_dir / "precision_recall_summary.csv"),
                "collaboration_gain_table_csv": str(out_dir / "collaboration_gain_table.csv"),
                "marker_overlap_heatmap_png": fig_paths[0] if fig_paths else "",
                "precision_recall_barplot_png": fig_paths[1] if len(fig_paths) > 1 else "",
                "run_log": str(log_path),
                **panglaodb_artifacts,
            },
            "main_results": {
                "cellmarker_filter_mode": filter_mode,
                "n_cell_types_total": int(len(all_cts)),
                "n_cell_types_mapped": n_mapped,
                "n_intersection_strict_wins": n_strict_int,
                "n_union_strict_wins": n_strict_uni,
                "summary_metrics": summary,
                "panglaodb": panglaodb_metrics,
            },
        }
        (out_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result
    except Exception as exc:
        log.error(f"adapter failed: {type(exc).__name__}: {exc}")
        return {
            "status": "failed",
            "summary": f"CellMarker evaluator failed: {type(exc).__name__}: {exc}",
            "artifacts": {"run_log": str(log_path)},
            "warnings": warnings + [str(exc)],
        }


def register() -> None:
    from agentcoop.core.repo_collaboration import register_local_adapter

    register_local_adapter("CellMarkerEvaluator")(invoke_cellmarker_evaluator_local)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_DEFAULT_ALIASES: dict[str, list[str]] = {
    "Basal": ["basal cell", "epidermal basal cell", "keratinocyte", "basal keratinocyte"],
    "Spinous": ["keratinocyte", "spinous keratinocyte", "suprabasal keratinocyte"],
    "Granular": ["keratinocyte", "granular keratinocyte"],
    "Bulge": ["hair follicle stem cell", "bulge cell", "hfsc"],
    "ahighCD34+ bulge": ["hair follicle stem cell", "bulge cell"],
    "alowCD34+ bulge": ["hair follicle stem cell", "bulge cell"],
    "K6+ Bulge Companion Layer": ["bulge companion layer cell", "bulge cell", "keratinocyte"],
    "Isthmus": ["isthmus cell", "keratinocyte"],
    "Infundibulum": ["infundibulum cell", "keratinocyte"],
    "ORS": ["outer root sheath cell", "keratinocyte"],
    "IRS": ["inner root sheath cell"],
    "Hair Shaft-cuticle.cortex": ["hair shaft cell", "cortex cell", "cuticle cell"],
    "Medulla": ["hair medulla cell", "medulla cell"],
    "TAC-1": ["transit amplifying cell", "matrix cell", "hair matrix cell"],
    "TAC-2": ["transit amplifying cell", "matrix cell", "hair matrix cell"],
    "Dermal Fibroblast": ["fibroblast", "dermal fibroblast"],
    "Dermal Papilla": ["dermal papilla cell", "fibroblast"],
    "Dermal Sheath": ["dermal sheath cell", "fibroblast"],
    "Endothelial": ["endothelial cell"],
    "Macrophage DC": ["macrophage", "dendritic cell"],
    "Melanocyte": ["melanocyte"],
    "Sebaceous Gland": ["sebaceous gland cell", "sebocyte"],
    "Schwann Cell": ["schwann cell"],
}


_NORM_RE = re.compile(r"\s+")


def _norm_symbol(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, float) and np.isnan(x):
        return ""
    return _NORM_RE.sub("", str(x).strip()).upper()


def _norm_label(x: Any) -> str:
    if x is None:
        return ""
    s = str(x).strip().lower()
    return re.sub(r"[\s\-/_]+", " ", s)


def _load_marker_json(path: Path, log: "_Logger") -> dict[str, list[str]]:
    log.info(f"loading top-N markers JSON: {path}")
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return {ct: list(genes or []) for ct, genes in raw.items()}


def _find_artifact(branch_responses: dict[str, Any], key: str, log: "_Logger") -> Path | None:
    """Search every branch's `artifacts` for a key matching `key`. Return
    the first hit (a path) as a Path object; None if not found."""
    for name, resp in (branch_responses or {}).items():
        arts = (resp or {}).get("artifacts") or {}
        if key in arts and arts[key]:
            log.info(f"  found {key} in branch {name!r}: {arts[key]}")
            return Path(str(arts[key]))
    return None


def _resolve_cellmarker_file(join_inputs: dict[str, Any], log: "_Logger") -> Path:
    p = join_inputs.get("cellmarker_file")
    if p:
        path = Path(p).expanduser()
        if path.is_file():
            return path
    # Fallback: search common dataset cache locations.
    candidates = [
        Path("data/shareseq_skin/Cell_marker_Mouse.xlsx"),
        Path("data/cellmarker/Cell_marker_Mouse.xlsx"),
        Path("data/heart_merfish/Cell_marker_Mouse.xlsx"),
    ]
    for c in candidates:
        if c.is_file():
            return c
    raise FileNotFoundError(
        "Cell_marker_Mouse.xlsx not found; declare it via "
        "join_agent.inputs.cellmarker_file in the request YAML"
    )


def _resolve_panglaodb_file(join_inputs: dict[str, Any], log: "_Logger") -> Path | None:
    """Locate a PanglaoDB markers TSV. Returns None if not declared and
    no canonical fallback exists — the evaluator silently skips PanglaoDB
    eval in that case."""
    p = join_inputs.get("panglaodb_file")
    if p:
        path = Path(p).expanduser()
        if path.is_file():
            return path
        log.info(f"  panglaodb_file declared but missing: {p}")
    candidates = [
        Path("data/panglaodb/PanglaoDB_markers_27_Mar_2020.tsv"),
        Path("data/panglaodb/PanglaoDB_markers.tsv"),
        Path("external/SpatialAgent/data/PanglaoDB_markers_27_Mar_2020.tsv"),
    ]
    for c in candidates:
        if c.is_file():
            log.info(f"  panglaodb_file fallback: {c}")
            return c
    return None


def _read_panglaodb_file(path: Path, log: "_Logger") -> pd.DataFrame:
    """Read PanglaoDB markers TSV. Schema (canonical):
       species, official gene symbol, cell type, nicknames, ...
    species is space-separated tokens like "Mm Hs" or "Mm".
    """
    if path.suffix == ".gz":
        df = pd.read_csv(path, sep="\t", compression="gzip")
    else:
        df = pd.read_csv(path, sep="\t")
    log.info(f"  PanglaoDB columns: {list(df.columns)[:14]}")
    return df


def _build_panglaodb_gold_sets(
    pdb: pd.DataFrame,
    *,
    organism: str,
    dataset_cell_types: list[str],
    aliases: dict[str, list[str]],
    log: "_Logger",
) -> tuple[dict[str, set[str]], list[dict[str, Any]]]:
    """Build per-dataset-celltype gold marker sets from PanglaoDB.

    PanglaoDB doesn't tag tissue at the cell-type level the way
    CellMarker does, so we filter by species and let the cell-type
    alias mapping handle skin scoping (matching dataset labels like
    `Basal` → `Basal cells`, `Sebocyte` → `Sebocytes`, etc.).
    """
    species_col = _find_col(pdb, ["species", "Species"])
    cell_col    = _find_col(pdb, ["cell type", "cell_type", "celltype", "Cell type"])
    sym_col     = _find_col(pdb, ["official gene symbol", "Symbol", "gene_symbol", "marker"])
    if not (species_col and cell_col and sym_col):
        raise RuntimeError(
            f"PanglaoDB schema not recognised; columns={list(pdb.columns)[:10]}"
        )

    # Species in PanglaoDB is space-separated; "Mm" must appear as a token.
    organism_token = "mm" if organism.lower().startswith("m") else "hs"
    def _has_species(s: Any) -> bool:
        if not isinstance(s, str):
            return False
        toks = [t.lower() for t in re.split(r"\s+", s.strip()) if t]
        return organism_token in toks

    df = pdb[pdb[species_col].apply(_has_species)].copy()
    log.info(f"  PanglaoDB filtered to species={organism}: {len(df)} rows")

    gold_by_pdb: dict[str, set[str]] = {}
    for _, row in df.iterrows():
        ct_raw = str(row[cell_col])
        sym_raw = row[sym_col]
        if isinstance(sym_raw, str):
            tokens = re.split(r"[,;|/]+", sym_raw)
        elif isinstance(sym_raw, (list, tuple)):
            tokens = [str(s) for s in sym_raw]
        else:
            tokens = [str(sym_raw)]
        norm_ct = _norm_label(ct_raw)
        bucket = gold_by_pdb.setdefault(norm_ct, set())
        for t in tokens:
            sym = _norm_symbol(t)
            if sym:
                bucket.add(sym)

    out: dict[str, set[str]] = {}
    mapping_records: list[dict[str, Any]] = []
    for ct in dataset_cell_types:
        merged: set[str] = set()
        used_aliases: list[str] = []
        for alias in [ct] + list(aliases.get(ct, [])):
            norm = _norm_label(alias)
            if norm in gold_by_pdb:
                merged |= gold_by_pdb[norm]
                used_aliases.append(alias)
                continue
            for pdb_label, genes in gold_by_pdb.items():
                if norm and (norm in pdb_label or pdb_label in norm):
                    merged |= genes
                    used_aliases.append(alias)
                    break
        out[ct] = merged
        mapping_records.append({
            "cell_type_dataset": ct,
            "panglaodb_aliases_used": ";".join(used_aliases) if used_aliases else "",
            "n_panglaodb_markers": len(merged),
            "include_in_macro_eval": bool(merged),
        })
    return out, mapping_records


def _eval_against_gold(
    rna: dict[str, list[str]],
    atac: dict[str, list[str]],
    gold: dict[str, set[str]],
    all_cts: list[str],
    *,
    db_label: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int, int]:
    """Compute per-cell-type precision/recall + collaboration-gain wins
    against a gold-set dict. Returns (rows, summary, n_int_wins,
    n_uni_wins, n_mapped). Used for both CellMarker and PanglaoDB —
    each evaluation is independent (no aggregation across DBs)."""
    rows: list[dict[str, Any]] = []
    n_int_wins = 0
    n_uni_wins = 0
    for ct in all_cts:
        R = set(_norm_symbol(g) for g in rna.get(ct, []) if _norm_symbol(g))
        A = set(_norm_symbol(g) for g in atac.get(ct, []) if _norm_symbol(g))
        I = R & A
        U = R | A
        G = set(gold.get(ct, []))
        row: dict[str, Any] = {
            "cell_type": ct,
            f"mapped_to_{db_label}": ct in gold and len(G) > 0,
            "n_gold": len(G),
            "n_rna": len(R), "n_atac": len(A),
            "n_intersection": len(I), "n_union": len(U),
        }
        if G:
            row.update({
                "precision_rna": _precision(R, G),
                "precision_atac": _precision(A, G),
                "precision_intersection": _precision(I, G),
                "recall_rna": _recall(R, G),
                "recall_atac": _recall(A, G),
                "recall_union": _recall(U, G),
                "hits_rna": len(R & G), "hits_atac": len(A & G),
                "hits_intersection": len(I & G), "hits_union": len(U & G),
            })
            int_w = _strict_win(row.get("precision_intersection"),
                                row.get("precision_rna"), row.get("precision_atac"))
            uni_w = _strict_win(row.get("recall_union"),
                                row.get("recall_rna"), row.get("recall_atac"))
            if int_w:
                n_int_wins += 1
            if uni_w:
                n_uni_wins += 1
        rows.append(row)
    df = pd.DataFrame(rows)
    metric_cols = [
        "precision_rna", "precision_atac", "precision_intersection",
        "recall_rna", "recall_atac", "recall_union",
    ]
    mapped = df[df[f"mapped_to_{db_label}"] == True].copy()
    summary: list[dict[str, Any]] = []
    for metric in metric_cols:
        vals = pd.to_numeric(mapped[metric], errors="coerce").dropna()
        summary.append({
            "metric": metric, "db": db_label,
            "mean": float(vals.mean()) if len(vals) else None,
            "median": float(vals.median()) if len(vals) else None,
            "std": float(vals.std()) if len(vals) else None,
            "n_cell_types": int(vals.shape[0]),
        })
    return rows, summary, n_int_wins, n_uni_wins, int(len(mapped))


def _read_cellmarker_file(path: Path, log: "_Logger") -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        df = pd.read_excel(path, engine="openpyxl")
    elif suffix in {".tsv", ".txt"}:
        df = pd.read_csv(path, sep="\t")
    else:
        df = pd.read_csv(path)
    log.info(f"  CellMarker columns: {list(df.columns)[:12]}")
    return df


def _build_gold_sets(
    cm: pd.DataFrame,
    *,
    organism: str,
    tissue_filter: list[str],
    dataset_cell_types: list[str],
    aliases: dict[str, list[str]],
    log: "_Logger",
) -> tuple[dict[str, set[str]], list[dict[str, Any]]]:
    """Return per-dataset-celltype gold marker sets + the alias mapping."""
    species_col = _find_col(cm, ["species", "Species"])
    tissue_col = _find_col(cm, ["tissue_type", "tissue", "Tissue", "Tissue_type"])
    cell_col = _find_col(cm, ["cell_name", "Cell_name", "cell type", "cell_type", "celltype"])
    sym_col = _find_col(cm, ["Symbol", "marker", "Marker", "gene_symbol", "GeneSymbol"])
    if not (species_col and cell_col and sym_col):
        raise RuntimeError(f"CellMarker schema not recognised; columns={list(cm.columns)[:10]}")

    df = cm[cm[species_col].astype(str).str.lower() == organism.lower()].copy()
    if tissue_col is not None:
        norm_tissues = df[tissue_col].astype(str).str.lower().str.strip()
        wanted = {t.lower().strip() for t in tissue_filter}
        df = df[norm_tissues.isin(wanted)
                | norm_tissues.apply(lambda x: any(w in x for w in wanted))]
    log.info(f"  filtered CellMarker: {len(df)} rows after species + tissue filter "
              f"({tissue_filter})")

    # Gold by CellMarker cell name.
    gold_by_cm: dict[str, set[str]] = {}
    for _, row in df.iterrows():
        ct_raw = str(row[cell_col])
        sym_raw = row[sym_col]
        # `sym_raw` may be a comma-separated list or a list itself.
        if isinstance(sym_raw, str):
            tokens = re.split(r"[,;|/]+", sym_raw)
        elif isinstance(sym_raw, (list, tuple)):
            tokens = [str(s) for s in sym_raw]
        else:
            tokens = [str(sym_raw)]
        norm_ct = _norm_label(ct_raw)
        bucket = gold_by_cm.setdefault(norm_ct, set())
        for t in tokens:
            sym = _norm_symbol(t)
            if sym:
                bucket.add(sym)

    # Map dataset cell types → CellMarker cell names by alias.
    out: dict[str, set[str]] = {}
    mapping_records: list[dict[str, Any]] = []
    for ct in dataset_cell_types:
        merged: set[str] = set()
        used_aliases: list[str] = []
        # Always also try the dataset label itself as an alias.
        for alias in [ct] + list(aliases.get(ct, [])):
            norm = _norm_label(alias)
            # Exact alias match → take all genes from that CellMarker cell name.
            if norm in gold_by_cm:
                merged |= gold_by_cm[norm]
                used_aliases.append(alias)
                continue
            # Substring fallback.
            for cm_label, genes in gold_by_cm.items():
                if norm and (norm in cm_label or cm_label in norm):
                    merged |= genes
                    used_aliases.append(alias)
                    break
        out[ct] = merged
        mapping_records.append({
            "cell_type_dataset": ct,
            "cellmarker_aliases_used": ";".join(used_aliases) if used_aliases else "",
            "n_cellmarker_markers": len(merged),
            "include_in_macro_eval": bool(merged),
        })
    return out, mapping_records


def _find_col(df: pd.DataFrame, candidates: Iterable[str]) -> str | None:
    cols_lower = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand in df.columns:
            return cand
        if cand.lower() in cols_lower:
            return cols_lower[cand.lower()]
    return None


def _precision(pred: set[str], gold: set[str]) -> float | None:
    return (len(pred & gold) / len(pred)) if pred else None


def _recall(pred: set[str], gold: set[str]) -> float | None:
    return (len(pred & gold) / len(gold)) if gold else None


def _safe_sub(a: Any, b: Any) -> float | None:
    try:
        if a is None or b is None or pd.isna(a) or pd.isna(b):
            return None
        return float(a) - float(b)
    except Exception:
        return None


def _strict_win(target: Any, *baselines: Any) -> bool | None:
    try:
        if target is None or pd.isna(target):
            return None
        clean_baselines = [float(b) for b in baselines if b is not None and not pd.isna(b)]
        if not clean_baselines:
            return None
        return float(target) > max(clean_baselines)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------


def _draw_overlap_heatmap(set_ops: dict[str, dict[str, Any]], path: Path) -> None:
    cell_types = sorted(set_ops.keys())
    if not cell_types:
        plt.figure(figsize=(4, 3))
        plt.text(0.5, 0.5, "no cell types", ha="center", va="center")
        plt.savefig(path, dpi=120, bbox_inches="tight")
        plt.close()
        return
    n = len(cell_types)
    M = np.zeros((n, n), dtype=float)
    rna_sets = {ct: set(set_ops[ct]["rna"]) for ct in cell_types}
    atac_sets = {ct: set(set_ops[ct]["atac"]) for ct in cell_types}
    for i, ri in enumerate(cell_types):
        for j, aj in enumerate(cell_types):
            R = rna_sets[ri]
            A = atac_sets[aj]
            U = R | A
            M[i, j] = (len(R & A) / len(U)) if U else 0.0
    fig, ax = plt.subplots(figsize=(max(5, 0.5 * n), max(5, 0.5 * n)))
    im = ax.imshow(M, vmin=0, vmax=max(0.05, float(M.max() if M.size else 0.05)),
                   cmap="viridis")
    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    ax.set_xticklabels(cell_types, rotation=60, ha="right", fontsize=7)
    ax.set_yticklabels(cell_types, fontsize=7)
    ax.set_xlabel("ATAC cell type")
    ax.set_ylabel("RNA cell type")
    ax.set_title("RNA × ATAC top-N marker Jaccard")
    fig.colorbar(im, fraction=0.04, pad=0.02)
    fig.tight_layout()
    fig.savefig(path, dpi=140, bbox_inches="tight")
    plt.close(fig)


def _draw_pr_barplot(df: pd.DataFrame, path: Path) -> None:
    mapped = df[df.get("mapped_to_cellmarker") == True].copy()
    if mapped.empty:
        plt.figure(figsize=(6, 3))
        plt.text(0.5, 0.5, "no mapped cell types", ha="center", va="center")
        plt.savefig(path, dpi=140, bbox_inches="tight")
        plt.close()
        return
    metrics = ["precision_rna", "precision_atac", "precision_intersection",
               "recall_rna", "recall_atac", "recall_union"]
    ct = mapped["cell_type"].astype(str).tolist()
    n = len(ct)
    width = 0.13
    x = np.arange(n)
    fig, ax = plt.subplots(figsize=(max(7, 0.5 * n), 5))
    palette = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b"]
    for i, m in enumerate(metrics):
        vals = pd.to_numeric(mapped[m], errors="coerce").fillna(0.0).tolist()
        ax.bar(x + (i - 2.5) * width, vals, width, label=m, color=palette[i])
    ax.set_xticks(x)
    ax.set_xticklabels(ct, rotation=55, ha="right", fontsize=8)
    ax.set_ylabel("metric value")
    ax.set_title("Per-cell-type precision / recall vs CellMarker 2.0")
    ax.legend(fontsize=8, loc="upper right", ncol=2)
    ax.set_ylim(0, 1.05)
    fig.tight_layout()
    fig.savefig(path, dpi=140, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------


class _Logger:
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


__all__ = ["invoke_cellmarker_evaluator_local", "register"]
