# Case Study 2 — Seurat × Signac Tool-Agent Collaboration on a Human Heart Paired snRNA/snATAC Multiome Dataset

## 0. Purpose

This case study tests whether **AgentCo-Op can compile and execute a collaborative workflow from external tool repositories**. The user provides only:

1. the task description;
2. the external tool GitHub URLs and tutorials;
3. a paired single-nucleus human heart multiome dataset;
4. two independent marker-gene databases; and
5. the desired output artifacts.

AgentCo-Op must autonomously inspect the tools, build Docker sandboxes, wrap the tools as execution nodes, run two modality-specific marker-finding agents, combine their outputs, evaluate them against independent marker databases, and return a fully auditable artifact package.

The scientific hypothesis remains the same as the earlier mouse-skin version, but the dataset is now switched to **human heart**:

```text
precision_intersection > precision_rna > precision_atac
recall_union > recall_rna > recall_atac
```

This ordering is a hypothesis, not a hard-coded success criterion. If it does not hold under the primary configuration, AgentCo-Op must report that honestly and run the pre-registered sensitivity analyses instead of tuning parameters post hoc.

---

## 1. Why switch from SHARE-seq mouse skin to human heart?

The original mouse skin SHARE-seq dataset is technically useful, but it is not ideal for this case study because the biological ground truth is less aligned with the project's broader tissue-biology narrative. A human heart dataset is better for three reasons:

1. **Better alignment with the project narrative.** Case Study 1 already uses human heart biology through TissueAgent-style external-agent collaboration. A human heart multiome marker task makes Case Study 2 conceptually consistent with the rest of the AgentCo-Op paper.
2. **Better fit for known marker databases.** Human heart cell types such as cardiomyocytes, fibroblasts, endothelial cells, smooth muscle cells, macrophages, and pericytes are broadly represented in curated marker resources.
3. **Better test of tool-agent collaboration.** The selected human heart dataset is a 10x single-cell multiome dataset with paired gene-expression and ATAC modalities, metadata labels, filtered feature-barcode matrices, and ATAC fragment files. This lets Seurat and Signac be wrapped as separate execution agents while still operating on barcodes from the same nuclei.

---

## 2. Primary dataset

### 2.1 Dataset selected

Use the human heart paired single-nucleus RNA + ATAC multiome dataset from:

```text
GSE270788 — Targeting Immune-Fibroblast Crosstalk in Myocardial Infarction and Cardiac Fibrosis III
NCBI GEO: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE270788
Organism: Homo sapiens
Tissue: human heart
Assay: paired single-nucleus RNA-seq and ATAC-seq using 10x Chromium Next GEM Single Cell Multiome ATAC + Gene Expression
Processed data: CellRanger ARC filtered matrices, ATAC fragments, and metadata.csv
```

GEO describes this series as paired single-nucleus RNA and ATAC multiome sequencing of human heart samples, including donor, acute myocardial infarction, ischemic cardiomyopathy, and non-ischemic cardiomyopathy samples. For this case study, the disease-state labels are not the main endpoint; they provide real biological heterogeneity, while the marker-evaluation task focuses on cell-type markers.

### 2.2 Default pilot sample

The default reproducible pilot should use one paired sample to keep runtime and storage manageable:

```text
Sample ID: MA7
Disease state: ICM
RNA/GEX sample: GSM8352050, title = GEX MA7
ATAC sample: GSM8352073, title = ATAC MA7
```

Required files:

```text
GEX matrix:
  GEO page:
    https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM8352050
  Direct NCBI download endpoint:
    https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSM8352050&file=GSM8352050_MA7_filtered_feature_bc_matrix.h5&format=file
  Expected local path:
    data/raw/gse270788/MA7/GSM8352050_MA7_filtered_feature_bc_matrix.h5

ATAC fragments:
  GEO page:
    https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM8352073
  Direct NCBI download endpoint:
    https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSM8352073&file=GSM8352073_MA7_atac_fragments.tsv.gz&format=file
  Expected local path:
    data/raw/gse270788/MA7/GSM8352073_MA7_atac_fragments.tsv.gz

Metadata:
  GEO series page:
    https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE270788
  Direct NCBI download endpoint:
    https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSE270788&file=GSE270788_metadata.csv.gz&format=file
  Expected local path:
    data/raw/gse270788/GSE270788_metadata.csv.gz
```

The `filtered_feature_bc_matrix.h5` file should be treated as a 10x multiome feature-barcode matrix. In Seurat/Signac it should be loaded with `Read10X_h5()`. The expected returned object is a named list containing at least:

```text
Gene Expression
Peaks
```

AgentCo-Op must verify this structure at runtime. If the H5 file does not contain both assays, the Data Inspector must stop the workflow and route the issue to the Repair Gate.

### 2.3 Optional full experiment

After the pilot sample succeeds, run the same workflow across all GSE270788 paired samples:

```text
MA5, MA6, MA7, MA8, MA9, MA10, MA11, MA13, MA14, MA19, MA20, MA22, MA23,
MA24, MA25, MA26, MA27, MA28, MA29, MA30, MA31, MA32, MA33
```

The full experiment should aggregate per-cell-type metrics across samples using either:

1. **macro-averaging by cell type within each sample**, followed by a mean across samples; or
2. **pooled marker discovery per cell type across samples**, after confirming barcode and label compatibility.

The pilot run is the required first deliverable. The all-sample run is a scalability extension.

### 2.4 Backup sample if MA7 fails

Use donor sample MA5 as a backup:

```text
RNA/GEX sample: GSM8352048, title = GEX MA5
ATAC sample: GSM8352071, title = ATAC MA5
Disease state: Donor
GEX direct endpoint:
  https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSM8352048&file=GSM8352048_MA5_filtered_feature_bc_matrix.h5&format=file
ATAC direct endpoint:
  https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSM8352071&file=GSM8352071_MA5_atac_fragments.tsv.gz&format=file
```

MA5 is a donor sample and is biologically attractive, but its fragment file is larger. Therefore, MA7 remains the default pilot sample unless the user explicitly wants donor-only analysis.

---

## 3. Ground-truth marker databases

Use two independent marker resources and evaluate them separately.

### 3.1 Database A: CellMarker 2.0

Primary landing page:

```text
https://bio-bigdata.hrbmu.edu.cn/CellMarker/CellMarker_download.html
```

Primary all-marker file:

```text
https://bio-bigdata.hrbmu.edu.cn/CellMarker/CellMarker_download_files/file/Cell_marker_All.xlsx
```

Filtering rule:

```text
species = Human
organ/tissue/tissue_type = Heart
```

Implementation notes:

- The exact column names may vary. The Database Agent must inspect the Excel header and detect the relevant fields case-insensitively.
- Accept candidate tissue columns such as `tissue`, `tissue_type`, `organ`, `tissueType`, or equivalent.
- Accept candidate species columns such as `species`, `speciesType`, or equivalent.
- If exact `Heart` filtering yields zero rows, run a documented fuzzy filter using `heart`, `cardiac`, `myocardium`, `atria`, `ventricle`, `left atrium`, `left ventricle`, and `right ventricle`, then write both the exact and fuzzy counts to `db_filter_log.json`.
- Do not silently replace CellMarker 2.0 with the older CellMarker text files. Older text files can be used only as a clearly labeled fallback sensitivity analysis.

### 3.2 Database B: PanglaoDB

Primary marker file:

```text
https://panglaodb.se/markers/PanglaoDB_markers_27_Mar_2020.tsv.gz
```

Filtering rule:

