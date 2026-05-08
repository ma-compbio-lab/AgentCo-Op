# Case Study 2: AgentCo-Op Orchestrates Seurat and Signac as Sandboxed Tool-Agent Nodes for SHARE-seq Skin Multiome Marker Analysis

## 0. One-sentence summary

This case study tests whether **AgentCo-Op** can accept external tool GitHub URLs, public multiome data links, cell-type labels, and a natural-language task description, then autonomously build Docker sandboxes and coordinate **Seurat** and **Signac** as specialized execution nodes to produce cross-modal cell-type marker sets and evaluate them against **CellMarker 2.0**.

---

## 1. Scientific and systems goal

### 1.1 Scientific task

Given paired SHARE-seq mouse skin multiome data, identify cell-type marker genes from two different molecular modalities:

```text
RNA expression evidence      -> Seurat -> rna_markers per cell type
Chromatin accessibility data -> Signac -> atac_markers per cell type
```

Then perform set operations for each cell type:

```text
intersection = rna_markers ∩ atac_markers
union        = rna_markers ∪ atac_markers
```

Finally, evaluate the RNA-only, ATAC-only, intersection, and union marker sets against **CellMarker 2.0** mouse skin marker annotations:

```text
precision_rna
precision_atac
precision_intersection

recall_rna
recall_atac
recall_union
```

The expected biological pattern is not a fixed numeric score. The experiment should test whether cross-modal collaboration produces interpretable marker-set behavior:

- the **intersection** may improve precision because a gene must be supported by both expression and chromatin accessibility evidence;
- the **union** may improve recall because it combines complementary evidence from transcriptome and chromatin accessibility;
- some cell types may not improve due to sparse ATAC signal, imperfect peak-to-gene mapping, or incomplete CellMarker coverage.

### 1.2 Systems task

This is primarily a **tool-as-agent collaboration** case study. AgentCo-Op should show that it can:

1. receive the following tool URLs:
   - Seurat GitHub: `https://github.com/satijalab/seurat`
   - Seurat tutorial: `https://satijalab.org/seurat/articles/pbmc3k_tutorial`
   - Signac GitHub: `https://github.com/stuart-lab/signac`
   - Signac multiomic tutorial: `https://stuartlab.org/signac/articles/pbmc_multiomic`
   - Signac mouse brain / scATAC tutorial: `https://stuartlab.org/signac/articles/mouse_brain_vignette`
2. inspect the repositories and documentation;
3. synthesize Docker sandboxes for Seurat and Signac;
4. wrap each tool as a callable agent node with a typed input-output schema;
5. coordinate the two nodes through a high-level workflow;
6. pass typed artifacts between nodes;
7. run deterministic set operations and database-based evaluation;
8. produce a final report with a complete trace, workflow graph, environment manifest, and reproducible code.

The intended claim is:

> A single tool can identify marker genes from one modality, but it cannot by itself perform a full cross-modal marker concordance workflow. AgentCo-Op composes independently developed tools into a collaborative system that produces cross-modal outputs that neither tool produces alone.

---

## 2. Public inputs

### 2.1 Dataset: SHARE-seq mouse skin late anagen multiome data

Use the mouse skin late-anagen SHARE-seq data from GEO series **GSE140203**.

The user-facing input should contain the exact URLs below.

#### RNA count matrix

GEO sample page:

```text
https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM4156608
```

Processed RNA count file:

```text
https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSM4156608&file=GSM4156608_skin.late.anagen.rna.counts.txt.gz&format=file
```

Expected file name after download:

```text
GSM4156608_skin.late.anagen.rna.counts.txt.gz
```

This file is the RNA-seq count matrix. The GEO page states that counts are indexed by genes or peaks as rows and cells as columns.

#### ATAC peak count matrix and metadata

GEO sample page:

```text
https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM4156597
```

Processed ATAC files:

```text
https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSM4156597&file=GSM4156597_skin.late.anagen.counts.txt.gz&format=file
https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSM4156597&file=GSM4156597_skin.late.anagen.peaks.bed.gz&format=file
https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSM4156597&file=GSM4156597_skin.late.anagen.barcodes.txt.gz&format=file
https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSM4156597&file=GSM4156597_skin_celltype.txt.gz&format=file
```

Optional full fragment file, only needed for the optional gene-activity sensitivity analysis:

```text
https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSM4156597&file=GSM4156597_skin.late.anagen.atac.fragments.bed.gz&format=file
```

Expected file names:

```text
GSM4156597_skin.late.anagen.counts.txt.gz
GSM4156597_skin.late.anagen.peaks.bed.gz
GSM4156597_skin.late.anagen.barcodes.txt.gz
GSM4156597_skin_celltype.txt.gz
GSM4156597_skin.late.anagen.atac.fragments.bed.gz  # optional; large
```

### 2.2 Cell-type labels

Use:

```text
GSM4156597_skin_celltype.txt.gz
```

The GEO description states that `celltype.txt` contains cell-type annotation and corresponding ATAC and RNA barcode information. AgentCo-Op should not assume fixed column names. The dataset schema inspector should infer which columns correspond to:

```text
rna_barcode
atac_barcode
cell_type / cluster_label
```

If multiple candidate columns exist, the agent should choose the mapping that maximizes barcode overlap with the RNA and ATAC count matrices.

### 2.3 CellMarker 2.0 reference database

CellMarker 2.0 download page:

```text
http://www.bio-bigdata.center/CellMarker_download.html
```

Alternative currently reachable mirror:

```text
https://xteam.xbio.top/CellMarker/download.jsp
```

Recommended file:

```text
Mouse cell markers
```

If the mirror provides multiple files, use the mouse-specific marker file if available. If not, use the all-marker file and filter for mouse entries.

### 2.4 Tools

```yaml
tools:
  - name: Seurat
    github_url: https://github.com/satijalab/seurat
    documentation:
      - https://satijalab.org/seurat/articles/pbmc3k_tutorial
      - https://satijalab.org/seurat/reference/findmarkers
    intended_node: rna_marker_agent

  - name: Signac
    github_url: https://github.com/stuart-lab/signac
    documentation:
      - https://stuartlab.org/signac/articles/pbmc_multiomic
      - https://stuartlab.org/signac/articles/mouse_brain_vignette
      - https://stuartlab.org/signac/reference/geneactivity
    intended_node: atac_marker_agent
```

---

## 3. Why this case study is useful for AgentCo-Op

This experiment is a clean demonstration of **specialized tool collaboration**.

Seurat is an R toolkit for single-cell genomics and is the natural execution backend for RNA marker discovery. Signac is an R toolkit for single-cell chromatin data and is the natural execution backend for ATAC peak analysis, differential accessibility, peak annotation, and optional gene-activity analysis.

The task requires collaboration because the final answer is not a standard Seurat output or a standard Signac output. It requires:

1. RNA marker discovery from Seurat;
2. ATAC marker-gene discovery from Signac;
3. barcode and cell-type alignment across modalities;
4. gene-symbol harmonization;
5. per-cell-type set operations;
6. CellMarker-based precision and recall evaluation;
7. integrated reporting.

Therefore this case study directly supports the AgentCo-Op claim that **a high-level agent can compile a workflow from externally developed tools and coordinate them to solve a task that no single tool directly solves**.

---

## 4. Recommended AgentCo-Op input prompt

### 4.1 Main prompt

Use the following prompt as the main case-study input.

```text
You are given two external tool repositories, their tutorials, a public SHARE-seq mouse skin multiome dataset, and cell-type labels.

Tool repositories:
1. Seurat: https://github.com/satijalab/seurat
2. Signac: https://github.com/stuart-lab/signac

Tool tutorials:
- Seurat PBMC marker tutorial: https://satijalab.org/seurat/articles/pbmc3k_tutorial
- Signac multiomic tutorial: https://stuartlab.org/signac/articles/pbmc_multiomic
- Signac mouse brain / scATAC tutorial: https://stuartlab.org/signac/articles/mouse_brain_vignette

Dataset:
- SHARE-seq mouse skin late-anagen RNA count matrix: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM4156608
- SHARE-seq mouse skin late-anagen ATAC peak count matrix and cell-type labels: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM4156597
- Use the RNA count matrix, ATAC peak count matrix, ATAC peaks BED file, barcodes file, and celltype file from the GEO supplementary files.

Reference database:
- CellMarker 2.0: http://www.bio-bigdata.center/CellMarker_download.html

Task:
Build a collaborative multi-agent workflow where Seurat is wrapped as an RNA marker-discovery agent and Signac is wrapped as an ATAC marker-discovery agent. Use the provided cell-type labels. For each cell type, identify the top N marker genes from RNA and ATAC, compute intersection and union marker sets, and evaluate these marker sets against CellMarker 2.0 mouse skin markers.

Requirements:
- Configure isolated Docker sandboxes for Seurat and Signac.
- Register each sandbox as a callable execution node with a typed input/output schema.
- Use Seurat on the scRNA-seq count matrix to compute rna_markers: top N genes per cell type.
- Use Signac on the scATAC-seq peak matrix to compute atac_markers: top N genes per cell type. The primary ATAC method should identify differential accessible peaks per cell type and map peaks to nearby genes. If the optional fragment file is available and runtime permits, also run a gene-activity sensitivity analysis.
- For every cell type, compute rna_markers ∩ atac_markers and rna_markers ∪ atac_markers.
- Download and process CellMarker 2.0 mouse marker annotations.
- Evaluate precision_rna, precision_atac, precision_intersection, recall_rna, recall_atac, and recall_union for each cell type and report macro and micro averages.
- Save all intermediate artifacts, scripts, logs, environment manifests, compiled workflow graph, execution trace, and final report.
```

