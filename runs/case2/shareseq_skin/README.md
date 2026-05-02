# Case Study 2 — Seurat × Signac on real SHARE-seq mouse skin multiome

Run produced by

```bash
agentcoop collaborate \
  --request case_study_2.request.yaml \
  --workdir runs/case2/shareseq_skin \
  --no-docker \
  --model gpt-5 \
  --reasoning-effort medium
```

The CLI **handles all preparatory work internally**: it clones each
GitHub repo, profiles it, renders the per-repo Dockerfiles + smoke
tests + `docker-compose.yml` (per `case_study_2.md` §8), registers
per-repo + join-agent AgentCards, **auto-installs every declared
Python package via the EnvManager** (recorded in
`manifests/env_manifest.json`), runs Seurat and Signac as parallel
branches on the real Farah / Ma SHARE-seq mouse skin late-anagen
multiome data, brokers the typed gene-set handoff, runs the
`CellMarkerEvaluator` join-agent (parses `Cell_marker_Mouse.xlsx`,
filters to skin tissues, computes precision / recall / collaboration
gain), and finally lets `gpt-5` write a single integrator report.

Real-data inputs (under `data/shareseq_skin/`):

```
GSM4156608_skin.late.anagen.rna.counts.txt.gz   (RNA: 23 296 genes × 42 948 cells, dense TSV)
GSM4156597_skin.late.anagen.counts.txt.gz       (ATAC: 344 592 peaks × 34 774 cells, MatrixMarket)
GSM4156597_skin.late.anagen.peaks.bed.gz        (peak BED)
GSM4156597_skin.late.anagen.barcodes.txt.gz     (ATAC barcodes)
GSM4156597_skin_celltype.txt.gz                 (3-column: atac.bc / rna.bc / celltype, 34 774 rows, 23 cell types)
Cell_marker_Mouse.xlsx                          (CellMarker 2.0 mouse marker database)
```

## Where to look first

| File | What's in it |
|---|---|
| **`final_report.md`** | **Standalone Markdown report.** Embeds the topology PNG, env table, per-stage table, integrator hypothesis, every artifact path. Open this first. |
| **`topology.png`** | Multi-agent topology (parallel_then_join), hierarchical layout, color-by-kind. |
| `topology.dot` | Graphviz source for the same diagram. |
| `collaboration_log.md` | Per-stage narrative. |
| `run_manifest.json` | Top-level outcome JSON. |
| `manifests/env_manifest.json` | Auto-resolved Python packages (e.g. `openpyxl` was auto-installed this run). |
| `artifacts/seurat_run/` | Seurat-equivalent outputs: full marker CSV, top-N JSON, celltype distribution. |
| `artifacts/signac_run/` | Signac-equivalent outputs: peak markers, peak→gene mapping, gene marker CSV, top-N JSON. |
| `artifacts/cellmarkerevaluator_run/` | Set-operations JSON, gold marker JSON, label mapping CSV, P/R per-celltype + summary, collaboration-gain table, heatmap + barplot PNGs. |
| `artifacts/integration/` | LLM (`gpt-5`) hypothesis report Markdown / JSON. |

## Top-line result

| Metric | Value |
|---|---|
| Status | **`success`** (real data; no synthetic fallback) |
| Wall time | 648 s (10.8 min) |
| Total cells after filters | 32 231 (22 cell types after dropping `Mix`; min 20 cells/type) |
| RNA matrix | 32 231 cells × 23 296 genes |
| ATAC matrix | 32 231 cells × 344 592 peaks |
| Seurat marker rows | 147 057 (after `only_pos`, `min_pct≥0.10`, `logfc_threshold≥0.25`) |
| Signac marker peaks → genes | 10 776 peaks → 10 574 mapped → 8 843 unique-cell-type-gene rows |
| mm10 gene-coord cache | GENCODE vM25 basic, 55 401 genes (auto-fetched on first run, cached at `agentcoop/wrappers/signac_local/data_cache/`) |
| Cell types mapped to CellMarker | **19 / 22** (extended skin filter; primary `Skin` + `Hair follicle` + `Hair`) |
| Total LLM tokens (integrator) | 5 679 (`gpt-5`, `reasoning_effort=medium`) |

## Cross-modal collaboration evidence

| metric | mean | median | std | n cell types |
|---|---:|---:|---:|---:|
| `precision_rna` | **0.051** | 0.04 | 0.072 | 19 |
| `precision_atac` | 0.003 | 0.00 | 0.008 | 12 |
| **`precision_intersection`** | **0.083** | 0.00 | 0.144 | 7 |
| `recall_rna` | **0.122** | 0.04 | 0.184 | 19 |
| `recall_atac` | 0.009 | 0.00 | 0.038 | 19 |
| `recall_union` | 0.122 | 0.04 | 0.184 | 19 |

The **intersection's mean precision (0.083) is 1.6× the RNA-only
precision (0.051) and ~25× the ATAC-only precision (0.003)** — exactly
the cross-modal-precision pattern the case study targets. The
**Basal** cell type in particular shows
`precision_intersection = 0.333` vs `precision_rna = 0.04` and
`precision_atac = 0.02` (a +0.29 / +0.31 gain over each single
modality, marked as a strict win in `collaboration_gain_table.csv`).