```text
species includes Hs
organ = Heart
```

Implementation notes:

- PanglaoDB species values often contain compact codes such as `Hs`, `Mm`, or combined values such as `Mm Hs`. For human heart, include rows where `species` contains `Hs`.
- Use `organ == Heart` as the primary exact filter.
- If exact filtering returns zero rows, run a documented fuzzy organ filter using `heart`, `cardiac`, `atria`, `ventricle`, and `myocardium`.
- PanglaoDB columns should include fields such as `official gene symbol`, `cell type`, `organ`, `species`, `sensitivity`, and `specificity`. The parser must normalize spaces, punctuation, and case in column names.

### 3.3 Database output contract

The Database Agent must export:

```text
artifacts/databases/cellmarker2_human_heart_markers.csv
artifacts/databases/panglaodb_hs_heart_markers.csv
artifacts/databases/db_filter_log.json
artifacts/databases/db_marker_sets.json
```

Each database marker CSV must contain at least:

```text
database
raw_cell_type
mapped_cell_type
gene_symbol
species
tissue_or_organ
source_row_id
source_notes
```

---

## 4. Tool-agent nodes

### 4.1 Agent 1: RNA marker gene finder

```text
Agent name: RNA Marker Agent
Tool: Seurat
GitHub: https://github.com/satijalab/seurat
Tutorial: https://satijalab.org/seurat/articles/pbmc3k_tutorial
Runtime: R
Method: Seurat::FindAllMarkers
Input: Gene Expression assay from the 10x multiome H5 file + author-provided cell-type labels from metadata
Output: per-cell-type ranked RNA marker genes
```

This agent must run independently of the ATAC agent. It can share the same cell labels but must not receive ATAC marker results.

Required output schema:

```text
cell_type
gene
p_val
avg_log2FC
pct.1
pct.2
p_val_adj
rank
agent
sample_id
parameters_hash
```

Primary parameters:

```yaml
return_thresh: 0.05
only_pos: true
test_use: wilcox
min_pct: 0.10
logfc_threshold: 0.25
top_n: 50
min_cells_per_type: 20
max_cells_per_type: null
random_seed: 42
```

`top_n` is applied after filtering by adjusted p-value and positive log fold change. Genes should be sorted by:

```text
p_val_adj ascending, avg_log2FC descending
```

### 4.2 Agent 2: ATAC marker gene finder

```text
Agent name: ATAC Marker Agent
Tool: Signac + Seurat
GitHub: https://github.com/stuart-lab/signac
Tutorials:
  https://stuartlab.org/signac/articles/pbmc_vignette
  https://stuartlab.org/signac/articles/pbmc_multiomic
Runtime: R
Method: Signac::GeneActivity followed by Seurat::FindAllMarkers
Input: Peaks assay from the 10x multiome H5 file + ATAC fragment file + author-provided cell-type labels from metadata
Output: per-cell-type ranked marker genes from gene activity scores
```

The ATAC agent must convert chromatin accessibility to a gene-level activity score matrix using `Signac::GeneActivity()`. The intended method is:

1. read the 10x multiome H5 with `Read10X_h5()`;
2. extract the `Peaks` matrix;
3. create a `ChromatinAssay` with the peak matrix, hg38 gene annotation, and the sample's fragment file;
4. run `GeneActivity()` to sum fragments overlapping gene bodies plus promoter regions;
5. create a gene-activity assay;
6. normalize the gene-activity assay; and
7. run `FindAllMarkers()` with the same DE parameters as the RNA agent.

Required output schema:

```text
cell_type
gene
p_val
avg_log2FC
pct.1
pct.2
p_val_adj
rank
agent
sample_id
parameters_hash
```

Primary parameters:

```yaml
return_thresh: 0.05
only_pos: true
test_use: wilcox
min_pct: 0.10
logfc_threshold: 0.25
top_n: 50
min_cells_per_type: 20
max_cells_per_type: null
random_seed: 42
gene_activity_extend_upstream: 2000
gene_activity_extend_downstream: 0
genome: hg38
annotation_package: EnsDb.Hsapiens.v86
```

### 4.3 Tool separation requirement

AgentCo-Op must register Seurat and Signac as separate execution nodes:

```text
RNA Marker Agent  -> uses only the RNA/Gene Expression assay
ATAC Marker Agent -> uses only the Peaks assay and fragments to derive gene activity
```

The collaboration happens through the Ensemble/Evaluation Agent, not by collapsing the two tool nodes into one monolithic script. This is essential because the case study is meant to show that AgentCo-Op can coordinate external tools as modular agent nodes.

---

## 5. Required structured user input to AgentCo-Op

Use this as the canonical task request for the experiment.

```yaml
case_study_id: case_study_2_human_heart_seurat_signac
objective: >
  Given a paired single-nucleus human heart multiome dataset with RNA and ATAC
  measurements from the same nuclei, use two separate external-tool agents to
  identify cell-type marker genes from each modality independently. The RNA
  agent should use Seurat FindAllMarkers on the gene-expression assay. The ATAC
  agent should use Signac GeneActivity to convert ATAC fragments into a gene
  activity matrix, then use Seurat FindAllMarkers with the same parameters.
  For each cell type, compute RNA markers, ATAC gene-activity markers,
  intersection markers, and union markers. Evaluate whether intersection
  increases precision and union increases recall relative to single-modality
  agents, using CellMarker 2.0 and PanglaoDB as two independent ground-truth
  marker databases.

dataset:
  name: GSE270788 human heart paired single-nucleus multiome
  organism: Homo sapiens
  tissue: human heart
  geo_series: GSE270788
  geo_series_url: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE270788
  default_sample_id: MA7
  default_sample_condition: ICM
  paired_cells: true
  assay_platform: 10x Chromium Next GEM Single Cell Multiome ATAC + Gene Expression
  gex_sample:
    accession: GSM8352050
    title: GEX MA7
    page: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM8352050
    file: GSM8352050_MA7_filtered_feature_bc_matrix.h5
    direct_download: https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSM8352050&file=GSM8352050_MA7_filtered_feature_bc_matrix.h5&format=file
  atac_sample:
    accession: GSM8352073
    title: ATAC MA7
    page: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM8352073
    file: GSM8352073_MA7_atac_fragments.tsv.gz
    direct_download: https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSM8352073&file=GSM8352073_MA7_atac_fragments.tsv.gz&format=file
  metadata:
    file: GSE270788_metadata.csv.gz
    direct_download: https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSE270788&file=GSE270788_metadata.csv.gz&format=file
  backup_sample:
    sample_id: MA5
    condition: Donor
    gex_accession: GSM8352048
    atac_accession: GSM8352071

ground_truth_marker_databases:
  cellmarker_2_0:
    landing_page: https://bio-bigdata.hrbmu.edu.cn/CellMarker/CellMarker_download.html
    marker_file: https://bio-bigdata.hrbmu.edu.cn/CellMarker/CellMarker_download_files/file/Cell_marker_All.xlsx
    filters:
      species: Human
      tissue_or_organ: Heart
  panglaodb:
    marker_file: https://panglaodb.se/markers/PanglaoDB_markers_27_Mar_2020.tsv.gz
    filters:
      species_contains: Hs
      organ: Heart

tools:
  rna_marker_agent:
    name: Seurat
    github: https://github.com/satijalab/seurat
    tutorial: https://satijalab.org/seurat/articles/pbmc3k_tutorial
    execution_language: R
    method: Seurat::FindAllMarkers
  atac_marker_agent:
    name: Signac
    github: https://github.com/stuart-lab/signac
    tutorials:
      - https://stuartlab.org/signac/articles/pbmc_vignette
      - https://stuartlab.org/signac/articles/pbmc_multiomic
    execution_language: R
    method: Signac::GeneActivity followed by Seurat::FindAllMarkers

parameters:
  top_n: 50
  return_thresh: 0.05
  only_pos: true
  test_use: wilcox
  min_pct: 0.10
  logfc_threshold: 0.25
  min_cells_per_type: 20
  max_cells_per_type: null
  random_seed: 42
  genome: hg38
  gene_activity_extend_upstream: 2000
  gene_activity_extend_downstream: 0
  primary_atac_gene_activity_method: Signac::GeneActivity_from_fragments
  sensitivity_grid:
    top_n: [25, 50, 100]
    return_thresh: [0.01, 0.05]
    logfc_threshold: [0.10, 0.25]

required_outputs:
  - link_preflight_report.json
  - executed_plan.json
  - dockerfiles and wrapper scripts for RNA and ATAC agents
  - RNA marker table per cell type
  - ATAC gene-activity marker table per cell type
  - top-N marker sets per modality and cell type
  - intersection and union marker sets per cell type
  - cell-type mapping table for CellMarker 2.0 and PanglaoDB
  - per-cell-type precision and recall table for RNA, ATAC, intersection, and union, evaluated separately against CellMarker 2.0 and PanglaoDB
  - mean and median precision/recall summary across mapped cell types
  - mean precision bar plot across methods
  - mean recall bar plot across methods
  - Venn diagram per major cell type, or a combined overlap panel
  - RNA/ATAC overlap heatmap across cell types
  - ATAC-only marker analysis, including ATAC-only genes found in either ground-truth database
  - all used marker lists from both databases and from both agents
  - final markdown report and executable notebook
```