### 4.2 More autonomous prompt

Use this only after the main run succeeds. It tests whether AgentCo-Op can infer more of the workflow from documentation.

```text
Given the Seurat and Signac GitHub repositories, their tutorials, the SHARE-seq skin RNA and ATAC GEO links, the cell-type label file, and CellMarker 2.0, configure sandboxed tool agents and complete a cross-modal marker-gene collaboration experiment. Return RNA markers, ATAC markers, intersection and union marker sets, CellMarker-based precision/recall metrics, figures, code, and a complete provenance trace.
```

---

## 5. Machine-readable request file

AgentCo-Op should support a request file like this.

```yaml
# case_study_2.request.yaml
case_id: shareseq_skin_seurat_signac_collaboration
mode: autonomous_tool_wrapping

repositories:
  - name: Seurat
    url: https://github.com/satijalab/seurat
    role_hint: scRNA_seq_marker_discovery
    docs:
      - https://satijalab.org/seurat/articles/pbmc3k_tutorial
      - https://satijalab.org/seurat/reference/findmarkers
  - name: Signac
    url: https://github.com/stuart-lab/signac
    role_hint: scATAC_seq_marker_discovery
    docs:
      - https://stuartlab.org/signac/articles/pbmc_multiomic
      - https://stuartlab.org/signac/articles/mouse_brain_vignette
      - https://stuartlab.org/signac/reference/geneactivity

dataset:
  name: shareseq_mouse_skin_late_anagen
  organism: Mus musculus
  genome: mm10
  tissue: skin
  geo_series: GSE140203
  rna_sample: GSM4156608
  atac_sample: GSM4156597
  files:
    rna_counts: https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSM4156608&file=GSM4156608_skin.late.anagen.rna.counts.txt.gz&format=file
    atac_counts: https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSM4156597&file=GSM4156597_skin.late.anagen.counts.txt.gz&format=file
    atac_peaks: https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSM4156597&file=GSM4156597_skin.late.anagen.peaks.bed.gz&format=file
    atac_barcodes: https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSM4156597&file=GSM4156597_skin.late.anagen.barcodes.txt.gz&format=file
    celltype_labels: https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSM4156597&file=GSM4156597_skin_celltype.txt.gz&format=file
    atac_fragments_optional: https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSM4156597&file=GSM4156597_skin.late.anagen.atac.fragments.bed.gz&format=file

cellmarker:
  download_page: http://www.bio-bigdata.center/CellMarker_download.html
  mirror: https://xteam.xbio.top/CellMarker/download.jsp
  species: Mouse
  tissue_filter_primary: Skin
  tissue_filter_extended: [Skin, Epidermis, Dermis, Hair follicle, Hair]

parameters:
  top_n: 50
  sensitivity_top_n: [20, 100]
  remove_cell_types: [Mixed]
  min_cells_per_type: 20
  rna_marker_method:
    tool: Seurat
    function: FindAllMarkers
    only_pos: true
    test_use: wilcox
    min_pct: 0.10
    logfc_threshold: 0.25
    rank_by: [p_val_adj, avg_log2FC]
  atac_marker_method:
    tool: Signac
    primary: differential_accessible_peaks_to_nearest_genes
    function: FindAllMarkers_or_FindMarkers_per_cell_type
    only_pos: true
    test_use: LR
    min_pct: 0.05
    latent_vars: nCount_peaks
    peak_to_gene: closest_feature
    rank_by: [p_val_adj, avg_log2FC, n_supporting_peaks]
  optional_atac_gene_activity:
    enabled_if_fragments_available: true
    function: GeneActivity
    extend_upstream: 2000
    top_n: 50

required_outputs:
  - compiled_workflow_graph.json
  - agent_registry.json
  - docker_build_report.json
  - data_inventory.json
  - barcode_alignment_report.json
  - celltype_distribution.csv
  - seurat_rna_markers_all.csv
  - rna_top_markers_by_celltype.json
  - signac_atac_peak_markers_all.csv
  - atac_top_marker_genes_by_celltype.json
  - marker_set_operations_by_celltype.json
  - marker_set_sizes_by_celltype.csv
  - cellmarker_gold_markers_by_celltype.json
  - cellmarker_label_mapping.csv
  - precision_recall_by_celltype.csv
  - precision_recall_summary.csv
  - marker_overlap_heatmap.png
  - precision_recall_barplot.png
  - final_report.md
  - reproducibility_notebook.ipynb
  - execution_trace.jsonl
  - run_manifest.json

success_criteria:
  docker_sandboxes_built: true
  seurat_node_called: true
  signac_node_called: true
  top_marker_sets_created_for_minimum_cell_types: 10
  cellmarker_evaluation_completed: true
  final_report_contains_collaboration_trace: true
  max_replans: 2
```

---

## 6. Expected compiled workflow topology

AgentCo-Op should compile a workflow like this:

```text
Task Profiler
  -> Repo Profiler
  -> Sandbox Builder
  -> Data Downloader / Cache Manager
  -> Dataset Schema Inspector
  -> Barcode and Cell-Type Alignment Agent
  -> Seurat RNA Marker Agent
  -> Signac ATAC Marker Agent
  -> Gene Symbol Harmonizer
  -> Set Operation Agent
  -> CellMarker Database Agent
  -> Marker Evaluation Agent
  -> Integrator / Reviewer
  -> Reporter
```

### 6.1 Why this topology is appropriate

This task has two independent modality-specific branches followed by deterministic integration. Therefore, the compiled graph should use **parallel specialist nodes** for RNA and ATAC marker discovery, then a **join node** for set operations and evaluation.

```text
                       -> Seurat RNA Marker Agent  -> rna_markers  ->
Dataset + labels ------>                                             SetOps -> CellMarkerEval -> Report
                       -> Signac ATAC Marker Agent -> atac_markers ->
```

The workflow should remain simple. It does not need debate agents or recursive planning. Dynamic refinement should only activate if a concrete execution signal indicates failure, such as missing files, failed package installation, barcode mismatch, empty marker sets, or missing CellMarker labels.

---

## 7. Agent registry design

### 7.1 Seurat RNA Marker Agent

```json
{
  "agent_id": "seurat_rna_marker_agent",
  "role": "scRNA-seq marker discovery specialist",
  "backend_type": "dockerized_r_tool",
  "source_repo": "https://github.com/satijalab/seurat",
  "capabilities": [
    "load gene-by-cell count matrix",
    "create Seurat object",
    "attach cell-type metadata",
    "normalize scRNA-seq counts",
    "run FindAllMarkers by cell type",
    "export marker tables and top-N gene sets"
  ],
  "input_schema": {
    "rna_counts_path": "string",
    "celltype_labels_path": "string",
    "rna_barcode_column": "string|null",
    "cell_type_column": "string|null",
    "top_n": "integer",
    "min_cells_per_type": "integer",
    "remove_cell_types": "array<string>",
    "output_dir": "string"
  },
  "output_schema": {
    "status": "success|failed",
    "rna_markers_all_csv": "string",
    "rna_top_markers_json": "string",
    "celltype_distribution_csv": "string",
    "run_log": "string",
    "warnings": "array<string>"
  }
}
```

### 7.2 Signac ATAC Marker Agent

```json
{
  "agent_id": "signac_atac_marker_agent",
  "role": "scATAC-seq marker-gene discovery specialist",
  "backend_type": "dockerized_r_tool",
  "source_repo": "https://github.com/stuart-lab/signac",
  "capabilities": [
    "load peak-by-cell ATAC count matrix",
    "load peak BED file",
    "create ChromatinAssay",
    "attach cell-type metadata",
    "run differential accessibility by cell type",
    "map marker peaks to nearest genes",
    "optionally compute gene-activity marker genes from fragments"
  ],
  "input_schema": {
    "atac_counts_path": "string",
    "peaks_bed_path": "string",
    "atac_barcodes_path": "string|null",
    "celltype_labels_path": "string",
    "fragments_path_optional": "string|null",
    "genome": "mm10",
    "top_n": "integer",
    "min_cells_per_type": "integer",
    "remove_cell_types": "array<string>",
    "output_dir": "string"
  },
  "output_schema": {
    "status": "success|failed",
    "atac_peak_markers_all_csv": "string",
    "atac_marker_genes_json": "string",
    "peak_to_gene_mapping_csv": "string",
    "optional_gene_activity_markers_csv": "string|null",
    "run_log": "string",
    "warnings": "array<string>"
  }
}
```

### 7.3 CellMarker Evaluation Agent

