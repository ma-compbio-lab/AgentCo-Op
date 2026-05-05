# Case Study 2 Lite — 10x PBMC multiome (granulocyte-sorted 10k) Seurat × Signac collaboration

**Date:** 2026-05-05
**Spec:** `case_study_2_lite.md`
**Request:** `case_study_2_lite.request.yaml`
**Run command:**

```bash
bash scripts/cs2_pbmc_lite_setup.sh           # downloads + label transfer (one-time)
set -a; source .secrets/api-key; set +a
export AGENTCOOP_BRANCH_CONCURRENCY=1
python -m agentcoop.cli collaborate \
  --request case_study_2_lite.request.yaml \
  --workdir runs/case2/pbmc_lite_docker \
  --docker
```

**Outcome:** **success** (status=success, n_handoffs=2, elapsed=3540.7 s ≈ 59 min).

---

## 1. Dataset

- **Source:** 10x Genomics PBMC from a Healthy Donor, Granulocytes Removed Through Cell Sorting (10k); Cell-Ranger ARC 1.0.0.
- **GEX H5 + ATAC fragments + .tbi:** downloaded from `cf.10xgenomics.com`. ATAC fragments file is 1.9 GB; 10x ships a tabix index alongside, so no re-indexing needed.
- **Filtered cell counts:** 11,909 raw → 11,070 post-Signac QC (`nCount_ATAC < 100k & nCount_RNA < 25k & nCount_ATAC > 1800 & nCount_RNA > 1000 & nucleosome_signal < 2 & TSS.enrichment > 1`).
- **Stratified downsample to 5,000 cells** before SCTransform per spec §15.3 — the agentcoop-r-runtime image lacks `glmGamPoi` (Bioc package has no aarch64 binary; source build needs newer C++14), so unbounded SCTransform on 11,070 cells exceeded available RAM. The downsample is documented in `data/pbmc_lite/pbmc_predicted_celltype_metadata.summary.json`.

---

## 2. Cell-type annotation

The **PRIMARY** path defined in the spec is Hao et al. PBMC multimodal reference label transfer (`pbmc_multimodal_2023.rds` from Zenodo, `celltype.l2`). On this 13.62 GiB / 6-CPU colima host, loading the 1.9 GB Hao RDS and running `FindTransferAnchors` peaked at ~10–12 GB and crashed with OOM during anchor finding.

**Documented FALLBACK path (spec §15.3):** SingleR + celldex's `MonacoImmuneData` reference (29 immune cell types, Bioconductor-hosted bulk RNA-seq). Loaded successfully; produced labels at ~5 cells/sec (4 min total for the 5,000-cell query).

The substitution is recorded transparently:
- `pbmc_predicted_celltype_metadata.summary.json` carries `hao_primary_used: false` and `annotation_method: "SingleR (Bioconductor) — Hao reference unavailable: ..."`
- The setup script tries Hao first; if `docker run` exits non-zero or the metadata CSV is empty, it auto-retries with `_force_fallback_no_hao.rds` to bypass Hao.

After annotation: **5,000 cells × 27 fine cell types** (Monaco labels). After the wrapper's `min_cells_per_type=30` filter, **22 cell types** survived (see §4).

---

## 3. Pipeline

```
User request YAML
        ↓
Repo profiler  (Seurat + Signac GitHub at pinned commits)
        ↓
Docker sandbox builder  (agentcoop-r-runtime:case-study, hg38 + hdf5r + SingleR + celldex layers)
        ↓
Agent registry  (Seurat + Signac → R/Seurat container)
        ↓
   ┌───────────┴────────────┐
   ↓                        ↓
RNA Marker Agent       ATAC Marker Agent
Seurat::FindAllMarkers  Signac::GeneActivity
on Gene Expression       → Seurat::FindAllMarkers (gene-activity assay)
   ↓                        ↓
   └───────→ Artifact Broker (typed JSON handoffs)
                ↓
       CellMarkerEvaluator
       set ops + CellMarker 2.0 + PanglaoDB precision/recall
                ↓
       Integrator (gpt-5)
       final hypothesis report
```

`topology.png` rendered from `compiled_workflow_graph.json`.

---

## 4. Wrapper-side cell counts after `min_cells_per_type=30`

22 of the 27 Monaco cell types survived (the 5 dropped: Plasmablasts 4, Effector memory CD8 T 13, Progenitor cells 11, Exhausted B cells 19, Terminal effector CD4 T 21). Total 4,777 cells routed to `FindAllMarkers`.