---

## 6. AgentCo-Op compiled workflow topology

AgentCo-Op should compile the following topology.

```text
User task + dataset/tool/database URLs
        |
        v
Link Preflight Agent
        |
        v
Tool / repo inspection agent
        |
        v
Docker sandbox compiler
        |
        v
Data acquisition and schema inspector
        |
        +-----------------------------------+
        |                                   |
        v                                   v
RNA Marker Agent                       ATAC Marker Agent
Seurat::FindAllMarkers                 Signac::GeneActivity + Seurat::FindAllMarkers
        |                                   |
        +-----------------+-----------------+
                          |
                          v
Set Operation / Ensemble Agent
intersection and union per cell type
                          |
                          v
Ground Truth Database Agent
CellMarker 2.0 and PanglaoDB marker sets
                          |
                          v
Evaluation Agent
precision, recall, summaries, sensitivity analysis
                          |
                          v
Reporter Agent
figures, tables, artifacts, final report
```

Required dynamic repair gates:

1. **Link preflight gate:** verify all dataset, database, and tool links before running computation.
2. **H5 schema gate:** confirm that the H5 file contains both `Gene Expression` and `Peaks` assays.
3. **Barcode/metadata gate:** confirm that cell barcodes in the matrix can be matched to metadata labels.
4. **Fragment/index gate:** confirm that the ATAC fragments file is valid, bgzip-compressed, and tabix-indexed. If the `.tbi` index is missing, create it.
5. **Marker-output gate:** confirm that each included cell type has non-empty RNA and ATAC marker outputs after filtering.
6. **Database-mapping gate:** confirm that each evaluated cell type has a non-empty ground-truth marker set in each database.
7. **Target-ordering gate:** evaluate the hypothesis ordering, but never enforce it by parameter cherry-picking.

---

## 7. Docker sandbox design

### 7.1 R base image

Use a Bioconductor-compatible image to reduce dependency failures:

```dockerfile
FROM bioconductor/bioconductor_docker:RELEASE_3_19

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    build-essential \
    libcurl4-openssl-dev \
    libssl-dev \
    libxml2-dev \
    libhdf5-dev \
    libpng-dev \
    libjpeg-dev \
    libfreetype6-dev \
    zlib1g-dev \
    tabix \
    samtools \
    wget \
    curl \
    git \
    pandoc \
    && rm -rf /var/lib/apt/lists/*

RUN R -e "install.packages(c('Seurat', 'Signac', 'SeuratObject', 'Matrix', 'data.table', 'dplyr', 'tidyr', 'readr', 'stringr', 'ggplot2', 'patchwork', 'jsonlite', 'openxlsx', 'VennDiagram', 'pheatmap', 'ComplexHeatmap', 'circlize', 'R.utils'), repos='https://cloud.r-project.org')"

RUN R -e "BiocManager::install(c('EnsDb.Hsapiens.v86', 'BSgenome.Hsapiens.UCSC.hg38', 'ensembldb', 'GenomicRanges', 'GenomeInfoDb', 'Rsamtools'), ask=FALSE, update=FALSE)"

WORKDIR /workspace
```

AgentCo-Op can use either:

1. one shared R image with separate wrappers for Seurat and Signac; or
2. two derived images, one named `agentcoop-seurat` and one named `agentcoop-signac`.

For the case-study claim, the critical point is not that the base OS image is different, but that the **execution nodes are invoked separately with typed inputs and outputs**.

### 7.2 Required mounted directories

```text
/workspace/data/raw/
/workspace/data/processed/
/workspace/artifacts/
/workspace/logs/
/workspace/scripts/
```

### 7.3 Sandbox security

Run containers with:

```bash
--network none after data download is complete
--read-only for source code where possible
--memory 64g for pilot run, 128g+ for full run
--cpus 8 for pilot run, 16+ for full run
```

The downloader step can have network access. The RNA and ATAC execution agents should run offline after the inputs are cached and checksummed.

---

## 8. Data acquisition and preprocessing

### 8.1 Link preflight

Before downloading, AgentCo-Op must generate:

```text
artifacts/link_preflight_report.json
```

Minimum fields:

```json
{
  "url": "...",
  "purpose": "GEX matrix / ATAC fragment / metadata / database / tool repo / tutorial",
  "method": "HEAD or GET",
  "status": "ok / redirected / failed / binary_ok_not_rendered",
  "http_status": 200,
  "content_type": "...",
  "checked_at_utc": "...",
  "notes": "..."
}
```

The run must not proceed if any required dataset file is unavailable, unless a pre-registered backup sample is used and the substitution is recorded in `executed_plan.json`.

### 8.2 Download commands

```bash
mkdir -p data/raw/gse270788/MA7

curl -L \
  "https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSM8352050&file=GSM8352050_MA7_filtered_feature_bc_matrix.h5&format=file" \
  -o data/raw/gse270788/MA7/GSM8352050_MA7_filtered_feature_bc_matrix.h5

curl -L \
  "https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSM8352073&file=GSM8352073_MA7_atac_fragments.tsv.gz&format=file" \
  -o data/raw/gse270788/MA7/GSM8352073_MA7_atac_fragments.tsv.gz

curl -L \
  "https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSE270788&file=GSE270788_metadata.csv.gz&format=file" \
  -o data/raw/gse270788/GSE270788_metadata.csv.gz

mkdir -p data/raw/databases

curl -L \
  "https://bio-bigdata.hrbmu.edu.cn/CellMarker/CellMarker_download_files/file/Cell_marker_All.xlsx" \
  -o data/raw/databases/Cell_marker_All.xlsx

curl -L \
  "https://panglaodb.se/markers/PanglaoDB_markers_27_Mar_2020.tsv.gz" \
  -o data/raw/databases/PanglaoDB_markers_27_Mar_2020.tsv.gz
```