```json
{
  "agent_id": "cellmarker_evaluation_agent",
  "role": "reference marker database evaluation specialist",
  "backend_type": "python_or_r_deterministic_tool",
  "capabilities": [
    "download and parse CellMarker 2.0",
    "filter for mouse skin markers",
    "map dataset cell-type labels to CellMarker cell-type names",
    "compute precision and recall for RNA, ATAC, intersection, and union marker sets",
    "export summary tables and figures"
  ],
  "input_schema": {
    "rna_top_markers_json": "string",
    "atac_marker_genes_json": "string",
    "cellmarker_file_or_download_page": "string",
    "organism": "Mouse",
    "tissue_filter": "string|array<string>",
    "label_alias_file_optional": "string|null",
    "output_dir": "string"
  },
  "output_schema": {
    "status": "success|failed",
    "precision_recall_by_celltype_csv": "string",
    "precision_recall_summary_csv": "string",
    "cellmarker_gold_markers_json": "string",
    "label_mapping_csv": "string",
    "figures": "array<string>",
    "warnings": "array<string>"
  }
}
```

---

## 8. Docker and sandbox design

### 8.1 Directory layout

Use a run-specific directory so all outputs are auditable.

```text
runs/case_study_2_shareseq_skin/
  request.yaml
  repos/
    seurat/
    signac/
  docker/
    Dockerfile.seurat
    Dockerfile.signac
    docker-compose.yml
  wrappers/
    seurat_rna_marker_agent.R
    signac_atac_marker_agent.R
    evaluate_marker_sets.py
    common_io.py
  data/
    raw/
    processed/
    cellmarker/
  artifacts/
    00_data_inventory/
    01_barcode_alignment/
    02_seurat_rna_markers/
    03_signac_atac_markers/
    04_set_operations/
    05_cellmarker_evaluation/
    06_figures/
    07_report/
  logs/
    docker_build/
    execution/
  trace/
    execution_trace.jsonl
    compiled_workflow_graph.json
    agent_registry.json
    run_manifest.json
```

### 8.2 Seurat Dockerfile

AgentCo-Op can synthesize a Dockerfile like this. Pin versions in the final experiment after the smoke test passes.

```dockerfile
# docker/Dockerfile.seurat
FROM rocker/r-ver:4.3.3

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    git wget curl ca-certificates build-essential \
    libcurl4-openssl-dev libssl-dev libxml2-dev \
    libhdf5-dev libz-dev libbz2-dev liblzma-dev \
    libpng-dev libjpeg-dev libfreetype6-dev \
    && rm -rf /var/lib/apt/lists/*

RUN R -e "install.packages(c('remotes','data.table','Matrix','jsonlite','dplyr','ggplot2'), repos='https://cloud.r-project.org')"
RUN R -e "remotes::install_github('satijalab/seurat')"

WORKDIR /workspace
COPY wrappers/seurat_rna_marker_agent.R /workspace/seurat_rna_marker_agent.R
ENTRYPOINT ["Rscript", "/workspace/seurat_rna_marker_agent.R"]
```

### 8.3 Signac Dockerfile

Use a Signac-specific image if AgentCo-Op discovers it, or build from R. The Signac documentation provides Docker images, so this is a practical default.

```dockerfile
# docker/Dockerfile.signac
FROM timoast/signac:latest

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    git wget curl ca-certificates build-essential \
    libcurl4-openssl-dev libssl-dev libxml2-dev \
    libhdf5-dev libz-dev libbz2-dev liblzma-dev \
    libpng-dev libjpeg-dev libfreetype6-dev \
    && rm -rf /var/lib/apt/lists/*

RUN R -e "install.packages(c('data.table','Matrix','jsonlite','dplyr','ggplot2','GenomicRanges'), repos='https://cloud.r-project.org')"
RUN R -e "if (!requireNamespace('BiocManager', quietly=TRUE)) install.packages('BiocManager', repos='https://cloud.r-project.org'); BiocManager::install(c('EnsDb.Mmusculus.v79','GenomeInfoDb','org.Mm.eg.db'), ask=FALSE, update=FALSE)"

WORKDIR /workspace
COPY wrappers/signac_atac_marker_agent.R /workspace/signac_atac_marker_agent.R
ENTRYPOINT ["Rscript", "/workspace/signac_atac_marker_agent.R"]
```

### 8.4 Docker Compose

```yaml
# docker/docker-compose.yml
services:
  seurat_agent:
    build:
      context: ..
      dockerfile: docker/Dockerfile.seurat
    volumes:
      - ../data:/data:ro
      - ../artifacts:/artifacts:rw
      - ../logs:/logs:rw
    environment:
      - OMP_NUM_THREADS=8
      - OPENBLAS_NUM_THREADS=8

  signac_agent:
    build:
      context: ..
      dockerfile: docker/Dockerfile.signac
    volumes:
      - ../data:/data:ro
      - ../artifacts:/artifacts:rw
      - ../logs:/logs:rw
    environment:
      - OMP_NUM_THREADS=8
      - OPENBLAS_NUM_THREADS=8
```

### 8.5 Sandbox rules

The two tool sandboxes should be isolated from the host except for explicit mounts:

```text
/data      read-only input data
/artifacts read-write output artifacts
/logs      read-write execution logs
```

Network should be enabled during image build and dataset download. During analysis execution, network can be disabled except when downloading CellMarker or package metadata is explicitly required. The final report should record the network policy.

---

## 9. Data download and schema inspection

### 9.1 Data download node

The Data Downloader should create:

```text
data/raw/GSM4156608_skin.late.anagen.rna.counts.txt.gz
data/raw/GSM4156597_skin.late.anagen.counts.txt.gz
data/raw/GSM4156597_skin.late.anagen.peaks.bed.gz
data/raw/GSM4156597_skin.late.anagen.barcodes.txt.gz
data/raw/GSM4156597_skin_celltype.txt.gz
```

Optional:

```text
data/raw/GSM4156597_skin.late.anagen.atac.fragments.bed.gz
```

A robust download command:

```bash
mkdir -p data/raw

wget -O data/raw/GSM4156608_skin.late.anagen.rna.counts.txt.gz \
  'https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSM4156608&file=GSM4156608_skin.late.anagen.rna.counts.txt.gz&format=file'

wget -O data/raw/GSM4156597_skin.late.anagen.counts.txt.gz \
  'https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSM4156597&file=GSM4156597_skin.late.anagen.counts.txt.gz&format=file'

wget -O data/raw/GSM4156597_skin.late.anagen.peaks.bed.gz \
  'https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSM4156597&file=GSM4156597_skin.late.anagen.peaks.bed.gz&format=file'

wget -O data/raw/GSM4156597_skin.late.anagen.barcodes.txt.gz \
  'https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSM4156597&file=GSM4156597_skin.late.anagen.barcodes.txt.gz&format=file'

wget -O data/raw/GSM4156597_skin_celltype.txt.gz \
  'https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSM4156597&file=GSM4156597_skin_celltype.txt.gz&format=file'
```

Record file sizes and SHA256 hashes:

```bash
sha256sum data/raw/* > artifacts/00_data_inventory/sha256sum.txt
ls -lh data/raw > artifacts/00_data_inventory/file_sizes.txt
```

### 9.2 Schema inspection node

The schema inspector should read only the first few lines at first. Do not load all matrices until the delimiter and dimensions are known.

```bash
zcat data/raw/GSM4156608_skin.late.anagen.rna.counts.txt.gz | head -n 3
zcat data/raw/GSM4156597_skin.late.anagen.counts.txt.gz | head -n 3
zcat data/raw/GSM4156597_skin.late.anagen.peaks.bed.gz | head -n 3
zcat data/raw/GSM4156597_skin_celltype.txt.gz | head -n 5
```

The schema inspector should emit:

```json
{
  "rna_counts": {
    "path": "data/raw/GSM4156608_skin.late.anagen.rna.counts.txt.gz",
    "orientation": "features_by_cells",
    "feature_type": "gene",
    "delimiter": "tab",
    "n_features": null,
    "n_cells": null
  },
  "atac_counts": {
    "path": "data/raw/GSM4156597_skin.late.anagen.counts.txt.gz",
    "orientation": "features_by_cells",
    "feature_type": "peak",
    "delimiter": "tab",
    "n_features": null,
    "n_cells": null
  },
  "celltype_labels": {
    "path": "data/raw/GSM4156597_skin_celltype.txt.gz",
    "candidate_rna_barcode_columns": [],
    "candidate_atac_barcode_columns": [],
    "candidate_celltype_columns": []
  }
}
```

### 9.3 Barcode alignment node

The barcode alignment node should infer column mappings by overlap.

Pseudo-code:

```python
rna_cells = set(rna_count_matrix_columns)
atac_cells = set(atac_count_matrix_columns)
labels = read_table(celltype_file)

for col in labels.columns:
    overlap_with_rna = len(set(labels[col]) & rna_cells)
    overlap_with_atac = len(set(labels[col]) & atac_cells)
    record_overlap(col, overlap_with_rna, overlap_with_atac)

rna_barcode_column = column_with_max_overlap_with_rna
atac_barcode_column = column_with_max_overlap_with_atac
cell_type_column = infer_column_by_name_or_cardinality(labels)
```

Validation thresholds:

```text
barcode_overlap_rna  >= 0.90, otherwise trigger repair
barcode_overlap_atac >= 0.90, otherwise try alternate barcode columns or cleaned barcode strings
```

Barcode cleaning rules:

```text
- trim whitespace
- remove trailing quotation marks
- preserve full barcode string by default
- only remove suffixes such as '-1' if doing so increases overlap and is logged
```

The output should be:

```text
artifacts/01_barcode_alignment/barcode_alignment_report.json
artifacts/01_barcode_alignment/celltype_distribution.csv
artifacts/01_barcode_alignment/aligned_cell_metadata.csv
```

---

## 10. Seurat RNA marker-discovery workflow

### 10.1 Main analysis choices

The Seurat node should use cell-type labels provided by the dataset, not unsupervised clusters inferred by Seurat. This keeps the RNA and ATAC branches aligned to the same biological labels.

Recommended settings:

```yaml
normalization: LogNormalize
scale_factor: 10000
marker_function: FindAllMarkers
only_pos: true
test_use: wilcox
min_pct: 0.10
logfc_threshold: 0.25
rank_by:
  - p_val_adj ascending
  - avg_log2FC descending
top_n: 50
```

### 10.2 R wrapper skeleton

```r
# wrappers/seurat_rna_marker_agent.R
suppressPackageStartupMessages({
  library(Seurat)
  library(data.table)
  library(Matrix)
  library(jsonlite)
  library(dplyr)
})

args <- commandArgs(trailingOnly = TRUE)
config_path <- args[1]
config <- jsonlite::fromJSON(config_path)

outdir <- config$output_dir
dir.create(outdir, recursive = TRUE, showWarnings = FALSE)

read_count_matrix <- function(path) {
  dt <- data.table::fread(path)
  feature_col <- names(dt)[1]
  features <- dt[[feature_col]]
  mat_df <- as.data.frame(dt[, -1])
  mat <- as(as.matrix(mat_df), "dgCMatrix")
  rownames(mat) <- make.unique(as.character(features))
  colnames(mat) <- names(dt)[-1]
  mat
}

counts <- read_count_matrix(config$rna_counts_path)
labels <- data.table::fread(config$celltype_labels_path)

rna_col <- config$rna_barcode_column
celltype_col <- config$cell_type_column

metadata <- as.data.frame(labels)
rownames(metadata) <- metadata[[rna_col]]

common_cells <- intersect(colnames(counts), rownames(metadata))
counts <- counts[, common_cells]
metadata <- metadata[common_cells, , drop = FALSE]
metadata$cell_type <- metadata[[celltype_col]]

if (!is.null(config$remove_cell_types)) {
  keep <- !(metadata$cell_type %in% config$remove_cell_types)
  counts <- counts[, keep]
  metadata <- metadata[keep, , drop = FALSE]
}

cell_counts <- table(metadata$cell_type)
valid_types <- names(cell_counts[cell_counts >= config$min_cells_per_type])
keep <- metadata$cell_type %in% valid_types
counts <- counts[, keep]
metadata <- metadata[keep, , drop = FALSE]

obj <- CreateSeuratObject(counts = counts, meta.data = metadata)
Idents(obj) <- obj$cell_type

obj <- NormalizeData(obj, normalization.method = "LogNormalize", scale.factor = 10000)
obj <- FindVariableFeatures(obj)

markers <- FindAllMarkers(
  obj,
  only.pos = TRUE,
  test.use = config$test_use,
  min.pct = config$min_pct,
  logfc.threshold = config$logfc_threshold
)

# Seurat v4/v5 may name the fold-change column differently.
fc_col <- intersect(c("avg_log2FC", "avg_logFC"), colnames(markers))[1]
if (is.na(fc_col)) stop("No avg_log2FC/avg_logFC column found in Seurat marker table.")

markers <- markers %>%
  arrange(cluster, p_val_adj, desc(.data[[fc_col]]))

fwrite(markers, file.path(outdir, "seurat_rna_markers_all.csv"))

make_top <- function(df, n) {
  df %>%
    group_by(cluster) %>%
    arrange(p_val_adj, desc(.data[[fc_col]]), .by_group = TRUE) %>%
    slice_head(n = n) %>%
    summarise(genes = list(unique(gene)), .groups = "drop")
}

top <- make_top(markers, config$top_n)
jsonlite::write_json(
  setNames(top$genes, top$cluster),
  file.path(outdir, "rna_top_markers_by_celltype.json"),
  pretty = TRUE,
  auto_unbox = TRUE
)

fwrite(
  data.frame(cell_type = names(table(obj$cell_type)), n_cells = as.integer(table(obj$cell_type))),
  file.path(outdir, "celltype_distribution.csv")
)

jsonlite::write_json(
  list(
    status = "success",
    n_cells = ncol(counts),
    n_genes = nrow(counts),
    n_cell_types = length(unique(metadata$cell_type)),
    marker_table = file.path(outdir, "seurat_rna_markers_all.csv"),
    top_markers = file.path(outdir, "rna_top_markers_by_celltype.json")
  ),
  file.path(outdir, "result.json"),
  pretty = TRUE,
  auto_unbox = TRUE
)
```

### 10.3 Seurat node output

Expected files:

```text
artifacts/02_seurat_rna_markers/seurat_rna_markers_all.csv
artifacts/02_seurat_rna_markers/rna_top_markers_by_celltype.json
artifacts/02_seurat_rna_markers/celltype_distribution.csv
artifacts/02_seurat_rna_markers/result.json
artifacts/02_seurat_rna_markers/run.log
```

---

## 11. Signac ATAC marker-gene workflow

### 11.1 Primary ATAC marker-gene method

Use **differentially accessible peaks mapped to nearby genes** as the primary method.

Rationale:

- the user-provided primary ATAC input is a peak count matrix;
- the peak count matrix and peaks BED file are enough to identify cell-type-specific accessible peaks;
- the optional fragment file is large and should not be required for the main experiment;
- marker-gene comparison against CellMarker requires gene-level output, so peak markers must be mapped to genes.

Recommended settings:

```yaml
normalization: TF-IDF for ATAC object diagnostics
marker_function: FindMarkers per cell type or FindAllMarkers if stable
test_use: LR
latent_vars: nCount_peaks
only_pos: true
min_pct: 0.05
peak_to_gene: ClosestFeature / nearest gene using mm10 annotation
top_n: 50
```

For each cell type, compare that cell type against all other cell types. For each differentially accessible peak, map the peak to the nearest gene using mm10 annotation. Aggregate peak-level evidence into gene-level ATAC marker scores.

Recommended gene-level aggregation:

```text
For each cell type and gene:
  best_p_val_adj      = minimum adjusted P-value among peaks mapped to the gene
  best_log2fc         = maximum avg_log2FC among peaks mapped to the gene
  n_supporting_peaks  = number of significant marker peaks mapped to the gene

Rank genes by:
  1. best_p_val_adj ascending
  2. best_log2fc descending
  3. n_supporting_peaks descending
```

### 11.2 Optional ATAC gene-activity sensitivity analysis

If the fragment file is available and compute budget permits, run a second ATAC marker-gene method:

```text
fragments + mm10 gene annotation -> Signac GeneActivity -> gene activity assay -> FindAllMarkers -> top N marker genes
```

Use this only as a sensitivity analysis because the fragment file is large. It should not be required for the main systems demonstration.

### 11.3 R wrapper skeleton

