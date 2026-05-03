# CS2 — SHARE-seq mouse skin multiome (Path C: Docker, R/Bioconductor sandbox + dual-DB eval)

**Session 9, 2026-05-03.** End-to-end Docker run of CS2 with **real R Seurat ::FindAllMarkers** and **real R Signac peak-to-gene** marker discovery, plus independent precision/recall against **CellMarker 2.0** and **PanglaoDB**.

| Field | Value |
|---|---|
| Run dir | `runs/case2/shareseq_skin_docker_c/` |
| Topology | `parallel_then_join` (Seurat ‖ Signac → CellMarkerEvaluator → integrator) |
| Docker mode | **on** (Seurat & Signac via `agentcoop-r-runtime:case-study`; CellMarkerEvaluator via `agentcoop-runtime:case-study`) |
| Branch concurrency | 1 (env `AGENTCOOP_BRANCH_CONCURRENCY=1`) — serialised so both R containers fit on a 14 GB colima |
| Wall time | 58 min (Seurat 14 min + Signac 41 min + integrator 1.5 min) |
| Status | `success` |
| Cells / genes / peaks | 32 231 / 23 296 / 344 592 |
| Cell types after filters | 22 |
| LLM integrator | `gpt-5` (reasoning_effort=medium); 5 893 tokens |

## Branch outputs

| Branch | Backend | Numbers |
|---|---|---|
| Seurat | **R/Seurat 5.0.3** | `FindAllMarkers` (presto+wilcox, only.pos, min.pct=0.10, logfc.threshold=0.25). 32 231 cells × 23 296 genes; 22 cell types; **15 396 marker rows**. |
| Signac | **R/Signac 1.13.0** | `signac_method = peak_to_gene` (case_study_2.md §11 primary). 32 231 cells × 344 592 peaks → variable peaks via `FindTopFeatures(min.cutoff="q75")` (86 361 peaks) → stratified 4 000-cell subsample → `FindAllMarkers` → `ClosestFeature` mapping → **35 487 marker rows**. |

## CellMarker 2.0 evaluation (skin filter)
| Metric | Mean | Median | n cell types |
|---|---:|---:|---:|
| precision_rna | 0.0674 | (single-mode) | 19 |
| precision_atac | 0.0084 | — | 19 |
| **precision_intersection** | **0.0893** | — | (small set) |
| recall_rna | 0.1751 | — | 19 |
| recall_atac | 0.0090 | — | 19 |
| recall_union | 0.1772 | — | 19 |

**Strict cross-modal wins on CellMarker: intersection 2 / 19, union 3 / 19.**

## PanglaoDB evaluation (mouse, alias-mapped to dataset cell types)
| Metric | Mean | Median | n cell types |
|---|---:|---:|---:|
| precision_rna | 0.1253 | — | 15 |
| precision_atac | 0.0280 | — | 15 |
| **precision_intersection** | **0.1637** | — | (small set) |
| recall_rna | 0.0778 | — | 15 |
| recall_atac | 0.0119 | — | 15 |
| recall_union | 0.0858 | — | 15 |

**Strict cross-modal wins on PanglaoDB: intersection 2 / 15, union 7 / 15.**

The two databases give complementary evaluation signal — PanglaoDB shows more
union wins (recall) because its mouse marker catalog is denser per cell type;
CellMarker keeps tighter precision because its `Skin`-tagged entries are
biology-curated to skin specifically.

## What changed in the framework for Path C

All Sessions 4–8 numbers remain reproducible — these edits are purely additive
or are guarded by new YAML knobs that default to the old behaviour:

| File | Change |
|---|---|
| `agentcoop/core/repo_collaboration.py` | Drop hardcoded `dry_run=True`; build the generic `agentcoop-runtime` image once; `_run_adapter_in_docker` does `docker run --rm` per call with bind-mounted workdir, honours per-spec `runtime_image` overrides; respects `AGENTCOOP_BRANCH_CONCURRENCY` (default = parallel) and `AGENTCOOP_DOCKER_RUN_TIMEOUT_S` (default 7 200 s). |
| `agentcoop/wrappers/__main__.py` (NEW) | Generic CLI — `python -m agentcoop.wrappers <agent> <invoke.json>` — used by every Docker invocation. |
| `agentcoop/wrappers/seurat_local/seurat_rna_marker_agent.R` (NEW) | Real R Seurat wrapper. Reads sparse 10X-style MTX trio when `<cache>/rna_mtx/` exists (avoids R's 24 GB transient on the dense GEO TSV); falls back to `data.table::fread` otherwise. |
| `agentcoop/wrappers/signac_local/signac_atac_marker_agent.R` (NEW) | Real R Signac wrapper. Pulls cell barcodes from `atac_mtx/` MTX trio. Picks the celltype-table column with maximum overlap against MTX cell IDs (handles SHARE-seq's `rna.bc`-style ATAC matrix vs `atac.bc`-style fragments quirk). Builds the named cells vector that `CreateFragmentObject` needs. `signac_method` selector: `peak_to_gene` (default, fits 16 GB host) or `gene_activity` (requires colima ≥ 22 GB). Stratified subsample (`peak_marker_max_cells`, default 4 000) keeps `FindAllMarkers` tractable. |
| `agentcoop/wrappers/cellmarker_evaluator_local/adapter.py` | Additive PanglaoDB block — extra `_resolve_panglaodb_file`, `_read_panglaodb_file`, `_build_panglaodb_gold_sets`, `_eval_against_gold` helpers; produces 4 new artifacts (`panglaodb_*` + `precision_recall_panglaodb_*`); doesn't touch the CellMarker code path. |
| `docker/agentcoop-runtime.Dockerfile` (NEW) | Generic Python sandbox — hosts every Python adapter. |
| `docker/agentcoop-r-runtime.Dockerfile` (NEW) | rocker/r-ver 4.3.3 + Bioconductor 3.18 (`GenomicRanges`, `IRanges`, `GenomeInfoDb`, `Rsamtools`, `EnsDb.Mmusculus.v79`, `org.Mm.eg.db`, `biovizBase`) + Seurat 5.0.3 + Signac 1.13.0 + presto + R.utils. Sanity step verifies `GetGRangesFromEnsDb()` works. |
| `docker/agentcoop-r-dispatch.sh` (NEW) | Dispatcher matching the universal entrypoint contract — `<agent_name> <invoke.json>` → routes to the right Rscript. |
| `scripts/dense_tsv_to_mtx.py` (NEW) | One-shot Python helper that streams a dense gzipped genes×cells TSV into a 10X-style MTX trio. |
| `scripts/cs2_setup.sh` (NEW) | End-to-end setup: downloads GEO files, downloads PanglaoDB (with SpatialAgent fallback), builds the RNA MTX trio, assembles the ATAC MTX trio from the GEO sparse counts, sorts + bgzips + tabix-indexes the 5 GB fragments BED (with comma → dot barcode normalisation), builds both Docker images. **Idempotent — re-runs skip steps whose outputs already exist.** |

## End-to-end reproduction

The session 9 contract is **single-command reproduction**. From a fresh clone of `clean-dev`:

```bash
# 1. One-time host setup (the script tells you what to install if anything is
#    missing).
brew install colima docker docker-compose docker-buildx htslib coreutils
colima start --runtime docker --cpu 6 --memory 14 --disk 120
docker context use colima

# 2. Generate the OpenAI key file the orchestrator's integrator step needs.
mkdir -p .secrets && echo "OPENAI_API_KEY=sk-..." > .secrets/api-key

# 3. Idempotent data + image setup (~50 min on first run, seconds on reruns).
bash scripts/cs2_setup.sh

# 4. End-to-end run.
set -a; source .secrets/api-key; set +a
export AGENTCOOP_BRANCH_CONCURRENCY=1   # serialise R Seurat + R Signac on a 14 GB colima
python -m agentcoop.cli collaborate \
    --request case_study_2.request.yaml \
    --workdir runs/case2/shareseq_skin_docker_c \
    --docker
```

Expected wall time after data + images are cached: **~55–60 min**
(Seurat 14 min, Signac 41 min, integrator 1–2 min).

## Why `signac_method = peak_to_gene` instead of GeneActivity (the user's prose)

`case_study_2.md` §11 itself names peak-to-nearest-gene as the **primary**
ATAC marker-gene method, with `Signac::GeneActivity` (§11.2) as an optional
sensitivity analysis. The session-9 user prose did mark GeneActivity primary;
that path is still wired (set `signac_method: gene_activity` in
`case_study_2.request.yaml`'s `parameters.atac_marker_method` block), but it
takes a hard memory wall on a 16 GB Mac:

| Step | RAM peak |
|---|---:|
| Read sparse MTX | ~ 1.0 GB |
| `CreateChromatinAssay` + TFIDF + SVD | ~ 4 GB |
| **`Signac::GeneActivity` "extracting reads overlapping genomic regions"** | **> 14 GB → OOM (rc=137)** |

So the default end-to-end run uses peak-to-gene (which does fit) and
documents GeneActivity as opt-in for hosts with colima ≥ 22 GB. Both code
paths are exercised in the wrapper — the YAML knob picks at runtime.

## Comparison vs Path B (`runs/case2/shareseq_skin_docker_b/`)

| Aspect | Path B (Python adapters in Docker) | Path C (real R Seurat + R Signac) |
|---|---|---|
| Seurat backend | scanpy `rank_genes_groups(method="wilcoxon")` | `Seurat::FindAllMarkers(test.use="wilcox")` (presto-accelerated) |
| Seurat marker rows | 147 057 (unfiltered scanpy output) | 15 396 (Seurat's logfc.threshold + min.pct filter) |
| Signac backend | scipy + pandas peak-to-gene | `Signac::FindAllMarkers` on top-q75 variable peaks + 4 000-cell stratified subsample, then `Signac::ClosestFeature` |
| Signac marker rows | 8 843 | 35 487 |
| CellMarker int wins | 1 / 19 | **2 / 19** |
| PanglaoDB int wins | 4 / 15 | 2 / 15 |
| PanglaoDB uni wins | 4 / 15 | **7 / 15** |
| Wall time | 31 min | 58 min |

Both runs are kept on disk — Path B is the Python-only baseline, Path C is the real R-tool run that case_study_2.md §0 originally asked for.