### 8.3 Checksums

After downloading, compute checksums:

```bash
sha256sum data/raw/gse270788/MA7/* > artifacts/input_checksums.sha256
sha256sum data/raw/gse270788/GSE270788_metadata.csv.gz >> artifacts/input_checksums.sha256
sha256sum data/raw/databases/* >> artifacts/input_checksums.sha256
```

### 8.4 Fragment indexing

Signac requires fragment-level access for the intended `GeneActivity()` path. AgentCo-Op must check whether a tabix index exists:

```bash
if [ ! -f data/raw/gse270788/MA7/GSM8352073_MA7_atac_fragments.tsv.gz.tbi ]; then
  tabix -p bed data/raw/gse270788/MA7/GSM8352073_MA7_atac_fragments.tsv.gz || true
fi
```

If direct indexing fails, run the repair path:

```bash
mkdir -p data/processed/gse270788/MA7
zcat data/raw/gse270788/MA7/GSM8352073_MA7_atac_fragments.tsv.gz \
  | sort -k1,1 -k2,2n \
  | bgzip -c > data/processed/gse270788/MA7/GSM8352073_MA7_atac_fragments.sorted.tsv.gz

tabix -p bed data/processed/gse270788/MA7/GSM8352073_MA7_atac_fragments.sorted.tsv.gz
```

Record whether the original or repaired fragment file was used.

### 8.5 Metadata and barcode matching

The Data Inspector must load the metadata and detect:

```text
barcode column
sample column
cell-type label column
disease/condition column, if present
```

Candidate column names:

```text
barcode: barcode, cell, cell_id, cellID, cell.name, Cell, cell_barcode
sample: sample, sample_id, orig.ident, library, donor, donor_id
cell type: cell_type, celltype, CellType, annotation, broad_cell_type, label, cluster_label, final_cell_type
condition: condition, disease, disease_state, status
```

Barcode normalization rules:

1. compare exact barcodes first;
2. if no match, strip sample prefixes such as `MA7_` or `MA7#`;
3. if no match, strip 10x suffixes such as `-1`, `-2` only after recording the transformation;
4. require at least 80% matrix-cell-to-metadata overlap for pilot execution;
5. if overlap is below 80%, stop and replan rather than silently continuing.

Required output:

```text
artifacts/preprocessing/barcode_match_report.json
artifacts/preprocessing/cell_metadata_matched.csv
```

---

## 9. R wrapper design

### 9.1 Shared input-preparation helper

Create `scripts/R/common_io.R`:

```r
suppressPackageStartupMessages({
  library(Seurat)
  library(Signac)
  library(data.table)
  library(Matrix)
  library(jsonlite)
  library(stringr)
})

normalize_gene <- function(x) {
  x <- trimws(as.character(x))
  x <- toupper(x)
  x <- gsub("\\\\..*$", "", x)
  x
}

normalize_barcode <- function(x) {
  x <- as.character(x)
  x <- gsub("^MA[0-9]+[_#-]", "", x)
  x
}

pick_col <- function(df, candidates) {
  nms <- names(df)
  low <- tolower(gsub("[^a-z0-9]", "", nms))
  cand <- tolower(gsub("[^a-z0-9]", "", candidates))
  hit <- match(cand, low)
  hit <- hit[!is.na(hit)]
  if (length(hit) == 0) return(NA_character_)
  nms[hit[1]]
}

load_multiome_counts <- function(h5_path) {
  counts <- Read10X_h5(h5_path)
  if (!is.list(counts)) {
    stop("Read10X_h5 did not return a list. Expected Gene Expression and Peaks assays.")
  }
  if (!("Gene Expression" %in% names(counts))) {
    stop(paste("Missing Gene Expression assay. Available assays:", paste(names(counts), collapse=", ")))
  }
  if (!("Peaks" %in% names(counts))) {
    stop(paste("Missing Peaks assay. Available assays:", paste(names(counts), collapse=", ")))
  }
  counts
}

load_and_match_metadata <- function(metadata_path, matrix_barcodes, sample_id, min_overlap=0.80) {
  meta <- fread(metadata_path)

  barcode_col <- pick_col(meta, c("barcode", "cell", "cell_id", "cellID", "cell.name", "Cell", "cell_barcode"))
  sample_col <- pick_col(meta, c("sample", "sample_id", "orig.ident", "library", "donor", "donor_id"))
  label_col <- pick_col(meta, c("cell_type", "celltype", "CellType", "annotation", "broad_cell_type", "label", "cluster_label", "final_cell_type"))

  if (is.na(barcode_col)) stop("Could not detect metadata barcode column")
  if (is.na(label_col)) stop("Could not detect metadata cell-type label column")

  if (!is.na(sample_col)) {
    meta_sample <- meta[toupper(as.character(get(sample_col))) == toupper(sample_id)]
    if (nrow(meta_sample) > 0) meta <- meta_sample
  }

  meta[, barcode_raw := as.character(get(barcode_col))]
  meta[, barcode_norm := normalize_barcode(barcode_raw)]
  meta[, cell_type_agentcoop := as.character(get(label_col))]

  mb <- data.table(barcode_matrix = matrix_barcodes)
  mb[, barcode_norm := normalize_barcode(barcode_matrix)]
  matched <- merge(mb, meta, by="barcode_norm", all.x=TRUE)

  overlap <- mean(!is.na(matched$cell_type_agentcoop))
  if (is.na(overlap) || overlap < min_overlap) {
    stop(sprintf("Barcode-to-metadata overlap %.3f is below threshold %.3f", overlap, min_overlap))
  }

  rownames_df <- matched$barcode_matrix
  out <- as.data.frame(matched)
  rownames(out) <- rownames_df
  out
}
```

### 9.2 RNA Marker Agent wrapper

Create `scripts/R/run_rna_marker_agent.R`:

```r
suppressPackageStartupMessages({
  library(Seurat)
  library(data.table)
  library(dplyr)
  library(jsonlite)
})
source("/workspace/scripts/R/common_io.R")

args <- commandArgs(trailingOnly=TRUE)
h5_path <- args[[1]]
metadata_path <- args[[2]]
sample_id <- args[[3]]
outdir <- args[[4]]

dir.create(outdir, recursive=TRUE, showWarnings=FALSE)
set.seed(42)

params <- list(
  return_thresh = 0.05,
  only_pos = TRUE,
  test_use = "wilcox",
  min_pct = 0.10,
  logfc_threshold = 0.25,
  top_n = 50,
  min_cells_per_type = 20,
  sample_id = sample_id,
  agent = "rna_marker_agent_seurat"
)

counts <- load_multiome_counts(h5_path)
rna_counts <- counts$`Gene Expression`
meta <- load_and_match_metadata(metadata_path, colnames(rna_counts), sample_id)

obj <- CreateSeuratObject(counts = rna_counts, assay = "RNA", meta.data = meta)
obj <- subset(obj, subset = !is.na(cell_type_agentcoop) & cell_type_agentcoop != "")
Idents(obj) <- obj$cell_type_agentcoop

cell_counts <- table(Idents(obj))
valid_types <- names(cell_counts)[cell_counts >= params$min_cells_per_type]
obj <- subset(obj, idents = valid_types)
Idents(obj) <- obj$cell_type_agentcoop

obj <- NormalizeData(obj, verbose=FALSE)
obj <- FindVariableFeatures(obj, verbose=FALSE)
obj <- ScaleData(obj, verbose=FALSE)

markers <- FindAllMarkers(
  obj,
  only.pos = params$only_pos,
  test.use = params$test_use,
  min.pct = params$min_pct,
  logfc.threshold = params$logfc_threshold,
  return.thresh = params$return_thresh
)

markers <- as.data.table(markers)
if (!("gene" %in% names(markers))) {
  markers[, gene := rownames(markers)]
}
markers[, agent := params$agent]
markers[, sample_id := sample_id]
markers[, gene_norm := normalize_gene(gene)]
setorder(markers, cluster, p_val_adj, -avg_log2FC)
markers[, rank := seq_len(.N), by=cluster]

fwrite(markers, file.path(outdir, "rna_markers_all.csv"))

topn <- markers[rank <= params$top_n]
fwrite(topn, file.path(outdir, "rna_markers_topN.csv"))

write_json(params, file.path(outdir, "rna_marker_params.json"), pretty=TRUE, auto_unbox=TRUE)
saveRDS(obj, file.path(outdir, "rna_seurat_object.rds"))
```