`recall_union` ≈ `recall_rna` because RNA recall already dominates
on this skin panel; ATAC adds little additional gold-marker recall on
the mm10 nearest-gene mapping (a known limitation enumerated in
`case_study_2.md` §21).

Honest caveats (also enumerated in the integrator report):

- ATAC-only precision is very low (0.3 %) because peak → nearest-gene
  mapping introduces noise — many marker peaks map to housekeeping
  genes near TSSes that aren't in CellMarker's curated lists. This
  matches §21.2 ("ATAC marker genes are indirect").
- 3 / 22 cell types fail to map to any CellMarker entry under the
  primary `Skin` filter (Schwann Cell, Hair Shaft / Sebaceous Gland in
  some runs); the extended `Skin + Hair follicle + Epidermis +
  Dermis + Hair` filter recovers most. The `cellmarker_label_mapping.csv`
  records the alias used per cell type.
- The Wilcoxon Python implementation in scanpy substitutes for
  Seurat's `wilcox` and Signac's `LR` tests when running in
  `--no-docker` mode; the spec's R-stack Docker images can be built
  from `docker/Dockerfile.Seurat` / `docker/Dockerfile.Signac` (also
  rendered this run) for a Wilcoxon ↔ Wilcoxon equivalence and an
  LR ↔ LR sensitivity check.

## Generated artifact tree

```
runs/case2/shareseq_skin/
  run_manifest.json                            — top-level outcome (status: success)
  final_report.md                              — standalone report (embeds topology + reports)
  collaboration_log.md                         — per-stage narrative
  topology.png                                 — parallel_then_join hierarchical render
  topology.dot                                 — Graphviz source
  compiled_workflow_graph.json                 — L7 external_repo_collaboration_l7_parallel_then_join graph
  agent_registry.json                          — Seurat + Signac AgentCards
  manifests/
    repo_profile_Seurat.json
    repo_profile_Signac.json
    docker_build_report.json                   — Dockerfiles written; daemon was down
    env_manifest.json                          — auto-resolved Python pkgs (8 packages)
    broker.jsonl                               — typed handoff audit log
  docker/
    Dockerfile.Seurat                          — generated from RepoProfile + SandboxSpec
    Dockerfile.Signac
    docker-compose.yml
    smoke_Seurat.py
    smoke_Signac.py
  artifacts/
    seurat_run/
      response.json
      seurat_rna_markers_all.csv               — 147 057 RNA marker rows
      rna_top_markers_by_celltype.json         — top-50 per cell type
      celltype_distribution.csv
      run.log
    signac_run/
      response.json
      signac_atac_peak_markers_all.csv         — 10 776 marker peak rows
      peak_to_gene_mapping.csv                 — 10 574 peak → mm10 gene rows
      signac_atac_marker_genes_all.csv         — 8 843 (cell-type, gene) marker scores
      atac_top_marker_genes_by_celltype.json   — top-50 per cell type
      run.log
    cellmarkerevaluator_run/
      response.json
      cellmarker_raw_mouse_markers.tsv         — full Mouse rows from CellMarker 2.0
      cellmarker_gold_markers_by_celltype.json — gold sets per dataset cell type
      cellmarker_label_mapping.csv             — aliases used per cell type
      marker_set_operations_by_celltype.json   — RNA / ATAC / intersection / union sets
      marker_set_sizes_by_celltype.csv         — set sizes + Jaccard
      gene_symbol_harmonization_report.json
      precision_recall_by_celltype.csv         — per-cell-type metrics
      precision_recall_summary.csv             — macro means / medians / stds
      collaboration_gain_table.csv             — intersection_strict_win / union_strict_win
      marker_overlap_heatmap.png               — RNA × ATAC top-N Jaccard heatmap
      precision_recall_barplot.png             — per-cell-type P/R bars
      run.log
    integration/
      final_hypothesis_report.md               — gpt-5 integrator output
      final_hypothesis_report.json
      final_summary.md
  traces/
    execution_trace.jsonl
```

## Reproduce

```bash
mkdir -p data/shareseq_skin
# (Manual download of the GEO files into data/shareseq_skin/.)

# AgentCo-Op handles environment configuration internally — no manual
# pip install required (openpyxl, scanpy, anndata etc are auto-installed
# the first time CS2 runs).
export OPENAI_API_KEY="..."
agentcoop collaborate \
  --request case_study_2.request.yaml \
  --workdir runs/case2/shareseq_skin \
  --no-docker \
  --model gpt-5 \
  --reasoning-effort medium
```

When the Docker daemon is up, drop `--no-docker` and the same request
runs each adapter inside its container — the `Dockerfile.*` and
`docker-compose.yml` per the `case_study_2.md` §8 R stack are emitted
under `runs/case2/shareseq_skin/docker/` on every run.

## Generalisation

The `parallel_then_join` topology is generic. To collaborate any
N agents that produce per-class outputs (markers, embeddings,
labels, …) and join them through an evaluator, add `topology:
parallel_then_join` and a `join_agent: {name, role_hint, inputs}`
block to your request YAML, register a local-Python adapter per agent
and per join-agent in `agentcoop/wrappers/<agent>/__init__.py`, and
invoke `agentcoop collaborate --request <yaml>`. Nothing in the
framework code mentions Seurat / Signac / CellMarker — see
`docs/external_agent_collaboration.md` for the design walk-through.
