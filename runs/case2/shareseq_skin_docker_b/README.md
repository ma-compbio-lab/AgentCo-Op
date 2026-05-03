# CS2 — SHARE-seq mouse skin multiome (Path B: Docker, Python sandbox + PanglaoDB)

**Session 9, 2026-05-03.** First Docker-mode rerun of CS2 after the
user's local colima setup. Adds independent PanglaoDB evaluation
alongside CellMarker 2.0 (per the user's case_study_2.md update).

| Field | Value |
|---|---|
| Run dir | `runs/case2/shareseq_skin_docker_b/` |
| Topology | `parallel_then_join` (Seurat ‖ Signac → CellMarkerEvaluator → integrator) |
| Docker mode | **on** (`agentcoop-runtime:case-study` Python image) |
| Wall time | 31:06 (Seurat + Signac branches run in parallel containers) |
| Status | `success` |
| Cells / genes / peaks | 32 231 / 23 296 / 344 592 |
| Cell types after filters | 22 |
| Top-N | 50 |
| LLM integrator | `gpt-5` (reasoning_effort=medium); 5 899 tokens |

## Branch outputs (identical to Session-7.3 no-docker run)
| Branch | Numbers |
|---|---|
| Seurat (RNA) | 147 057 marker rows; barcode_col=`rna.bc`, celltype_col=`celltype` |
| Signac (ATAC) | 10 776 peak markers → 8 843 gene markers via peak-to-nearest-gene |

## CellMarker 2.0 evaluation (skin filter)
| Metric | Mean | Median | n cell types |
|---|---:|---:|---:|
| precision_rna | 0.0505 | 0.040 | 19 |
| precision_atac | 0.0033 | 0.000 | 12 |
| precision_intersection | 0.0833 | 0.000 | 7 |
| recall_rna | 0.1225 | 0.040 | 19 |
| recall_atac | 0.0093 | 0.000 | 19 |
| recall_union | 0.1225 | 0.040 | 19 |

Strict cross-modal wins on CellMarker: **intersection 1 / 19**, **union 0 / 19**.

## PanglaoDB evaluation (NEW — Path B addition)
| Metric | Mean | Median | n cell types |
|---|---:|---:|---:|
| precision_rna | 0.1120 | 0.080 | 15 |
| precision_atac | 0.0375 | 0.030 | 8 |
| **precision_intersection** | **0.3900** | **0.333** | 5 |
| recall_rna | 0.0665 | 0.056 | 15 |
| recall_atac | 0.0119 | 0.000 | 15 |
| recall_union | 0.0720 | 0.056 | 15 |

Strict cross-modal wins on PanglaoDB: **intersection 4 / 15**, **union 4 / 15**.

PanglaoDB shows a much stronger collaboration signal than CellMarker
alone:

| Cell type | P_rna | P_atac | P_intersection |
|---|---:|---:|---:|
| Dermal Fibroblast | 0.40 | 0.12 | **0.75** |
| Spinous | 0.08 | 0.08 | **0.667** |
| Basal | 0.10 | 0.04 | **0.333** |
| Infundibulum | 0.02 | 0.02 | **0.20** |

These four cell types post a strict intersection-precision win — the
gene that survives both transcriptional and chromatin-accessibility
evidence is, on average, 3-8× more likely to be a known PanglaoDB
marker than the gene found by either modality alone.

## Why PanglaoDB and CellMarker disagree

- **CellMarker filter mode = `skin`** (strict tissue tag): catches
  dataset-specific labels like Bulge, ORS, IRS that have explicit
  skin-tagged entries in CellMarker. Mapped 19 / 22 dataset cell types.
- **PanglaoDB has no per-tissue tag**: we filter by `species=Mm` and
  let the cell-type alias mapping do the scoping. Mapped 15 / 22 — a
  bit fewer because PanglaoDB doesn't catalog hair-follicle structures
  like TAC, Medulla, ahighCD34+ bulge, alowCD34+ bulge, IRS, Hair
  Shaft cuticle/cortex.
- **Per-cell-type gold-set size**: PanglaoDB has 39-188 markers per
  mapped cell type vs CellMarker's 7-50 — denser per-cell-type catalog
  → higher baseline precision, lower recall ceiling.
- **Conclusion**: the two databases give **complementary** evaluation
  signal; reporting them separately (as the user mandated) is more
  informative than aggregating.

## What changed in the framework for this run

Three surgical edits in `agentcoop/`, all generic / additive:

1. **`agentcoop/core/repo_collaboration.py`**: dropped the
   hardcoded `dry_run=True` for sandbox builds; added
   `_build_runtime_image()` which builds (or reuses cached)
   `agentcoop-runtime:case-study`; rewrote `_run_adapter_in_docker`
   to `docker run --rm` per-call with bind-mounted workdir at the
   identical host path so request JSON paths resolve unchanged
   inside the container. Per-spec `runtime_image` override
   honoured (used by Path C for the R-based image).
2. **`agentcoop/wrappers/__main__.py`** (NEW): generic CLI
   entrypoint `python -m agentcoop.wrappers <agent> <invoke.json>`.
3. **`agentcoop/wrappers/cellmarker_evaluator_local/adapter.py`**:
   added `_resolve_panglaodb_file`, `_read_panglaodb_file`,
   `_build_panglaodb_gold_sets`, `_eval_against_gold` helpers; the
   main `invoke_*` adds a PanglaoDB block that produces 4 new
   artifacts (`panglaodb_gold_markers_by_celltype.json`,
   `panglaodb_label_mapping.csv`,
   `precision_recall_panglaodb_by_celltype.csv`,
   `precision_recall_panglaodb_summary.csv`) without touching the
   CellMarker code path.

Plus one new generic Dockerfile: `docker/agentcoop-runtime.Dockerfile`
(python:3.11-slim + agentcoop pkg + scientific stack, hosts every
Python adapter).

## Reproducing
```bash
colima start --runtime docker --cpu 6 --memory 10 --disk 120
set -a; source .secrets/api-key; set +a
docker build -f docker/agentcoop-runtime.Dockerfile -t agentcoop-runtime:case-study .
python -m agentcoop.cli collaborate \
  --request case_study_2.request.yaml \
  --workdir runs/case2/shareseq_skin_docker_b \
  --docker
```

## Path C reference
Real R-Seurat + R-Signac::GeneActivity numbers land at
`runs/case2/shareseq_skin_docker_c/` once the rocker/r-ver +
Bioconductor + Seurat + Signac image finishes building and the
fragments BED is sorted/bgzipped/tabix-indexed.