### 9.3 ATAC Marker Agent wrapper

Create `scripts/R/run_atac_marker_agent.R`:

```r
suppressPackageStartupMessages({
  library(Seurat)
  library(Signac)
  library(data.table)
  library(dplyr)
  library(jsonlite)
  library(EnsDb.Hsapiens.v86)
  library(BSgenome.Hsapiens.UCSC.hg38)
  library(GenomeInfoDb)
})
source("/workspace/scripts/R/common_io.R")

args <- commandArgs(trailingOnly=TRUE)
h5_path <- args[[1]]
fragment_path <- args[[2]]
metadata_path <- args[[3]]
sample_id <- args[[4]]
outdir <- args[[5]]

dir.create(outdir, recursive=TRUE, showWarnings=FALSE)
set.seed(42)

params <- list(
  return_thresh = 0.05,
  only_pos = TRUE,
  test_use = "wilcox",
  min_pct = 0.10,
  logfc_threshold = 0.25,
  top_n = 50,
  min_cells_per_type = 20,
  sample_id = sample_id,
  agent = "atac_marker_agent_signac_geneactivity",
  genome = "hg38",
  gene_activity_extend_upstream = 2000,
  gene_activity_extend_downstream = 0
)

counts <- load_multiome_counts(h5_path)
atac_counts <- counts$Peaks
meta <- load_and_match_metadata(metadata_path, colnames(atac_counts), sample_id)

annotation <- GetGRangesFromEnsDb(ensdb = EnsDb.Hsapiens.v86)
seqlevelsStyle(annotation) <- "UCSC"
genome(annotation) <- "hg38"

chrom_assay <- CreateChromatinAssay(
  counts = atac_counts,
  sep = c(":", "-"),
  fragments = fragment_path,
  annotation = annotation
)

obj <- CreateSeuratObject(counts = chrom_assay, assay = "ATAC", meta.data = meta)
obj <- subset(obj, subset = !is.na(cell_type_agentcoop) & cell_type_agentcoop != "")
Idents(obj) <- obj$cell_type_agentcoop

cell_counts <- table(Idents(obj))
valid_types <- names(cell_counts)[cell_counts >= params$min_cells_per_type]
obj <- subset(obj, idents = valid_types)
Idents(obj) <- obj$cell_type_agentcoop

DefaultAssay(obj) <- "ATAC"
gene.activities <- GeneActivity(
  obj,
  assay = "ATAC",
  extend.upstream = params$gene_activity_extend_upstream,
  extend.downstream = params$gene_activity_extend_downstream
)

obj[["GA"]] <- CreateAssayObject(counts = gene.activities)
DefaultAssay(obj) <- "GA"
obj <- NormalizeData(obj, assay="GA", normalization.method="LogNormalize", verbose=FALSE)
Idents(obj) <- obj$cell_type_agentcoop

markers <- FindAllMarkers(
  obj,
  assay = "GA",
  only.pos = params$only_pos,
  test.use = params$test_use,
  min.pct = params$min_pct,
  logfc.threshold = params$logfc_threshold,
  return.thresh = params$return_thresh
)

markers <- as.data.table(markers)
if (!("gene" %in% names(markers))) {
  markers[, gene := rownames(markers)]
}
markers[, agent := params$agent]
markers[, sample_id := sample_id]
markers[, gene_norm := normalize_gene(gene)]
setorder(markers, cluster, p_val_adj, -avg_log2FC)
markers[, rank := seq_len(.N), by=cluster]

fwrite(markers, file.path(outdir, "atac_gene_activity_markers_all.csv"))

topn <- markers[rank <= params$top_n]
fwrite(topn, file.path(outdir, "atac_gene_activity_markers_topN.csv"))

write_json(params, file.path(outdir, "atac_marker_params.json"), pretty=TRUE, auto_unbox=TRUE)
saveRDS(obj, file.path(outdir, "atac_gene_activity_seurat_object.rds"))
```

---

## 10. Ensemble and set operations

The Ensemble Agent takes the two `topN` marker tables and computes per cell type:

```text
markers_rna[cell_type]
markers_atac[cell_type]
markers_intersection[cell_type] = markers_rna[cell_type] ∩ markers_atac[cell_type]
markers_union[cell_type]        = markers_rna[cell_type] ∪ markers_atac[cell_type]
markers_atac_only[cell_type]    = markers_atac[cell_type] - markers_rna[cell_type]
markers_rna_only[cell_type]     = markers_rna[cell_type] - markers_atac[cell_type]
```

Use normalized gene symbols for set operations, but keep original symbols in the output.

Required outputs:

```text
artifacts/ensemble/marker_sets_by_cell_type.json
artifacts/ensemble/marker_sets_by_cell_type.csv
artifacts/ensemble/overlap_summary_by_cell_type.csv
artifacts/ensemble/atac_only_markers_by_cell_type.csv
artifacts/ensemble/rna_only_markers_by_cell_type.csv
```

`overlap_summary_by_cell_type.csv` schema:

```text
sample_id
cell_type
n_rna
n_atac
n_intersection
n_union
jaccard
n_rna_only
n_atac_only
```

---

## 11. Cell-type mapping for database evaluation

Only evaluate cell types that can be mapped to entries in a given database. The mapping must be explicit, saved, and manually reviewable.

Required mapping file:

```text
artifacts/databases/cell_type_mapping.csv
```

Schema:

```text
source_dataset_label
normalized_dataset_label
cellmarker_label
panglaodb_label
include_cellmarker
include_panglaodb
rationale
```

Initial mapping candidates:

| Dataset label pattern | CellMarker 2.0 label | PanglaoDB label | Notes |
|---|---|---|---|
| Cardiomyocyte, CM, Ventricular cardiomyocyte, Atrial cardiomyocyte | Cardiomyocyte | Cardiomyocytes | Keep broad mapping unless the DB has chamber-specific labels. |
| Fibroblast, Fib, Myofibroblast, Matrifibrocyte | Fibroblast / Myofibroblast | Fibroblasts / Myofibroblasts | Prefer exact subtype if available; otherwise use broad fibroblast. |
| Endothelial, Endothelial cell, EC | Endothelial cell | Endothelial cells | Include vascular and lymphatic ECs only if database supports the subtype or broad EC. |
| Smooth muscle cell, SMC | Smooth muscle cell | Smooth muscle cells | Include if database has heart/vascular SMC markers. |
| Pericyte, Mural cell | Pericyte or Smooth muscle cell | Pericytes / Smooth muscle cells | Prefer pericyte; if unavailable, either map to mural/SMC with rationale or exclude. |
| Macrophage, Mono/Mac, Monocyte | Macrophage / Monocyte | Macrophages / Monocytes | Use exact immune label if available. |
| T cell, B cell, NK cell | T cell / B cell / Natural killer cell | T cells / B cells / NK cells | Evaluate only if the database has heart-specific entries. |
| Adipocyte | Fat cell / adipocyte | Adipocytes | Include only if heart-specific entries exist. |
| Neuron, Schwann cell, Glial cell | Neuron / Schwann cell / Glial cell | Neurons / Schwann cells | Include only if present in heart-filtered database; otherwise exclude. |

