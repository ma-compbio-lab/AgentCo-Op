# Case Study 2 (human heart) — GSE270788 MA7 Seurat × Signac collaboration

**Date:** 2026-05-05
**Spec:** `case_study_2_human_heart.md`
**Request:** `case_study_2_human_heart.request.yaml`
**Run command:**

```bash
set -a; source .secrets/api-key; set +a
export AGENTCOOP_BRANCH_CONCURRENCY=1
python -m agentcoop.cli collaborate \
  --request case_study_2_human_heart.request.yaml \
  --workdir runs/case2/human_heart_ma7_docker \
  --docker
```

**Outcome:** **success** (status=success, n_handoffs=2, elapsed=540.6s).

---

## 1. Dataset

- **GEO series:** GSE270788 — Targeting Immune-Fibroblast Crosstalk in Myocardial Infarction and Cardiac Fibrosis III
- **Sample:** MA7 (ICM disease state)
- **GEX (GSM8352050):** `data/heart_human/MA7/GSM8352050_MA7_filtered_feature_bc_matrix.h5` (13 MB; 597 cells × 36 601 genes / 76 162 peaks)
- **ATAC fragments (GSM8352073):** `data/heart_human/MA7/GSM8352073_MA7_atac_fragments.tsv.gz` (1.4 GB; tabix-indexed in place)
- **Metadata (series-level):** `data/heart_human/GSE270788_metadata.csv.gz` (46 384 rows across 24 samples; 244 rows for MA7)

After QC + barcode matching: **190 cells**, **4 cell types** with ≥ 20 cells each (Endothelium 57, Cardiomyocyte 52, Myeloid 44, Fibroblast 37). Pericyte / Endocardium / Lymphatic dropped (each < 20 cells in MA7).

---

## 2. Pipeline

```
User request YAML
        ↓
Repo profiler  (Seurat + Signac GitHub at pinned commits)
        ↓
Docker sandbox builder  (agentcoop-r-runtime:case-study, hg38-aware leaf layer)
        ↓
Agent registry  (Seurat → R/Seurat container; Signac → R/Signac container)
        ↓
   ┌───────────┴────────────┐
   ↓                        ↓
RNA Marker Agent       ATAC Marker Agent
Seurat::FindAllMarkers  Signac::GeneActivity
on Gene Expression       → Seurat::FindAllMarkers
   ↓                        ↓
   └───────→ Artifact Broker (typed JSON handoffs)
                ↓
       CellMarkerEvaluator
       set ops + CellMarker 2.0 + PanglaoDB precision/recall
                ↓
       Integrator (gpt-5)
       final hypothesis report
```

The compiled topology PNG is at `topology.png`; DOT source at `topology.dot`.

---

## 3. Headline results — does the hypothesis hold?

The pre-registered hypothesis (`case_study_2_human_heart.md` §18.1):

```
precision_intersection > precision_rna > precision_atac
recall_union           > recall_rna   > recall_atac
```

### CellMarker 2.0 (human heart filter; 2 cell types mapped — Cardiomyocyte, Fibroblast)

| Metric | Value (mean) | Ranking |
|---|---:|---|
| precision_intersection | 0.369 |  ← largest |
| precision_rna | 0.120 | |
| precision_atac | 0.110 | ← smallest |
| recall_union | 0.291 | ← largest |
| recall_rna | 0.229 | |
| recall_atac | 0.189 | ← smallest |

**Hypothesis HOLDS** for both precision and recall. Both Cardiomyocyte and Fibroblast show strict-win for `intersection_strict_win` AND `union_strict_win`.

### PanglaoDB (Hs heart filter; 3 cell types mapped — Cardiomyocyte, Fibroblast, Myeloid)

| Metric | Value (mean) | Ranking |
|---|---:|---|
| precision_intersection | 0.492 | ← largest |
| precision_rna | 0.247 | |
| precision_atac | 0.227 | ← smallest |
| recall_union | 0.144 | ← largest |
| recall_rna | 0.092 | |
| recall_atac | 0.089 | ← smallest |