```r
# wrappers/signac_atac_marker_agent.R
suppressPackageStartupMessages({
  library(Signac)
  library(Seurat)
  library(data.table)
  library(Matrix)
  library(jsonlite)
  library(dplyr)
  library(GenomicRanges)
  library(EnsDb.Mmusculus.v79)
  library(GenomeInfoDb)
})

args <- commandArgs(trailingOnly = TRUE)
config_path <- args[1]
config <- jsonlite::fromJSON(config_path)

outdir <- config$output_dir
dir.create(outdir, recursive = TRUE, showWarnings = FALSE)

read_count_matrix <- function(path) {
  dt <- data.table::fread(path)
  feature_col <- names(dt)[1]
  features <- dt[[feature_col]]
  mat_df <- as.data.frame(dt[, -1])
  mat <- as(as.matrix(mat_df), "dgCMatrix")
  rownames(mat) <- as.character(features)
  colnames(mat) <- names(dt)[-1]
  mat
}

read_peaks <- function(path, peak_names) {
  peaks <- data.table::fread(path, header = FALSE)
  colnames(peaks)[1:3] <- c("chr", "start", "end")
  gr <- GRanges(seqnames = peaks$chr, ranges = IRanges(start = peaks$start + 1, end = peaks$end))
  # Use matrix row names if they exist and have matching length; otherwise construct names from BED.
  if (length(peak_names) == length(gr)) {
    names(gr) <- peak_names
  } else {
    names(gr) <- paste0(peaks$chr, ":", peaks$start + 1, "-", peaks$end)
  }
  gr
}

counts <- read_count_matrix(config$atac_counts_path)
peaks_gr <- read_peaks(config$peaks_bed_path, rownames(counts))

labels <- data.table::fread(config$celltype_labels_path)
barcode_col <- config$atac_or_rna_barcode_column_for_atac_matrix
celltype_col <- config$cell_type_column

metadata <- as.data.frame(labels)
rownames(metadata) <- metadata[[barcode_col]]

common_cells <- intersect(colnames(counts), rownames(metadata))
counts <- counts[, common_cells]
metadata <- metadata[common_cells, , drop = FALSE]
metadata$cell_type <- metadata[[celltype_col]]

if (!is.null(config$remove_cell_types)) {
  keep <- !(metadata$cell_type %in% config$remove_cell_types)
  counts <- counts[, keep]
  metadata <- metadata[keep, , drop = FALSE]
}

cell_counts <- table(metadata$cell_type)
valid_types <- names(cell_counts[cell_counts >= config$min_cells_per_type])
keep <- metadata$cell_type %in% valid_types
counts <- counts[, keep]
metadata <- metadata[keep, , drop = FALSE]

# Prepare gene annotation.
annotation <- GetGRangesFromEnsDb(ensdb = EnsDb.Mmusculus.v79)
seqlevelsStyle(annotation) <- "UCSC"
genome(annotation) <- "mm10"

chrom_assay <- CreateChromatinAssay(
  counts = counts,
  ranges = peaks_gr,
  genome = "mm10",
  annotation = annotation
)

obj <- CreateSeuratObject(counts = chrom_assay, assay = "peaks", meta.data = metadata)
Idents(obj) <- obj$cell_type

# Basic ATAC normalization / diagnostics.
obj <- RunTFIDF(obj)
obj <- FindTopFeatures(obj, min.cutoff = "q0")
obj <- RunSVD(obj)

cell_types <- sort(unique(obj$cell_type))
all_peak_markers <- list()
all_gene_markers <- list()

for (ct in cell_types) {
  message("Running differential accessibility for cell type: ", ct)
  da <- FindMarkers(
    object = obj,
    ident.1 = ct,
    ident.2 = setdiff(cell_types, ct),
    only.pos = TRUE,
    test.use = config$test_use,
    min.pct = config$min_pct,
    latent.vars = config$latent_vars
  )
  da$peak <- rownames(da)
  da$cluster <- ct
  all_peak_markers[[ct]] <- da
}

peak_markers <- dplyr::bind_rows(all_peak_markers)
data.table::fwrite(peak_markers, file.path(outdir, "signac_atac_peak_markers_all.csv"))

# Map peaks to closest annotated genes.
# If ClosestFeature fails due to name formatting, fall back to distanceToNearest.
closest_list <- list()
for (ct in cell_types) {
  ct_peaks <- all_peak_markers[[ct]]
  if (nrow(ct_peaks) == 0) next
  peak_names <- ct_peaks$peak
  closest <- tryCatch({
    cf <- ClosestFeature(obj, regions = peak_names)
    cf$peak <- peak_names
    cf
  }, error = function(e) {
    message("ClosestFeature failed; using distanceToNearest fallback: ", e$message)
    peak_gr <- granges(obj)[peak_names]
    hits <- distanceToNearest(peak_gr, annotation)
    data.frame(
      peak = names(peak_gr)[queryHits(hits)],
      gene_name = annotation$gene_name[subjectHits(hits)],
      distance = mcols(hits)$distance
    )
  })
  closest$cluster <- ct
  closest_list[[ct]] <- closest
}

peak_to_gene <- dplyr::bind_rows(closest_list)
data.table::fwrite(peak_to_gene, file.path(outdir, "peak_to_gene_mapping.csv"))

merged <- peak_markers %>%
  left_join(peak_to_gene, by = c("cluster", "peak"))

# Normalize possible gene column names.
gene_col <- intersect(c("gene_name", "gene", "symbol", "external_gene_name"), colnames(merged))[1]
if (is.na(gene_col)) stop("Could not find gene name column after peak-to-gene mapping.")

fc_col <- intersect(c("avg_log2FC", "avg_logFC"), colnames(merged))[1]
if (is.na(fc_col)) stop("No avg_log2FC/avg_logFC column found in Signac marker table.")

merged <- merged %>% filter(!is.na(.data[[gene_col]]), .data[[gene_col]] != "")

gene_scores <- merged %>%
  group_by(cluster, gene = .data[[gene_col]]) %>%
  summarise(
    best_p_val_adj = suppressWarnings(min(p_val_adj, na.rm = TRUE)),
    best_log2fc = suppressWarnings(max(.data[[fc_col]], na.rm = TRUE)),
    n_supporting_peaks = n(),
    example_peaks = paste(head(unique(peak), 5), collapse = ";"),
    .groups = "drop"
  ) %>%
  arrange(cluster, best_p_val_adj, desc(best_log2fc), desc(n_supporting_peaks))

data.table::fwrite(gene_scores, file.path(outdir, "signac_atac_marker_genes_all.csv"))

top <- gene_scores %>%
  group_by(cluster) %>%
  slice_head(n = config$top_n) %>%
  summarise(genes = list(unique(gene)), .groups = "drop")

jsonlite::write_json(
  setNames(top$genes, top$cluster),
  file.path(outdir, "atac_top_marker_genes_by_celltype.json"),
  pretty = TRUE,
  auto_unbox = TRUE
)

jsonlite::write_json(
  list(
    status = "success",
    n_cells = ncol(counts),
    n_peaks = nrow(counts),
    n_cell_types = length(unique(metadata$cell_type)),
    peak_marker_table = file.path(outdir, "signac_atac_peak_markers_all.csv"),
    gene_marker_table = file.path(outdir, "signac_atac_marker_genes_all.csv"),
    top_marker_genes = file.path(outdir, "atac_top_marker_genes_by_celltype.json")
  ),
  file.path(outdir, "result.json"),
  pretty = TRUE,
  auto_unbox = TRUE
)
```

### 11.4 Signac node output

Expected files:

```text
artifacts/03_signac_atac_markers/signac_atac_peak_markers_all.csv
artifacts/03_signac_atac_markers/peak_to_gene_mapping.csv
artifacts/03_signac_atac_markers/signac_atac_marker_genes_all.csv
artifacts/03_signac_atac_markers/atac_top_marker_genes_by_celltype.json
artifacts/03_signac_atac_markers/result.json
artifacts/03_signac_atac_markers/run.log
```

Optional gene-activity output:

```text
artifacts/03_signac_atac_markers/signac_gene_activity_markers_all.csv
artifacts/03_signac_atac_markers/atac_gene_activity_top_markers_by_celltype.json
```

---

## 12. Gene-symbol harmonization

Before set operations and CellMarker evaluation, AgentCo-Op should harmonize gene symbols.

Rules:

1. Keep the original symbols for reporting.
2. Create a normalized comparison key:

```text
symbol_key = upper(trim(symbol))
```

3. Remove empty symbols and obvious non-gene placeholders.
4. Collapse duplicate symbols within each cell type.
5. If available, use `org.Mm.eg.db` or an MGI symbol table to map aliases to official mouse gene symbols.
6. Report how many genes were altered by alias mapping.

Output:

```text
artifacts/04_set_operations/gene_symbol_harmonization_report.json
```

---

## 13. Set operation node

For each cell type `c`:

```python
R_c = set(rna_markers[c])
A_c = set(atac_markers[c])
I_c = R_c & A_c
U_c = R_c | A_c
```

Save both full JSON and table summaries.

```json
{
  "Basal": {
    "rna": ["Krt14", "Krt5", "..."],
    "atac": ["Krt14", "Trp63", "..."],
    "intersection": ["Krt14", "..."],
    "union": ["Krt14", "Krt5", "Trp63", "..."]
  }
}
```

Summary columns:

```text
cell_type
n_rna
n_atac
n_intersection
n_union
jaccard_rna_atac
```

Jaccard similarity:

```text
jaccard_rna_atac = |R_c ∩ A_c| / |R_c ∪ A_c|
```

Expected files:

```text
artifacts/04_set_operations/marker_set_operations_by_celltype.json
artifacts/04_set_operations/marker_set_sizes_by_celltype.csv
artifacts/04_set_operations/marker_overlap_heatmap.png
```

---

## 14. CellMarker 2.0 evaluation

### 14.1 Database processing

The CellMarker Database Agent should:

1. download the mouse marker table;
2. parse columns robustly;
3. filter for mouse entries;
4. filter for skin-related tissues;
5. map dataset cell-type labels to CellMarker cell-type labels;
6. create gold marker sets for each mapped cell type.

Recommended tissue filters:

```text
primary:  tissue == Skin
extended: tissue in {Skin, Epidermis, Dermis, Hair follicle, Hair}
```

Use both if possible:

- **primary CellMarker evaluation**: strict `Skin` filter;
- **sensitivity evaluation**: extended skin/hair/epidermis/dermis filter.

### 14.2 Cell-type label mapping

The SHARE-seq skin labels may be more specific than CellMarker labels. AgentCo-Op should create a mapping table rather than forcing exact string matches.

Output table:

```text
cell_type_dataset
cellmarker_cell_type
mapping_rule
confidence
n_cellmarker_markers
include_in_macro_eval
notes
```

Mapping rules:

```text
exact_string_match
case_insensitive_match
manual_alias
llm_suggested_and_verified
unmapped
```

The final report should clearly state which cell types were included in the database-based evaluation. If a dataset cell type has no CellMarker entry, report it as `unmapped` and exclude it from matched-only macro averages. Also provide a strict metric variant that treats unmapped cell types as zero-recall if desired.

Suggested alias examples to check, not to hard-code without validation:

```text
Basal              -> basal cell / epidermal basal cell / keratinocyte
Bulge              -> hair follicle stem cell / bulge cell
Outer root sheath  -> outer root sheath cell / keratinocyte
Inner root sheath  -> inner root sheath cell
Dermal fibroblast  -> fibroblast
Endothelial        -> endothelial cell
Macrophage         -> macrophage
Melanocyte         -> melanocyte
Sebocyte           -> sebocyte
```

### 14.3 Precision and recall definitions

For each mapped cell type `c`, let:

```text
G_c = CellMarker gold marker set for cell type c
R_c = top-N RNA marker genes from Seurat
A_c = top-N ATAC marker genes from Signac
I_c = R_c ∩ A_c
U_c = R_c ∪ A_c
```

Compute:

```text
precision_rna          = |R_c ∩ G_c| / |R_c|
precision_atac         = |A_c ∩ G_c| / |A_c|
precision_intersection = |I_c ∩ G_c| / |I_c|, if |I_c| > 0 else NA

recall_rna             = |R_c ∩ G_c| / |G_c|
recall_atac            = |A_c ∩ G_c| / |G_c|
recall_union           = |U_c ∩ G_c| / |G_c|
```

Optional F1 scores:

```text
f1_rna          = 2 * precision_rna * recall_rna / (precision_rna + recall_rna)
f1_atac         = 2 * precision_atac * recall_atac / (precision_atac + recall_atac)
f1_intersection = 2 * precision_intersection * recall_intersection / (precision_intersection + recall_intersection)
f1_union        = 2 * precision_union * recall_union / (precision_union + recall_union)
```

The user requested only precision for RNA, ATAC, intersection and recall for RNA, ATAC, union. Still, saving all related metrics is useful for analysis.

### 14.4 Aggregation

Report:

```text
per-cell-type metrics
macro average across mapped cell types
micro average pooling marker hits across mapped cell types
cell-count-weighted average, optional
```

Primary summary table:

```text
metric
mean
median
std
n_cell_types
cellmarker_filter_mode
```

Expected output files:

```text
artifacts/05_cellmarker_evaluation/cellmarker_raw_mouse_markers.tsv
artifacts/05_cellmarker_evaluation/cellmarker_gold_markers_by_celltype.json
artifacts/05_cellmarker_evaluation/cellmarker_label_mapping.csv
artifacts/05_cellmarker_evaluation/precision_recall_by_celltype.csv
artifacts/05_cellmarker_evaluation/precision_recall_summary.csv
artifacts/05_cellmarker_evaluation/precision_recall_barplot.png
artifacts/05_cellmarker_evaluation/collaboration_gain_table.csv
```

### 14.5 Collaboration-gain metrics

Add explicit metrics to support the systems claim.

```text
intersection_precision_gain_over_rna  = precision_intersection - precision_rna
intersection_precision_gain_over_atac = precision_intersection - precision_atac
union_recall_gain_over_rna            = recall_union - recall_rna
union_recall_gain_over_atac           = recall_union - recall_atac
```

Report the fraction of cell types where:

```text
precision_intersection > max(precision_rna, precision_atac)
recall_union > max(recall_rna, recall_atac)
```

Do not claim success only from aggregate improvements. Also inspect cell-type-specific failures.

---

## 15. Evaluation script skeleton

```python
# wrappers/evaluate_marker_sets.py
import json
import re
from pathlib import Path
import pandas as pd


def norm_symbol(x: str) -> str:
    if pd.isna(x):
        return ""
    return re.sub(r"\s+", "", str(x).strip()).upper()


def load_marker_json(path):
    with open(path) as f:
        raw = json.load(f)
    return {ct: {norm_symbol(g) for g in genes if norm_symbol(g)} for ct, genes in raw.items()}


def precision(pred, gold):
    return len(pred & gold) / len(pred) if len(pred) else None


def recall(pred, gold):
    return len(pred & gold) / len(gold) if len(gold) else None


def main(config):
    outdir = Path(config["output_dir"])
    outdir.mkdir(parents=True, exist_ok=True)

    rna = load_marker_json(config["rna_top_markers_json"])
    atac = load_marker_json(config["atac_top_marker_genes_json"])

    # gold_markers_by_celltype should already be processed by the CellMarker parser.
    with open(config["cellmarker_gold_markers_json"]) as f:
        gold_raw = json.load(f)
    gold = {ct: {norm_symbol(g) for g in genes if norm_symbol(g)} for ct, genes in gold_raw.items()}

    rows = []
    set_ops = {}
    for ct in sorted(set(rna) | set(atac)):
        R = rna.get(ct, set())
        A = atac.get(ct, set())
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
            "jaccard_rna_atac": len(I) / len(U) if U else None,
        }
        if ct not in gold:
            rows.append({
                "cell_type": ct,
                "mapped_to_cellmarker": False,
                "n_gold": 0,
                "n_rna": len(R),
                "n_atac": len(A),
                "n_intersection": len(I),
                "n_union": len(U),
            })
            continue
        G = gold[ct]
        rows.append({
            "cell_type": ct,
            "mapped_to_cellmarker": True,
            "n_gold": len(G),
            "n_rna": len(R),
            "n_atac": len(A),
            "n_intersection": len(I),
            "n_union": len(U),
            "precision_rna": precision(R, G),
            "precision_atac": precision(A, G),
            "precision_intersection": precision(I, G),
            "recall_rna": recall(R, G),
            "recall_atac": recall(A, G),
            "recall_union": recall(U, G),
            "hits_rna": len(R & G),
            "hits_atac": len(A & G),
            "hits_intersection": len(I & G),
            "hits_union": len(U & G),
        })

    with open(outdir / "marker_set_operations_by_celltype.json", "w") as f:
        json.dump(set_ops, f, indent=2)

    df = pd.DataFrame(rows)
    df.to_csv(outdir / "precision_recall_by_celltype.csv", index=False)

    metric_cols = [
        "precision_rna", "precision_atac", "precision_intersection",
        "recall_rna", "recall_atac", "recall_union"
    ]
    mapped = df[df["mapped_to_cellmarker"] == True].copy()
    summary = []
    for metric in metric_cols:
        vals = pd.to_numeric(mapped[metric], errors="coerce").dropna()
        summary.append({
            "metric": metric,
            "mean": vals.mean() if len(vals) else None,
            "median": vals.median() if len(vals) else None,
            "std": vals.std() if len(vals) else None,
            "n_cell_types": int(vals.shape[0]),
        })
    pd.DataFrame(summary).to_csv(outdir / "precision_recall_summary.csv", index=False)


if __name__ == "__main__":
    import sys
    with open(sys.argv[1]) as f:
        cfg = json.load(f)
    main(cfg)
```

---

## 16. Prompt design for AgentCo-Op nodes

### 16.1 Planner prompt

```text
You are the Planner for AgentCo-Op. The user has provided GitHub repositories for Seurat and Signac, GEO links for SHARE-seq skin RNA/ATAC data, cell-type labels, and CellMarker 2.0. Compile the simplest workflow that can satisfy the request.

Do not use an unnecessary multi-agent debate. Use two parallel execution branches: one Seurat branch for RNA markers and one Signac branch for ATAC markers. Then join the branches with deterministic set operations and CellMarker evaluation.

Your plan must specify:
1. data download and schema inspection;
2. barcode and cell-type label alignment;
3. Seurat Docker sandbox construction and wrapper execution;
4. Signac Docker sandbox construction and wrapper execution;
5. marker gene harmonization;
6. per-cell-type intersection and union;
7. CellMarker 2.0 evaluation;
8. final report and artifact packaging.

For every step, state expected artifacts and validation checks.
```

### 16.2 Repository profiler prompt

```text
You are a Repository Profiler. Inspect the provided repository and documentation URLs. Identify the language, package manager, installation method, likely entrypoints, required system dependencies, and a minimal way to call the package headlessly inside Docker.

Return a JSON profile with:
- repository name and URL;
- detected language;
- installation strategy;
- Docker base image recommendation;
- expected capabilities;
- risks and repair strategies;
- suggested wrapper entrypoint.

Do not invent APIs. If the repository is an R package, propose an Rscript wrapper that calls documented functions.
```

### 16.3 Seurat marker agent prompt

```text
You are the Seurat RNA Marker Agent. Your only job is to run an auditable Seurat-based scRNA-seq marker discovery workflow.

Inputs:
- RNA count matrix with genes as rows and cells as columns.
- Cell-type metadata containing RNA barcodes and cell-type labels.
- top_n and marker-testing parameters.

Tasks:
1. Load the RNA count matrix.
2. Align cells to metadata by RNA barcode.
3. Remove excluded labels such as Mixed.
4. Create a Seurat object.
5. Normalize counts with LogNormalize.
6. Set cell-type labels as identities.
7. Run FindAllMarkers with the configured parameters.
8. Export all markers and top-N markers per cell type.

Output only structured artifacts and a concise execution summary. Do not perform biological interpretation.
```

### 16.4 Signac marker agent prompt