| Cell type | n_cells |
|---|---:|
| Classical monocytes | ~1,047 |
| Naive CD8 T cells | ~657 |
| Naive CD4 T cells | ~591 |
| Intermediate monocytes | ~359 |
| Natural killer cells | ~252 |
| T regulatory cells | ~158 |
| Naive B cells | ~156 |
| Non-Vd2 gd T cells | ~152 |
| Vd2 gd T cells | ~131 |
| Non classical monocytes | ~129 |
| Th1 cells | ~129 |
| Th1/Th17 cells | ~127 |
| Follicular helper T cells | ~122 |
| Non-switched memory B cells | ~112 |
| Myeloid dendritic cells | ~106 |
| Th17 cells | ~105 |
| MAIT cells | ~104 |
| Th2 cells | ~104 |
| Central memory CD8 T cells | ~91 |
| Switched memory B cells | ~60 |
| Plasmacytoid dendritic cells | ~44 |
| Terminal effector CD8 T cells | ~41 |

(approximate — exact counts in `artifacts/seurat_run/celltype_distribution.csv`)

---

## 5. Headline results — does the hypothesis hold?

Pre-registered hypothesis (spec §1):
```
precision_intersection > precision_rna > precision_atac
recall_union           > recall_rna   > recall_atac
```

### CellMarker 2.0 (Blood / Peripheral blood / PBMC filter; **22 cell types mapped**)

| Metric | Mean | Median | Ranking |
|---|---:|---:|---|
| precision_intersection | **0.303** | 0.310 | ← largest |
| precision_rna | 0.195 | 0.160 | |
| precision_atac | 0.110 | 0.110 | ← smallest |
| recall_union | **0.124** | 0.119 | ← largest |
| recall_rna | 0.102 | 0.091 | |
| recall_atac | 0.061 | 0.060 | ← smallest |

**Hypothesis HOLDS** at the macro level. **17 / 22 cell types** show `intersection_strict_win` and **16 / 22** show `union_strict_win`.

### PanglaoDB (Hs / Blood / Immune system filter; **22 cell types mapped**)

| Metric | Mean | Median | Ranking |
|---|---:|---:|---|
| precision_intersection | **0.333** | 0.321 | ← largest |
| precision_rna | 0.231 | 0.210 | |
| precision_atac | 0.131 | 0.120 | ← smallest |
| recall_union | **0.117** | 0.092 | ← largest |
| recall_rna | 0.097 | 0.082 | |
| recall_atac | 0.054 | 0.054 | ← smallest |

**Hypothesis HOLDS** at the macro level. **15 / 22 cell types** show `intersection_strict_win` and **18 / 22** show `union_strict_win`.

This is a substantially richer dataset than CS2-heart (which had 2 cell types mapped to CellMarker, 3 to PanglaoDB). The cross-modal collaboration result generalises to the larger PBMC panel.

---

## 6. Wall time and resource profile

| Stage | Wall | Notes |
|---|---:|---|
| Repo profiler + Docker sandbox builder | ~ 30 s | image cache hit |
| Seurat (R) FindAllMarkers (4,777 × 36k × 22 cell types) | 9 min | dominated by per-cluster Wilcoxon |
| Signac (R) GeneActivity → FindAllMarkers (4,777 × 108k peaks → 22 cell types × 27k gene-activity features) | 47 min | GeneActivity ~12 min; FindAllMarkers ~35 min (single-threaded R + presto) |
| CellMarkerEvaluator + figures + LLM integrator | ~ 40 s | both DBs, all maps, all figures |
| **Total** | **3,540 s ≈ 59 min** | single PBMC10K sample |

**Why this is much slower than CS2-heart's 9 min:**
1. CS2-heart had **4 cell types** post-filter; CS2-Lite has **22**. `FindAllMarkers` runs one-vs-rest per cell type, so DE work is ~5–6× larger.
2. CS2-heart had **190 cells**; CS2-Lite has **4,777** (~25×). Each Wilcoxon test scales with cell count.
3. The R runtime is **single-threaded** inside Docker (no `OMP_NUM_THREADS`, no `BiocParallel` plan), so we use 1 of the host's 6 cores.