Rules:

1. A cell type can be included for CellMarker but excluded for PanglaoDB, or vice versa.
2. If a database has no markers for a mapped label, exclude that label for that database.
3. Never compute recall using a zero-size DB marker set.
4. Report the number of included and excluded labels separately for each database.

---

## 12. Evaluation metrics

For each cell type and each database, compute:

```text
precision_rna   = |markers_rna   ∩ DB| / |markers_rna|
precision_atac  = |markers_atac  ∩ DB| / |markers_atac|
precision_inter = |markers_inter ∩ DB| / |markers_inter|

recall_rna   = |markers_rna   ∩ DB| / |DB|
recall_atac  = |markers_atac  ∩ DB| / |DB|
recall_union = |markers_union ∩ DB| / |DB|
```

Handle empty sets as follows:

```text
If |markers_rna| = 0, precision_rna = NA, not 0.
If |markers_atac| = 0, precision_atac = NA, not 0.
If |markers_inter| = 0, precision_inter = NA, not 0.
If |DB| = 0, exclude the cell type for that database.
```

Primary summaries:

```text
mean_precision_rna
mean_precision_atac
mean_precision_intersection
median_precision_rna
median_precision_atac
median_precision_intersection

mean_recall_rna
mean_recall_atac
mean_recall_union
median_recall_rna
median_recall_atac
median_recall_union
```

Compute summaries separately for:

```text
CellMarker 2.0
PanglaoDB
```

Also compute a paired per-cell-type comparison:

```text
precision_intersection - precision_rna
precision_rna - precision_atac
recall_union - recall_rna
recall_rna - recall_atac
```

Optional statistical tests:

```text
Wilcoxon signed-rank test across mapped cell types for paired metric differences.
Bootstrap 95% confidence intervals over cell types.
```

Required evaluation outputs:

```text
artifacts/evaluation/evaluation_per_cell_type.csv
artifacts/evaluation/evaluation_summary_by_database.csv
artifacts/evaluation/hypothesis_ordering_report.json
artifacts/evaluation/sensitivity_summary.csv
```

---

## 13. Required figures

### 13.1 Mean precision bar plot

```text
artifacts/figures/mean_precision_by_method_cellmarker2.png
artifacts/figures/mean_precision_by_method_panglaodb.png
```

Methods shown:

```text
RNA
ATAC
Intersection
```

### 13.2 Mean recall bar plot

```text
artifacts/figures/mean_recall_by_method_cellmarker2.png
artifacts/figures/mean_recall_by_method_panglaodb.png
```

Methods shown:

```text
RNA
ATAC
Union
```

### 13.3 Venn diagrams

Generate either:

1. one Venn diagram per mapped major cell type; or
2. a multi-panel Venn summary for the top 6 major cell types by cell count.

```text
artifacts/figures/venn_<cell_type>.png
artifacts/figures/venn_major_cell_types_panel.png
```

### 13.4 RNA/ATAC overlap heatmap

Rows: cell types.

Columns:

```text
n_rna
n_atac
n_intersection
n_union
jaccard
n_atac_only_known_cellmarker2
n_atac_only_known_panglaodb
```

Output:

```text
artifacts/figures/rna_atac_marker_overlap_heatmap.png
```

### 13.5 ATAC-only known marker analysis

Output:

```text
artifacts/figures/atac_only_known_marker_counts.png
artifacts/analysis/atac_only_known_markers.csv
artifacts/analysis/atac_only_marker_report.md
```

The ATAC-only report should answer:

1. Which ATAC-only genes are known markers in CellMarker 2.0?
2. Which ATAC-only genes are known markers in PanglaoDB?
3. Which cell types have the most known ATAC-only markers?
4. Are these genes plausible regulatory or lineage markers rather than generic housekeeping genes?

---

## 14. Sensitivity analyses

Run sensitivity analyses only after the primary run finishes.

Pre-registered grid:

```yaml
sensitivity_grid:
  top_n: [25, 50, 100]
  return_thresh: [0.01, 0.05]
  logfc_threshold: [0.10, 0.25]
```

For each setting, export:

```text
artifacts/sensitivity/<setting_id>/evaluation_summary_by_database.csv
artifacts/sensitivity/<setting_id>/hypothesis_ordering_report.json
```

The Reporter should summarize whether the qualitative ordering is:

```text
consistent across most settings
observed only under permissive settings
not observed
mixed across databases
```

Do not select the best-looking setting as the main result. The primary result is always:

```text
top_n = 50
return_thresh = 0.05
logfc_threshold = 0.25
min_pct = 0.10
```

---

## 15. AgentCo-Op prompts

### 15.1 Global planner prompt

```text
You are AgentCo-Op's Planner. Compile a minimal but sufficient multi-agent
workflow for this task. The user has provided GitHub URLs for Seurat and Signac,
a paired human heart multiome dataset, two marker databases, and a target
precision/recall evaluation. Do not solve the task with a single monolithic
script. You must create separate execution nodes for:

1. RNA Marker Agent using Seurat FindAllMarkers on the Gene Expression assay.
2. ATAC Marker Agent using Signac GeneActivity from ATAC fragments, followed by
   Seurat FindAllMarkers on the gene activity assay.
3. Ensemble/Evaluation Agent for marker set operations and database evaluation.

Plan requirements:
- Run link preflight before computation.
- Build or select Docker sandboxes for R, Seurat, Signac, and required
  Bioconductor annotation packages.
- Use the MA7 sample by default unless links fail; use MA5 donor as backup only
  if needed and record the substitution.
- Verify that the H5 file contains both Gene Expression and Peaks assays.
- Verify barcode overlap between matrix barcodes and metadata labels.
- Verify or create a tabix index for the ATAC fragment file.
- Evaluate CellMarker 2.0 and PanglaoDB separately.
- Report precision and recall honestly; do not tune parameters to force the
  expected ordering.

Return a structured plan with expected artifacts for every step.
```

### 15.2 Link preflight agent prompt

```text
You are the Link Preflight Agent. Check every required URL before analysis.
For each URL, determine whether it is reachable, whether it redirects, and
whether it appears to be an HTML page or binary downloadable file. For binary
files, it is sufficient to confirm that the server returns OK even if the file
cannot be rendered in text. Write link_preflight_report.json with one record per
URL. If a required link fails, return a repair recommendation and do not allow
execution to proceed.
```

### 15.3 Repo/tool inspection agent prompt

```text
You are the Tool Inspection Agent. Inspect the Seurat and Signac GitHub
repositories and official tutorials. Extract installation requirements,
required R packages, relevant functions, and expected input formats. For Seurat,
focus on Read10X_h5, CreateSeuratObject, NormalizeData, and FindAllMarkers. For
Signac, focus on CreateChromatinAssay, GeneActivity, hg38 annotations,
fragment-file requirements, and integration with Seurat objects. Return a
machine-readable tool manifest for the Docker Sandbox Compiler.
```