```text
You are the Signac ATAC Marker Agent. Your job is to run an auditable Signac-based scATAC-seq marker-gene workflow.

Inputs:
- ATAC peak count matrix with peaks as rows and cells as columns.
- Peak BED file.
- Cell-type metadata containing ATAC and RNA barcode columns.
- mm10 genome annotation.
- top_n and differential-accessibility parameters.

Primary method:
1. Load the peak count matrix and peak BED file.
2. Align cells to metadata using the barcode column that maximizes overlap with ATAC matrix columns.
3. Create a ChromatinAssay.
4. Attach mm10 gene annotation.
5. Set cell-type labels as identities.
6. For each cell type, run differential accessibility against all other cell types.
7. Map marker peaks to nearby genes.
8. Aggregate peak-level evidence into gene-level ATAC marker scores.
9. Export all peak markers, peak-to-gene mappings, all marker genes, and top-N genes per cell type.

Optional method:
If the fragment file is available and runtime permits, compute GeneActivity and run gene-activity marker analysis as a sensitivity check.

Do not use RNA expression values to define ATAC markers.
```

### 16.5 Set operation and evaluation prompt

```text
You are the Marker Set Evaluation Agent. Your inputs are RNA top-N markers, ATAC top-N marker genes, and CellMarker 2.0 mouse skin reference markers.

Tasks:
1. Harmonize gene symbols while preserving original symbols for reporting.
2. For each cell type, compute RNA, ATAC, intersection, and union marker sets.
3. Parse CellMarker and create gold marker sets for each mapped cell type.
4. Compute precision_rna, precision_atac, precision_intersection, recall_rna, recall_atac, and recall_union.
5. Report macro and micro averages.
6. Report cell types that could not be mapped to CellMarker.
7. Produce tables and figures.

Be explicit about denominator choices and missing-label handling.
```

### 16.6 Integrator / Reporter prompt

```text
You are the Integrator and Reporter. Summarize the collaborative workflow and results.

The report must include:
1. what repositories were wrapped;
2. what Docker images were built;
3. what data files were used;
4. how barcodes and labels were aligned;
5. Seurat RNA marker results;
6. Signac ATAC marker-gene results;
7. intersection and union statistics;
8. CellMarker precision/recall metrics;
9. collaboration-gain analysis;
10. failure cases and caveats;
11. links to all artifacts and scripts;
12. a clear statement of whether AgentCo-Op demonstrated multi-tool collaboration beyond a single-tool workflow.
```

---

## 17. Dynamic refinement and repair gates

AgentCo-Op should not mutate the topology for every minor issue. It should activate a gated repair subgraph only for concrete failures.

### 17.1 Data repair gates

| Failure signal | Repair action |
|---|---|
| Download URL returns HTML instead of gzip | Retry with GEO sample page file link; use GSE140203_RAW.tar as fallback. |
| Matrix delimiter or orientation unclear | Inspect first lines; infer whether first column is feature ID; try sparse conversion. |
| RNA barcode overlap below threshold | Try alternate label columns; trim whitespace; test suffix removal. |
| ATAC barcode overlap below threshold | Try ATAC barcode column, RNA barcode column, and barcodes file; use mapping from `celltype.txt`. |
| Cell type column unclear | Choose low-cardinality text column with biologically meaningful labels; ask evaluator to verify. |

### 17.2 Seurat repair gates

| Failure signal | Repair action |
|---|---|
| Seurat GitHub install fails | Install CRAN Seurat if compatible; record deviation. |
| Memory error while loading RNA matrix | Use sparse parser; optionally subset for smoke test, then full run with more memory. |
| `avg_log2FC` column absent | Use `avg_logFC` fallback. |
| Marker table empty for a cell type | Lower `logfc_threshold`; verify minimum cell count. |

### 17.3 Signac repair gates

| Failure signal | Repair action |
|---|---|
| Signac GitHub install fails | Use `timoast/signac:latest` Docker base or Bioconductor/CRAN-compatible install. |
| Peak names mismatch BED rows | Construct peak names from BED coordinates and align by row order. |
| `ClosestFeature` fails | Use `distanceToNearest` from `GenomicRanges` as fallback. |
| `latent.vars = nCount_peaks` missing | Compute `nCount_peaks` from count matrix or use the Seurat-created assay metadata column. |
| ATAC marker table empty | Lower `min_pct` to 0.01, verify labels, and rerun. |
| Fragment-based GeneActivity too slow | Skip optional sensitivity analysis and report that the primary peak-to-gene method was used. |

### 17.4 CellMarker repair gates

| Failure signal | Repair action |
|---|---|
| Primary CellMarker download unavailable | Try mirror download page or cached file. |
| No exact tissue match for Skin | Use extended tissue filter: Skin, Epidermis, Dermis, Hair follicle, Hair. |
| Dataset label does not match CellMarker label | Generate alias candidates, verify marker plausibility, and write mapping table. |
| Too few gold markers for a cell type | Mark the cell type as low-coverage and exclude from matched-only macro average. |

---

## 18. Baselines and ablations

### 18.1 Biological baselines

| Baseline | Purpose |
|---|---|
| RNA-only Seurat markers | Measures what a single RNA tool can recover. |
| ATAC-only Signac markers | Measures what a single ATAC tool can recover. |
| Random RNA marker sets with same size | Negative control for RNA precision/recall. |
| Random ATAC marker sets with same size | Negative control for ATAC precision/recall. |
| Shuffled cell labels | Negative control for cell-type specificity. |

### 18.2 Systems ablations

| Ablation | Expected evidence |
|---|---|
| No AgentCo-Op; manually run Seurat only | Cannot produce ATAC marker sets or cross-modal union/intersection. |
| No AgentCo-Op; manually run Signac only | Cannot produce RNA marker sets or cross-modal union/intersection. |
| AgentCo-Op without dynamic repair | More failures on dependency, barcode, or CellMarker parsing issues. |
| AgentCo-Op without typed artifact schemas | Higher risk of incompatible handoff between Seurat, Signac, and evaluator nodes. |
| AgentCo-Op with one combined monolithic R node | May complete the task, but weakens the specialized-node collaboration claim. |

### 18.3 Required comparison table

The final report should include:

```text
method
uses_seurat
uses_signac
uses_cellmarker
uses_cross_modal_set_ops
manual_interventions
completed
n_artifacts_produced
precision_rna_mean
precision_atac_mean
precision_intersection_mean
recall_rna_mean
recall_atac_mean
recall_union_mean
```

---

## 19. Expected AgentCo-Op outputs

At minimum, the final run should produce:

```text
compiled_workflow_graph.json
agent_registry.json
docker_build_report.json
data_inventory.json
barcode_alignment_report.json
celltype_distribution.csv
seurat_rna_markers_all.csv
rna_top_markers_by_celltype.json
signac_atac_peak_markers_all.csv
peak_to_gene_mapping.csv
signac_atac_marker_genes_all.csv
atac_top_marker_genes_by_celltype.json
gene_symbol_harmonization_report.json
marker_set_operations_by_celltype.json
marker_set_sizes_by_celltype.csv
cellmarker_gold_markers_by_celltype.json
cellmarker_label_mapping.csv
precision_recall_by_celltype.csv
precision_recall_summary.csv
collaboration_gain_table.csv
marker_overlap_heatmap.png
precision_recall_barplot.png
final_report.md
reproducibility_notebook.ipynb
execution_trace.jsonl
run_manifest.json
```

### 19.1 Final report structure

```text
1. Executive summary
2. Input repositories and datasets
3. Compiled workflow graph
4. Sandbox and environment summary
5. Data schema and barcode alignment
6. RNA marker discovery with Seurat
7. ATAC marker-gene discovery with Signac
8. Cross-modal set operations
9. CellMarker 2.0 evaluation
10. Collaboration-gain analysis
11. Dynamic repairs triggered
12. Limitations
13. Reproducibility instructions
14. Artifact links
```

### 19.2 Run manifest

```json
{
  "case_id": "shareseq_skin_seurat_signac_collaboration",
  "timestamp_utc": "...",
  "agentcoop_commit": "...",
  "repositories": {
    "seurat": {
      "url": "https://github.com/satijalab/seurat",
      "commit": "..."
    },
    "signac": {
      "url": "https://github.com/stuart-lab/signac",
      "commit": "..."
    }
  },
  "docker_images": {
    "seurat_agent": "...",
    "signac_agent": "..."
  },
  "datasets": {
    "rna_counts_sha256": "...",
    "atac_counts_sha256": "...",
    "celltype_labels_sha256": "..."
  },
  "parameters": {
    "top_n": 50,
    "rna_test_use": "wilcox",
    "atac_test_use": "LR",
    "cellmarker_filter": "mouse_skin"
  },
  "completed_steps": [
    "download",
    "schema_inspection",
    "barcode_alignment",
    "seurat_rna_markers",
    "signac_atac_markers",
    "set_operations",
    "cellmarker_evaluation",
    "report"
  ]
}
```

---

## 20. Practical compute recommendations

The main experiment should not require the optional 5 GB fragment file.

Recommended resources for the primary peak-to-gene experiment:

```text
CPU:    8-16 cores
RAM:    32-64 GB
Disk:   100 GB free
GPU:    not required
```

Run in two stages:

1. **Smoke run**:
   - subsample up to 200 cells per cell type;
   - top N = 20;
   - confirm Docker images, parsing, barcode alignment, and output schemas.