**Hypothesis HOLDS** for both precision and recall. 2 / 3 cell types show strict-win (Cardiomyocyte and Fibroblast); Myeloid is mapped to PanglaoDB only and contributes to the macro means.

---

## 4. Per-cell-type precision/recall (CellMarker 2.0 main result)

| Cell type | n_gold | n_rna | n_atac | n_int | n_union | P_rna | P_atac | P_int | R_rna | R_atac | R_union |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Cardiomyocyte | 42 | 50 | 50 | 11 | 89 | 0.120 | 0.140 | **0.364** | 0.143 | 0.167 | **0.214** |
| Fibroblast | 19 | 50 | 50 | 8 | 92 | 0.120 | 0.080 | **0.375** | 0.316 | 0.211 | **0.368** |
| Endothelium | 0 | 50 | 50 | 13 | 87 | — | — | — | — | — | — |
| Myeloid | 0 | 50 | 50 | 11 | 89 | — | — | — | — | — | — |

Endothelium / Myeloid have `n_gold = 0` for CellMarker heart-filtered → excluded from the macro mean, per the design rule "never compute recall using a zero-size DB marker set" (spec §11).

PanglaoDB maps Myeloid (n_gold = 15) so it's included in the PanglaoDB macro.

---

## 5. Token / cost / time

| Stage | Wall | Notes |
|---|---:|---|
| Repo profiler + sandbox builder | ~ 30 s | container reuse from cache |
| Seurat (R) FindAllMarkers | ~ 13 s | 190 cells × 36 601 genes |
| Signac (R) GeneActivity → FindAllMarkers | ~ 9 min | dominated by `GeneActivity()` (~ 7 min) |
| CellMarkerEvaluator + figures | ~ 20 s | both DBs, 2 figures |
| Integrator (`gpt-5`) | ~ 15 s | summary report |
| **Total** | **540.6 s** (~ 9 min) | single MA7 sample |

LLM tokens: ~ a few thousand (planner + integrator only — wrapper code is deterministic R).

---

## 6. Reproducibility

```bash
# 1. Setup data + docker images (idempotent; first run downloads ~1.5 GB)
bash scripts/cs2_human_heart_setup.sh

# 2. Run end-to-end
set -a; source .secrets/api-key; set +a
export AGENTCOOP_BRANCH_CONCURRENCY=1
python -m agentcoop.cli collaborate \
  --request case_study_2_human_heart.request.yaml \
  --workdir runs/case2/human_heart_ma7_docker \
  --docker
```

Total cold-start wall time: ~ 25 min (data download ~ 5 min, image rebuild ~ 10 min if image is missing, pipeline ~ 9 min). Steady-state (data cached, image cached): ~ 9 min.

---

## 7. Artifact tree

```
runs/case2/human_heart_ma7_docker/
├── final_report.md                         AC-generated end-to-end report
├── run_manifest.json                        full provenance
├── compiled_workflow_graph.json             AC's compiled blueprint
├── agent_registry.json                      registered agents
├── collaboration_log.md                     human-readable log
├── topology.png + topology.dot              compiled topology figure
├── manifests/                               repo profiles, env manifest, broker
├── docker/                                  generated Dockerfile + compose
├── traces/                                  per-stage event traces
├── _invoke/                                 invoke JSONs sent to each adapter
├── wrappers/                                wrapper-side run.log mirrors
└── artifacts/
    ├── seurat_run/
    │   ├── seurat_rna_markers_all.csv          3 129 markers
    │   ├── rna_top_markers_by_celltype.json    top-50 / cell type
    │   ├── celltype_distribution.csv           4 cell types
    │   └── run.log
    ├── signac_run/
    │   ├── signac_gene_activity_markers_all.csv (4 144 markers)
    │   ├── atac_top_marker_genes_by_celltype.json
    │   └── run.log
    ├── cellmarkerevaluator_run/
    │   ├── precision_recall_by_celltype.csv
    │   ├── precision_recall_summary.csv
    │   ├── precision_recall_panglaodb_by_celltype.csv
    │   ├── precision_recall_panglaodb_summary.csv
    │   ├── collaboration_gain_table.csv
    │   ├── cellmarker_label_mapping.csv
    │   ├── panglaodb_label_mapping.csv
    │   ├── cellmarker_gold_markers_by_celltype.json
    │   ├── panglaodb_gold_markers_by_celltype.json
    │   ├── marker_set_operations_by_celltype.json
    │   ├── marker_set_sizes_by_celltype.csv
    │   ├── marker_overlap_heatmap.png
    │   ├── precision_recall_barplot.png
    │   └── run.log
    └── integration/
        ├── final_hypothesis_report.md
        ├── final_hypothesis_report.json
        └── final_summary.md
```