### 15.4 Docker sandbox compiler prompt

```text
You are the Docker Sandbox Compiler. Build a reproducible R/Bioconductor
container that can run Seurat and Signac on a 10x multiome H5 file and ATAC
fragments. Include system dependencies for HDF5, curl, SSL, XML, tabix, samtools,
and Bioconductor annotation packages for hg38. Produce Dockerfile, build command,
run command, package lock information, and a smoke test that loads Seurat,
Signac, EnsDb.Hsapiens.v86, and BSgenome.Hsapiens.UCSC.hg38.
```

### 15.5 Data inspector prompt

```text
You are the Data Inspector Agent. Load the GSE270788 MA7 H5 file and metadata.
Confirm that Read10X_h5 returns Gene Expression and Peaks assays. Identify the
metadata barcode column, sample column, and cell-type label column. Match matrix
barcodes to metadata labels using exact matching first and documented barcode
normalization only if needed. Require at least 80% barcode overlap. Export a
barcode match report and matched cell metadata table. If assay names or metadata
columns differ from expectation, repair by schema inference and document the
choice.
```

### 15.6 RNA Marker Agent prompt

```text
You are the RNA Marker Agent. Use only the Gene Expression assay and the
provided cell-type labels. Do not use ATAC marker results. Run Seurat
FindAllMarkers for each cell type against all other cells using the registered
parameters. Keep only positive markers with adjusted p-value below
return_thresh. Export all markers and top-N markers per cell type with columns:
cell_type, gene, p_val, avg_log2FC, pct.1, pct.2, p_val_adj, rank, agent,
sample_id, parameters_hash. Save the Seurat object and a parameter JSON file.
```

### 15.7 ATAC Marker Agent prompt

```text
You are the ATAC Marker Agent. Use only the Peaks assay, ATAC fragment file, and
provided cell-type labels. Do not use RNA marker results. Create a Signac
ChromatinAssay with hg38 annotation and fragments. Use Signac::GeneActivity to
create a gene activity matrix by counting fragments over gene bodies and the
2 kb upstream promoter region. Normalize the gene activity assay and run Seurat
FindAllMarkers with the same DE parameters used by the RNA agent. Export all
markers and top-N markers per cell type with the required schema. If the fragment
index is missing, ask the Manager to run the fragment-index repair gate. If
GeneActivity fails, return a structured failure with the exact error; do not
silently switch to a peak-overlap approximation.
```

### 15.8 Ensemble/Evaluation Agent prompt

```text
You are the Ensemble and Evaluation Agent. Combine RNA and ATAC top-N marker
sets per cell type. Compute intersection, union, RNA-only, and ATAC-only sets.
Load CellMarker 2.0 and PanglaoDB, filter to human heart markers, normalize gene
symbols, and create a manual-reviewable cell-type mapping table. Evaluate only
cell types that have non-empty mapped database markers. Compute precision and
recall per cell type and database. Report mean and median summaries separately
for CellMarker 2.0 and PanglaoDB. Check whether the target ordering holds, but
never modify the primary parameters to force it. Generate all requested tables,
plots, and the ATAC-only known-marker analysis.
```

### 15.9 Evaluator prompt

```text
You are the Evaluator. Check the completed plan and artifacts. Required success
conditions:

- link_preflight_report.json exists and shows all required links as usable;
- Dockerfile and wrapper scripts exist;
- RNA and ATAC marker CSV files exist and have required columns;
- marker set intersection and union files exist;
- CellMarker 2.0 and PanglaoDB filtered marker lists exist;
- cell-type mapping table exists;
- per-cell-type and summary evaluation tables exist for both databases;
- precision and recall figures exist;
- Venn/overlap heatmap and ATAC-only analysis exist;
- final report explains whether the expected ordering holds.

Route to REPLAN only for missing required artifacts, failed marker generation,
barcode mismatch below threshold, missing database mapping, or silent parameter
changes. Route to REPORT if the artifacts exist and the results are honestly
reported, even if the expected ordering does not hold.
```

---

## 16. Expected AgentCo-Op outputs

Final artifact tree:

```text
case_study_2_human_heart/
  executed_plan.json
  final_report.md
  final_notebook.ipynb
  link_preflight_report.json
  input_checksums.sha256

  docker/
    Dockerfile
    build.sh
    run_rna_agent.sh
    run_atac_agent.sh
    session_info_rna.txt
    session_info_atac.txt

  scripts/
    R/common_io.R
    R/run_rna_marker_agent.R
    R/run_atac_marker_agent.R
    R/run_database_agent.R
    R/run_ensemble_evaluation.R
    R/plot_results.R

  data/
    raw/
      gse270788/
      databases/
    processed/
      gse270788/

  artifacts/
    preprocessing/
      h5_schema_report.json
      barcode_match_report.json
      cell_metadata_matched.csv
      fragment_index_report.json

    rna_agent/
      rna_markers_all.csv
      rna_markers_topN.csv
      rna_marker_params.json
      rna_seurat_object.rds
      rna_agent_log.txt

    atac_agent/
      atac_gene_activity_markers_all.csv
      atac_gene_activity_markers_topN.csv
      atac_marker_params.json
      atac_gene_activity_seurat_object.rds
      atac_agent_log.txt

    ensemble/
      marker_sets_by_cell_type.json
      marker_sets_by_cell_type.csv
      overlap_summary_by_cell_type.csv
      atac_only_markers_by_cell_type.csv
      rna_only_markers_by_cell_type.csv

    databases/
      cellmarker2_human_heart_markers.csv
      panglaodb_hs_heart_markers.csv
      db_filter_log.json
      db_marker_sets.json
      cell_type_mapping.csv

    evaluation/
      evaluation_per_cell_type.csv
      evaluation_summary_by_database.csv
      hypothesis_ordering_report.json
      sensitivity_summary.csv

    figures/
      mean_precision_by_method_cellmarker2.png
      mean_precision_by_method_panglaodb.png
      mean_recall_by_method_cellmarker2.png
      mean_recall_by_method_panglaodb.png
      venn_major_cell_types_panel.png
      rna_atac_marker_overlap_heatmap.png
      atac_only_known_marker_counts.png
```

---

## 17. Baselines and ablations

### 17.1 Required baselines

1. **RNA-only Seurat baseline**
   - Use `precision_rna` and `recall_rna`.
2. **ATAC-only Signac baseline**
   - Use `precision_atac` and `recall_atac`.
3. **Naive random marker baseline**
   - For each cell type, sample the same number of genes as the RNA marker set from expressed genes.
   - Compute precision and recall against the same DB markers.
4. **Highly expressed RNA baseline**
   - For each cell type, rank genes by average expression only, without DE.

### 17.2 Required ablations

1. **No collaboration:** report RNA and ATAC metrics without intersection/union.
2. **No independent modality isolation:** allow ATAC to see RNA markers and show that this violates the case-study design; do not use as main result.
3. **No database mapping gate:** demonstrate that failing to map labels inflates or invalidates metrics.
4. **No dynamic repair:** disable fragment-index repair and record whether Signac fails.

---

## 18. Interpretation guide

### 18.1 Desired positive result

The strongest result would be:

```text
CellMarker 2.0:
  precision_intersection > precision_rna > precision_atac
  recall_union > recall_rna > recall_atac

PanglaoDB:
  precision_intersection > precision_rna > precision_atac
  recall_union > recall_rna > recall_atac
```