2. **Full run**:
   - use all cells after filtering;
   - top N = 50;
   - sensitivity top N = 20 and 100;
   - optional fragment-based gene activity if resources permit.

---

## 21. Important methodological caveats

1. **CellMarker is not a perfect ground truth.** It is a manually curated marker database, not an exhaustive truth set for every SHARE-seq skin label. Report database coverage and unmapped cell types.
2. **ATAC marker genes are indirect.** Peak accessibility is regulatory evidence. Mapping peaks to nearest genes is a useful approximation but can miss distal enhancer-gene relationships.
3. **Intersection can be too small.** If the RNA and ATAC marker sets are each top 50 genes, some cell types may have small or empty intersections. This is not necessarily failure; report NA precision for empty intersections and use sensitivity analyses with top N = 100.
4. **Union can inflate recall by increasing set size.** Always report union size and precision_union as an auxiliary metric, even though the requested primary union metric is recall.
5. **Do not use RNA expression to create ATAC markers.** The primary Signac ATAC marker set should be generated from peak accessibility, then mapped to genes. RNA may only be used for label alignment and final comparison.
6. **Keep the graph simple.** This task does not require many LLM agents. Most of the value comes from correct repository wrapping, typed artifacts, deterministic tool execution, and targeted repair.

---

## 22. Minimal success checklist

The case study should be considered successful if all of the following are true:

```text
[ ] AgentCo-Op accepted GitHub URLs and data URLs as input.
[ ] AgentCo-Op generated or selected Docker sandboxes for Seurat and Signac.
[ ] AgentCo-Op registered Seurat and Signac as separate callable agent nodes.
[ ] The Seurat node produced RNA marker genes per cell type.
[ ] The Signac node produced ATAC-derived marker genes per cell type.
[ ] The framework aligned cell-type labels across RNA and ATAC modalities.
[ ] The framework computed per-cell-type intersections and unions.
[ ] The framework downloaded or used CellMarker 2.0 marker annotations.
[ ] The framework produced requested precision and recall metrics.
[ ] The final report includes artifact links, code, environment details, and execution trace.
[ ] At least one dynamic repair or validation decision is logged, or the trace explicitly states that no repair was needed.
```

---

## 23. Recommended final narrative for the paper

A concise paper-style description could be:

> In the SHARE-seq skin multiome case study, AgentCo-Op received only tool repository URLs, public GEO dataset links, cell-type labels, and an analysis request. The compiler synthesized a two-branch workflow that wrapped Seurat as an RNA marker-discovery node and Signac as an ATAC marker-discovery node. AgentCo-Op aligned cell barcodes and labels across modalities, executed both tools in isolated Docker sandboxes, converted ATAC peak-level markers into gene-level marker sets, and joined the branches through deterministic set operations. Against CellMarker 2.0 mouse skin annotations, the system reported RNA-only, ATAC-only, intersection, and union marker retrieval metrics. This demonstrates a tool-collaboration pattern in which independent domain tools are composed into a cross-modal workflow that neither tool directly provides alone.

---

## 24. References and useful links

- SHARE-seq GEO series GSE140203: `https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE140203`
- SHARE-seq RNA sample GSM4156608: `https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM4156608`
- SHARE-seq ATAC sample GSM4156597: `https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM4156597`
- SHARE-seq paper: Ma et al., *Chromatin Potential Identified by Shared Single-Cell Profiling of RNA and Chromatin*, Cell, 2020.
- Seurat GitHub: `https://github.com/satijalab/seurat`
- Seurat tutorial: `https://satijalab.org/seurat/articles/pbmc3k_tutorial`
- Seurat FindMarkers reference: `https://satijalab.org/seurat/reference/findmarkers`
- Signac GitHub: `https://github.com/stuart-lab/signac`
- Signac documentation: `https://stuartlab.org/signac/`
- Signac PBMC multiomic tutorial: `https://stuartlab.org/signac/articles/pbmc_multiomic`
- Signac mouse brain vignette: `https://stuartlab.org/signac/articles/mouse_brain_vignette`
- Signac GeneActivity reference: `https://stuartlab.org/signac/reference/geneactivity`
- CellMarker 2.0 paper: Hu et al., *CellMarker 2.0: an updated database of manually curated cell markers in human/mouse and web tools based on scRNA-seq data*, Nucleic Acids Research, 2023.
- CellMarker 2.0 download page: `http://www.bio-bigdata.center/CellMarker_download.html`
- CellMarker mirror: `https://xteam.xbio.top/CellMarker/download.jsp`

---

## 25. End-to-end reproduction (Session 9)

This case study is reproducible from a fresh clone of `clean-dev` with the
following sequence. The runs land at:

- **Path B** — Python sandbox image, fastest:
  `runs/case2/shareseq_skin_docker_b/`
- **Path C** — real R Seurat + R Signac in rocker/Bioconductor image:
  `runs/case2/shareseq_skin_docker_c/`

### 25.1 One-time host setup

```bash
brew install colima docker docker-compose docker-buildx htslib coreutils
colima start --runtime docker --cpu 6 --memory 14 --disk 120
docker context use colima
```

(A 12 GB colima is enough for Path B; **Path C needs 14 GB** because
`Signac::FindAllMarkers` peaks at ~ 8 GB resident on the SHARE-seq dataset.
`Signac::GeneActivity` needs ≥ 22 GB and is opt-in — see §25.5.)

Drop the OpenAI key the integrator step uses:

```bash
mkdir -p .secrets && echo "OPENAI_API_KEY=sk-..." > .secrets/api-key
```

### 25.2 One-time data + image setup (`scripts/cs2_setup.sh`)

```bash
bash scripts/cs2_setup.sh
```

The script is **idempotent** — re-runs skip every step whose outputs already
exist. It will:

1. download the 5 GEO files (`GSM4156608_*.rna.counts.txt.gz`,
   `GSM4156597_*.counts.txt.gz`, `peaks.bed.gz`, `barcodes.txt.gz`,
   `celltype.txt.gz`) plus the optional 5 GB
   `GSM4156597_*.atac.fragments.bed.gz`;
2. fetch PanglaoDB markers (with `external/SpatialAgent/data/`
   fallback if the live URL 403s);
3. convert the dense gzipped RNA TSV into a 10X-style sparse MTX trio
   (`scripts/dense_tsv_to_mtx.py`) — without this step `Seurat` blows up
   R's heap by ~24 GB transient on the 32 k-cell matrix;
4. assemble the ATAC MTX trio from the GEO sparse counts (the
   `.txt.gz` is already MatrixMarket — we rename it to `matrix.mtx.gz`
   and supply `features.tsv.gz` from `peaks.bed.gz` and
   `barcodes.tsv.gz` from `barcodes.txt.gz`);
5. sort + bgzip + tabix-index the fragments BED with the comma → dot
   barcode normalisation Signac expects (~ 22 min on aarch64 with
   GNU `gsort --parallel=8`);
6. build the two Docker images (`agentcoop-runtime:case-study` ≈ 2 min,
   `agentcoop-r-runtime:case-study` ≈ 50 min on first build, seconds on
   incremental rebuilds via leaf-layer caching).

### 25.3 Path B — fastest end-to-end run (Python sandbox)

```bash
set -a; source .secrets/api-key; set +a
python -m agentcoop.cli collaborate \
    --request case_study_2.request.yaml \
    --workdir runs/case2/shareseq_skin_docker_b \
    --docker
```

Wall time: ~ 31 min (parallel Seurat + Signac Python adapters in two
containers). Outputs include `precision_recall_panglaodb_*.csv` alongside
the original `precision_recall_*.csv` files.

### 25.4 Path C — real R Seurat + R Signac (default end-to-end)

The shipped `case_study_2.request.yaml` already routes Seurat/Signac through
`agentcoop-r-runtime:case-study` via `sandbox_overrides.runtime_image`. Just
launch with branch concurrency = 1 (so both R containers fit on a 14 GB
colima):

```bash
set -a; source .secrets/api-key; set +a
export AGENTCOOP_BRANCH_CONCURRENCY=1
python -m agentcoop.cli collaborate \
    --request case_study_2.request.yaml \
    --workdir runs/case2/shareseq_skin_docker_c \
    --docker
```

Wall time: ~ 55–60 min (Seurat 14 min + Signac 41 min + integrator 1.5 min).

### 25.5 Optional sensitivity analysis — `Signac::GeneActivity`

`case_study_2.md` §11 declares peak-to-gene as the **primary** ATAC
marker-gene method, with `Signac::GeneActivity` (§11.2) as an optional
sensitivity analysis. The `signac_method` knob in
`case_study_2.request.yaml`'s `parameters.atac_marker_method` block selects
between them. To run GeneActivity-primary you need:

- colima ≥ 22 GB memory (the "extracting reads overlapping genomic regions"
  step OOMs at 14 GB on this dataset)
- the fragments file at `data/shareseq_skin/GSM4156597_*.atac.fragments.tsv.bgz`
  with a tabix index next to it (the setup script produces both)

Then change one line in the YAML:

```yaml
parameters:
  atac_marker_method:
    signac_method: gene_activity   # was: peak_to_gene
```

and re-run the Path C command. Path C produces the same artifact filenames
in either mode, so downstream nodes (CellMarkerEvaluator + integrator)
don't change.