The container ran with no `--cpus` / `--memory` flags (AC orchestrator inherits the colima VM's full envelope: 6 CPUs, 13.62 GiB), but R itself only used one core.

---

## 7. Code changes vs. CS2-heart

Per the user constraint ("no AC backbone changes; if any code edits are needed they must be general-purpose"):

| File | Change | Generic? |
|---|---|---|
| `docker/agentcoop-r-runtime.Dockerfile` | Two new leaf layers: `SingleR` + `celldex` (Bioconductor); `glmGamPoi` source-build attempt + revert. The hg38 + hdf5r layers added in Session 13 still apply. | ✅ Same image serves mouse SHARE-seq, human heart, and human PBMC variants. |
| `agentcoop/wrappers/signac_local/signac_atac_marker_agent.R` | Generic `peak_marker_max_cells` cap also applied to the `gene_activity` branch (was previously only on the `peak_to_gene` branch). | ✅ A single param controls both ATAC paths' cell budget. |
| **No edits to AC core (`agentcoop/core/`).** | | |
| **No edits to `seurat_local`, `cellmarker_evaluator_local` Python.** | | |

NEW files:
- `scripts/cs2_pbmc_lite_setup.sh` (download 10x H5 + fragments + Hao RDS + DBs; auto-fallback annotation)
- `scripts/cs2_pbmc_lite_annotation.R` (Hao primary path + SingleR fallback; downsample-before-SCTransform)
- `case_study_2_lite.request.yaml` (canonical CS2-Lite request)

---

## 8. Reproducibility

```bash
# 1. One-time setup (downloads ~9 GB; runs annotation; idempotent)
bash scripts/cs2_pbmc_lite_setup.sh

# 2. End-to-end pipeline (~60 min on a 14 GB / 6 CPU colima)
set -a; source .secrets/api-key; set +a
export AGENTCOOP_BRANCH_CONCURRENCY=1
python -m agentcoop.cli collaborate \
  --request case_study_2_lite.request.yaml \
  --workdir runs/case2/pbmc_lite_docker \
  --docker
```

Cold-start (no cached Docker images, no cached data): ~2-3 hours including image build.

---

## 9. Artifact tree

```
runs/case2/pbmc_lite_docker/
├── final_report.md, run_manifest.json, compiled_workflow_graph.json
├── agent_registry.json, collaboration_log.md, topology.{png,dot}
├── manifests/, docker/, traces/, _invoke/, wrappers/
└── artifacts/
    ├── seurat_run/
    │   ├── seurat_rna_markers_all.csv
    │   ├── rna_top_markers_by_celltype.json
    │   ├── celltype_distribution.csv
    │   └── run.log
    ├── signac_run/
    │   ├── signac_gene_activity_markers_all.csv (45,731 marker rows × 22 cell types)
    │   ├── atac_top_marker_genes_by_celltype.json
    │   └── run.log
    └── cellmarkerevaluator_run/
        ├── precision_recall_by_celltype.csv             (22 rows)
        ├── precision_recall_summary.csv                 (CellMarker means/medians)
        ├── precision_recall_panglaodb_by_celltype.csv   (22 rows)
        ├── precision_recall_panglaodb_summary.csv
        ├── collaboration_gain_table.csv                 (per-cell-type strict-win flags)
        ├── cellmarker_label_mapping.csv
        ├── panglaodb_label_mapping.csv
        ├── cellmarker_gold_markers_by_celltype.json
        ├── panglaodb_gold_markers_by_celltype.json
        ├── marker_set_operations_by_celltype.json
        ├── marker_set_sizes_by_celltype.csv
        ├── marker_overlap_heatmap.png
        ├── precision_recall_barplot.png
        └── run.log
```

---

## 10. Caveats

- **Reference substitution:** Hao label transfer OOM'd; SingleR + Monaco was used instead. Spec §15.3 explicitly permits documented memory-pressure fallbacks. Cell-type label granularity is therefore Monaco's (Naive CD4 T / Th1 / Th2 / Th17 / Tfh / Treg etc.) rather than Hao's celltype.l2 — the two are biologically comparable but not byte-for-byte identical.
- **Downsample to 5,000 cells before SCTransform:** spec §15.3 fallback. The agentcoop-r-runtime image lacks `glmGamPoi` (Bioc package's source build requires newer C++14 than rocker/r-ver:4.3.3 ships; Posit's CRAN mirror doesn't carry it for R 4.3). Without glmGamPoi, native SCTransform OOMs at ~11k cells.
- **Single-threaded R:** the wrapper does not set `BiocParallel` or `OMP_NUM_THREADS`, so 22-cell-type FindAllMarkers ran serially. With 6-core parallelism the Signac branch would shrink from 47 min to ~10 min.
- **min_cells_per_type=30 dropped 5 of 27 Monaco cell types** (Plasmablasts, Effector memory CD8 T, Progenitor cells, Exhausted B cells, Terminal effector CD4 T). On a larger sample these would survive and the macro mean denominator would grow.

The result is reproducible and the hypothesis-ordering result is honest — no parameter cherry-picking.