This would support the claim that AgentCo-Op can synthesize a useful collaborative workflow where two specialized tool agents produce complementary evidence and a high-level ensemble agent improves precision/recall tradeoffs.

### 18.2 Mixed but still useful result

A mixed result is still informative. Examples:

```text
precision_intersection > precision_rna for CellMarker but not PanglaoDB
recall_union > recall_rna for PanglaoDB but not CellMarker
ATAC-only markers contain known markers for fibroblasts and endothelial cells but not cardiomyocytes
```

This would show that the framework can execute the collaborative workflow, while the scientific hypothesis depends on database coverage, cell-type mapping, and ATAC gene-activity quality.

### 18.3 Negative result

A negative result should be reported directly. Possible reasons:

1. ATAC gene activity is sparser and noisier than RNA expression.
2. Marker databases are RNA-centric and may undervalue chromatin-only evidence.
3. CellMarker and PanglaoDB have incomplete heart-specific marker coverage for some labels.
4. Disease-state samples may contain activated or remodeled cell states whose markers differ from canonical healthy-heart markers.
5. Top-N truncation may penalize union recall if known markers rank below the cutoff.

A negative result does not invalidate AgentCo-Op if the workflow is reproducible, modular, and auditable. It simply weakens the biological hypothesis and should motivate a refined case-study analysis.

---

## 19. Known risks and mitigations

### Risk 1: CellMarker 2.0 download instability

Mitigation:

- Use the official CellMarker 2.0 download page as the primary source.
- Try the all-marker xlsx link first.
- If the binary download times out, retry with `curl -L --retry 5 --retry-delay 10`.
- If it still fails, stop and report the failure; do not silently substitute old CellMarker files in the primary analysis.

### Risk 2: PanglaoDB marker file availability

Mitigation:

- Use the stable `PanglaoDB_markers_27_Mar_2020.tsv.gz` URL.
- Cache the file and checksum it.
- If unavailable, stop the PanglaoDB branch and still run CellMarker 2.0, with the missing PanglaoDB result explicitly marked as incomplete.

### Risk 3: Metadata label-column ambiguity

Mitigation:

- Use schema inference plus a logged column selection.
- Generate `cell_type_column_candidates.json`.
- Require user review only if multiple plausible label columns produce substantially different cell-type counts.

### Risk 4: Fragment file lacks tabix index

Mitigation:

- Generate `.tbi` with `tabix -p bed`.
- If indexing fails, sort and bgzip the fragments, then index.
- Record the repaired path in `fragment_index_report.json`.

### Risk 5: Runtime and memory

Mitigation:

- Start with MA7 pilot.
- Use `max_cells_per_type` only in a resource-limited debug run; primary results should use all cells passing filters.
- If full GeneActivity is too slow, persist the gene activity matrix to RDS and reuse it across sensitivity runs.

### Risk 6: Expected ordering does not hold

Mitigation:

- Report the result honestly.
- Run the pre-registered sensitivity grid.
- Analyze whether the issue arises from database coverage, cell-type mapping, or ATAC marker sparsity.

---

## 20. Link verification log for the experiment design

The following links were checked while preparing this document. Binary links may return an OK status even though they cannot be rendered as text in a browser-like tool; that is sufficient for preflight, but the actual AgentCo-Op runner must still perform its own `curl`-level verification and checksum logging.

| Resource | Link | Verified design status |
|---|---|---|
| GSE270788 GEO series | https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE270788 | Public GEO page lists paired RNA/ATAC multiome design, 46 samples, `GSE270788_RAW.tar`, and `GSE270788_metadata.csv.gz`. |
| MA7 GEX sample | https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM8352050 | Public GEO page lists `GSM8352050_MA7_filtered_feature_bc_matrix.h5`. |
| MA7 GEX direct file | https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSM8352050&file=GSM8352050_MA7_filtered_feature_bc_matrix.h5&format=file | Direct binary endpoint returned OK during web preflight. |
| MA7 ATAC sample | https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM8352073 | Public GEO page lists `GSM8352073_MA7_atac_fragments.tsv.gz`. |
| MA7 ATAC direct file | https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSM8352073&file=GSM8352073_MA7_atac_fragments.tsv.gz&format=file | Direct binary endpoint returned OK during web preflight. |
| GSE270788 metadata | https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSE270788&file=GSE270788_metadata.csv.gz&format=file | Direct binary endpoint returned OK during web preflight. |
| Backup MA5 GEX sample | https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM8352048 | Public GEO page lists donor condition and the MA5 filtered feature-barcode H5 file. |
| Backup MA5 ATAC sample | https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM8352071 | Public GEO page lists donor condition and the MA5 ATAC fragment file. |
| CellMarker 2.0 download page | https://bio-bigdata.hrbmu.edu.cn/CellMarker/CellMarker_download.html | Official CellMarker 2.0 download page lists all, human, mouse, and single-cell marker xlsx files. |
| CellMarker 2.0 all-marker xlsx | https://bio-bigdata.hrbmu.edu.cn/CellMarker/CellMarker_download_files/file/Cell_marker_All.xlsx | Direct binary endpoint returned OK during web preflight. |
| PanglaoDB marker file | https://panglaodb.se/markers/PanglaoDB_markers_27_Mar_2020.tsv.gz | Direct binary endpoint returned OK during web preflight. |
| Seurat GitHub | https://github.com/satijalab/seurat | Public repository reachable. |
| Seurat guided tutorial | https://satijalab.org/seurat/articles/pbmc3k_tutorial | Public tutorial reachable and documents `FindAllMarkers()`. |
| Signac GitHub | https://github.com/stuart-lab/signac | Public repository reachable. |
| Signac PBMC scATAC tutorial | https://stuartlab.org/signac/articles/pbmc_vignette | Public tutorial reachable and documents `GeneActivity()`. |
| Signac 10x multiome tutorial | https://stuartlab.org/signac/articles/pbmc_multiomic | Public tutorial reachable and documents `Read10X_h5()`, `CreateChromatinAssay()`, and fragment use. |

---

## 21. Minimal success criteria

This case study is successful if AgentCo-Op produces:

1. a reproducible Docker sandbox for Seurat and Signac;
2. separate RNA and ATAC execution nodes with typed input/output contracts;
3. successful marker discovery from RNA and ATAC gene activity;
4. marker intersection and union per cell type;
5. CellMarker 2.0 and PanglaoDB evaluation run separately;
6. precision and recall tables and plots;
7. ATAC-only known-marker analysis;
8. an executed plan and artifact manifest; and
9. an honest final report explaining whether the expected precision/recall ordering holds.

The case study is not considered successful if the workflow is implemented as a single unstructured script with no explicit agent/tool boundaries, even if the final metrics are computed.

---

## 22. References and primary resources

- GSE270788 GEO series: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE270788
- MA7 GEX sample GSM8352050: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM8352050
- MA7 ATAC sample GSM8352073: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM8352073
- CellMarker 2.0 download page: https://bio-bigdata.hrbmu.edu.cn/CellMarker/CellMarker_download.html
- PanglaoDB marker file: https://panglaodb.se/markers/PanglaoDB_markers_27_Mar_2020.tsv.gz
- Seurat GitHub: https://github.com/satijalab/seurat
- Seurat guided tutorial: https://satijalab.org/seurat/articles/pbmc3k_tutorial
- Signac GitHub: https://github.com/stuart-lab/signac
- Signac PBMC scATAC tutorial: https://stuartlab.org/signac/articles/pbmc_vignette
- Signac 10x multiome tutorial: https://stuartlab.org/signac/articles/pbmc_multiomic