---

## 8. Notes / limitations

- **Sample MA7 is small.** 597 raw cells, 244 with author-provided cell-type labels, 190 retained at `min_cells_per_type=20`. The expected ordering still emerges cleanly because the gold-set marker lists are the same regardless of n_cells.
- **CellMarker 2.0 heart coverage is sparse.** Only Cardiomyocyte (42 markers) and Fibroblast (19) had non-empty heart-filtered marker sets. Endothelium / Myeloid mapped to 0 markers under the strict heart filter — they would map under broader tissue filters. Per the spec, we did NOT broaden to force the macro mean up.
- **PanglaoDB has better heart coverage** for the cell types in MA7 (Cardiomyocyte 104, Fibroblast 178, Myeloid 15 markers).
- **Pericyte / Endocardium / Lymphatic** dropped at the cell-count threshold; they would survive on a larger sample (`MA5` donor or the full all-sample run from spec §2.3).
- **The full all-sample run** (24 samples) would aggregate per-sample-per-cell-type metrics and likely strengthen the hypothesis result. We ran the MA7 pilot per spec §2.2.

---

## 9. What was changed in the codebase to enable this run

Per the user constraint ("no major code modifications; if any, must be general-purpose"):

| File | Change | Purpose |
|---|---|---|
| `docker/agentcoop-r-runtime.Dockerfile` | Added hg38 leaf layer (`EnsDb.Hsapiens.v86`, `BSgenome.Hsapiens.UCSC.hg38`, `org.Hs.eg.db`) and `hdf5r` | Generic — same image now serves mouse mm10 + human hg38 |
| `agentcoop/wrappers/seurat_local/seurat_rna_marker_agent.R` | Added `dataset.format` switch (`tenx_h5_multiome` path: `Read10X_h5` + `$Gene Expression`); generic `find_candidate_col` for column priority; metadata-coverage barcode-overlap check | Generic — same wrapper handles SHARE-seq dense TSV and 10x H5 multiome |
| `agentcoop/wrappers/signac_local/signac_atac_marker_agent.R` | Same `dataset.format` switch (10x path: `Read10X_h5` + `$Peaks`, hg38 annotation, GeneActivity primary); `load_ensdb_for_genome` helper; CreateChromatinAssay parameterized by genome | Generic — both genomes supported, both paths preserved |
| `agentcoop/wrappers/cellmarker_evaluator_local/adapter.py` | Added `_HUMAN_HEART_ALIASES` table (auto-selected when `organism="Human"`); generalized `cellmarker_raw_markers.tsv` artifact name; tissue-aware `filter_mode` label; widened CellMarker file fallback paths | Generic — request YAML's `aliases`/`organism`/`tissue_filter_*` drive everything; default heart aliases ship as a fallback |

| New file | Purpose |
|---|---|
| `scripts/cs2_human_heart_setup.sh` | Idempotent GSE270788 download + tabix index + image check |
| `case_study_2_human_heart.request.yaml` | Canonical request driving `agentcoop collaborate` |

No changes to AC backbone (`agentcoop/core/`). No changes to the SHARE-seq path (`scripts/cs2_setup.sh`, `case_study_2.request.yaml`) — both case studies coexist.

Tests after all changes: **144 / 144 pass.**
