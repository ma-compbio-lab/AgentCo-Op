# Case Study 2 Lite: AgentCo-Op Collaboration Between Seurat and Signac on 10x PBMC Multiome

## 1. Purpose of this case study

This case study is designed to demonstrate that **AgentCo-Op can synthesize a practical multi-agent bioinformatics workflow from external tool repositories, dataset links, marker databases, and a natural-language task description**. The workflow should not be implemented as a monolithic script. Instead, AgentCo-Op should compile and execute a workflow in which:

1. A **Seurat RNA marker agent** identifies marker genes from the RNA modality.
2. A **Signac ATAC marker agent** independently identifies marker genes from the ATAC modality by first converting chromatin accessibility into gene-level activity scores.
3. An **ensemble/evaluation agent** combines the two marker sets by intersection and union, then evaluates the resulting marker lists against independent marker gene databases.

The scientific hypothesis is:

- The **intersection** of RNA-derived and ATAC-derived marker genes should be more precise than either modality alone because genes supported by both expression and accessibility are more likely to be canonical cell-type markers.
- The **union** of RNA-derived and ATAC-derived marker genes should have higher recall than either modality alone because the two modalities may recover complementary marker evidence.

The engineering hypothesis is:

- A single tool, either Seurat or Signac alone, cannot complete the full task because each covers only one modality-specific marker discovery path. AgentCo-Op should be able to configure sandboxed tool-agent nodes from GitHub URLs, route modality-specific subtasks to them, aggregate their outputs, and evaluate the collaborative result.

The target inequalities are:

```text
precision_intersection > precision_rna > precision_atac
recall_union > recall_rna > recall_atac
```

These are **expected results, not constraints to force**. The final report must state whether the inequalities hold under the primary protocol and under sensitivity analyses. AgentCo-Op must not tune thresholds solely to make the target ordering true.

---

## 2. Why this is the lite version

The previous version of Case Study 2 used a larger and more biologically specialized human heart multiome dataset. This lite version uses the public **10x Genomics PBMC Multiome dataset** that is also used in the official Signac multiome tutorial. It is smaller, better documented, easier to reproduce, and has well-known immune cell marker databases.

The chosen dataset is:

**10x Genomics PBMC Multiome dataset: PBMC from a Healthy Donor, Granulocytes Removed Through Cell Sorting, 10k**

Important properties:

- It contains paired scRNA-seq and scATAC-seq from the same nuclei/cells.
- It is a human blood/PBMC dataset.
- The official Signac multiome tutorial uses this dataset and provides direct download commands for the filtered feature-barcode matrix, ATAC fragments file, and ATAC fragments index.
- The 10x dataset page reports an estimated 11,909 cells/nuclei, 108,377 ATAC peaks, 36,601 RNA features in the h5 file, and paired ATAC and gene-expression libraries.

This makes it suitable for a reproducible AgentCo-Op demonstration because the task is complex enough to require multiple specialists, but small enough to run on a workstation or a moderate cloud instance.

---

## 3. Inputs to AgentCo-Op

AgentCo-Op receives the following inputs.

### 3.1 User task

```text
Given a single-cell multiomics dataset with paired scRNA-seq and scATAC-seq from the same cells, use two separate agents to identify cell-type marker genes from each modality independently. Then evaluate whether the intersection of the two marker sets achieves higher precision, and the union achieves higher recall, compared to either agent alone, using marker gene databases as ground truth.

Use the 10x Genomics PBMC Multiome dataset. Use Seurat for RNA marker discovery and Signac GeneActivity plus Seurat FindAllMarkers for ATAC-derived gene-level marker discovery. Use CellMarker 2.0, PanglaoDB, and the combined union of both databases as ground-truth marker resources. Produce all required tables, plots, marker lists, database lists, cell-type mapping files, execution logs, and a final report.
```

### 3.2 Tool repositories

```yaml
tool_repositories:
  - name: Seurat
    github_url: https://github.com/satijalab/seurat
    role: RNA single-cell analysis and marker discovery
    documentation:
      - https://satijalab.org/seurat/articles/pbmc3k_tutorial.html
      - https://satijalab.org/seurat/articles/seurat5_wilcox_DETest.html

  - name: Signac
    github_url: https://github.com/stuart-lab/signac
    role: single-cell chromatin analysis, chromatin assay construction, gene activity scoring
    documentation:
      - https://stuartlab.org/signac/articles/pbmc_multiomic
      - https://stuartlab.org/signac/articles/pbmc_vignette.html
      - https://stuartlab.org/signac/reference/geneactivity
```

AgentCo-Op should use these URLs to register two external tool nodes. The workflow can install stable CRAN/Bioconductor releases for reproducibility, but the registry entry must retain the GitHub URL, detected package name, installed version, and, if installed directly from GitHub, the commit SHA.

### 3.3 Dataset URLs

```yaml
dataset:
  name: 10x Genomics PBMC Multiome, granulocytes removed, 10k
  source_page: https://www.10xgenomics.com/datasets/pbmc-from-a-healthy-donor-granulocytes-removed-through-cell-sorting-10-k-1-standard-1-0-0
  files:
    filtered_feature_bc_matrix_h5:
      url: https://cf.10xgenomics.com/samples/cell-arc/1.0.0/pbmc_granulocyte_sorted_10k/pbmc_granulocyte_sorted_10k_filtered_feature_bc_matrix.h5
      local_name: pbmc_granulocyte_sorted_10k_filtered_feature_bc_matrix.h5
      purpose: contains both Gene Expression and Peaks matrices

    atac_fragments:
      url: https://cf.10xgenomics.com/samples/cell-arc/1.0.0/pbmc_granulocyte_sorted_10k/pbmc_granulocyte_sorted_10k_atac_fragments.tsv.gz
      local_name: pbmc_granulocyte_sorted_10k_atac_fragments.tsv.gz
      purpose: required for Signac GeneActivity computation

    atac_fragments_index:
      url: https://cf.10xgenomics.com/samples/cell-arc/1.0.0/pbmc_granulocyte_sorted_10k/pbmc_granulocyte_sorted_10k_atac_fragments.tsv.gz.tbi
      local_name: pbmc_granulocyte_sorted_10k_atac_fragments.tsv.gz.tbi
      purpose: required by Signac to query the fragments file
```

### 3.4 PBMC reference for cell-type annotation

```yaml
cell_type_reference:
  name: Hao et al. PBMC multimodal reference
  url: https://zenodo.org/records/7779017/files/pbmc_multimodal_2023.rds
  local_name: pbmc_multimodal_2023.rds
  required_metadata_column: celltype.l2
  purpose: transfer fine-grained PBMC cell type labels to the query multiome dataset
```

The workflow should use the `celltype.l2` level from the reference because it includes fine-grained immune cell labels such as CD4 Naive, CD4 TCM, CD8 TEM, CD14 Mono, CD16 Mono, B naive, and related PBMC populations.

### 3.5 Marker gene databases

```yaml
marker_databases:
  - name: CellMarker 2.0
    primary_url: http://www.bio-bigdata.center/CellMarker_download_files/file/Cell_marker_All.xlsx
    fallback_download_page: https://xteam.xbio.top/CellMarker/download.jsp
    local_name: Cell_marker_All.xlsx
    filters:
      species: Human
      tissue_terms:
        - Blood
        - Peripheral blood
        - PBMC
        - Immune system

  - name: PanglaoDB
    primary_url: https://panglaodb.se/markers/PanglaoDB_markers_27_Mar_2020.tsv.gz
    local_name: PanglaoDB_markers_27_Mar_2020.tsv.gz
    filters:
      species_contains: Hs
      organ_terms:
        - Blood
        - Immune system
```

For evaluation mode (c), AgentCo-Op should merge the CellMarker and PanglaoDB marker lists by cell type after mapping, then take the union of marker genes for each mapped cell type.

---

## 4. Link and data preflight requirements

Before building or executing the analysis, AgentCo-Op must perform a preflight stage.

### 4.1 Required checks

For every input URL, AgentCo-Op should record:

- URL.
- Download status.
- HTTP status code if available.
- Local file path.
- File size.
- Whether the file is binary, gzipped, xlsx, or RDS.
- A short validation result.

For binary URLs, a text-only browser may be unable to render the content. That should not be treated as failure if a direct command-line download succeeds and the file passes local validation.

### 4.2 Expected validation commands

```bash
mkdir -p data/raw logs

wget -c -O data/raw/pbmc_granulocyte_sorted_10k_filtered_feature_bc_matrix.h5 \
  https://cf.10xgenomics.com/samples/cell-arc/1.0.0/pbmc_granulocyte_sorted_10k/pbmc_granulocyte_sorted_10k_filtered_feature_bc_matrix.h5

wget -c -O data/raw/pbmc_granulocyte_sorted_10k_atac_fragments.tsv.gz \
  https://cf.10xgenomics.com/samples/cell-arc/1.0.0/pbmc_granulocyte_sorted_10k/pbmc_granulocyte_sorted_10k_atac_fragments.tsv.gz

wget -c -O data/raw/pbmc_granulocyte_sorted_10k_atac_fragments.tsv.gz.tbi \
  https://cf.10xgenomics.com/samples/cell-arc/1.0.0/pbmc_granulocyte_sorted_10k/pbmc_granulocyte_sorted_10k_atac_fragments.tsv.gz.tbi

wget -c -O data/raw/pbmc_multimodal_2023.rds \
  https://zenodo.org/records/7779017/files/pbmc_multimodal_2023.rds

wget -c -O data/raw/Cell_marker_All.xlsx \
  http://www.bio-bigdata.center/CellMarker_download_files/file/Cell_marker_All.xlsx

wget -c -O data/raw/PanglaoDB_markers_27_Mar_2020.tsv.gz \
  https://panglaodb.se/markers/PanglaoDB_markers_27_Mar_2020.tsv.gz
```

Local validation:

```bash
file data/raw/* > logs/file_types.txt
ls -lh data/raw/* > logs/file_sizes.txt

gzip -t data/raw/pbmc_granulocyte_sorted_10k_atac_fragments.tsv.gz
gzip -t data/raw/PanglaoDB_markers_27_Mar_2020.tsv.gz

test -f data/raw/pbmc_granulocyte_sorted_10k_atac_fragments.tsv.gz.tbi
```

### 4.3 Preflight output

AgentCo-Op should write:

```text
artifacts/preflight/download_manifest.json
artifacts/preflight/download_manifest.tsv
artifacts/preflight/link_check_report.md
logs/download.log
```

If any primary database link fails, AgentCo-Op should attempt documented fallback sources and record the fallback in the manifest. For CellMarker, the fallback is the official download page. For PanglaoDB, the direct marker TSV URL is primary; if it fails, AgentCo-Op should record failure and stop the database evaluation rather than silently substitute a different marker database.

---

## 5. Desired compiled workflow topology

AgentCo-Op should compile the following simple but modular workflow:

```text
Planner
  -> Data Preflight Agent
  -> Multiome Preprocessing and Annotation Agent
  -> parallel:
       RNA Marker Agent      [Seurat]
       ATAC Marker Agent     [Signac + Seurat]
  -> Ensemble Set Operation Agent
  -> Database Evaluation Agent
  -> Visualization Agent
  -> Evaluator Gate
  -> Reporter
```

The main contribution is not that the graph is complex. The contribution is that AgentCo-Op chooses a **minimal workflow with clear tool boundaries**:

- Seurat is responsible for RNA marker discovery.
- Signac is responsible for ATAC-to-gene-activity conversion and ATAC-derived marker discovery.
- The ensemble/evaluation stage is responsible for set operations, marker database mapping, and quantitative evaluation.

The two marker agents should run independently after receiving the shared annotated Seurat object. The final result should require both agents; a single tool node should not be able to produce the full cross-modality precision/recall evaluation.

---

## 6. Sandbox and tool-agent design

### 6.1 Docker image strategy

The lite case study should use one reproducible R image template and instantiate separate containers for each agent node. This provides execution isolation while avoiding unnecessary build complexity.

Recommended base image:

```dockerfile
FROM rocker/r-ver:4.4.2

ENV DEBIAN_FRONTEND=noninteractive
ENV R_REMOTES_NO_ERRORS_FROM_WARNINGS=true

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    wget \
    curl \
    ca-certificates \
    libcurl4-openssl-dev \
    libssl-dev \
    libxml2-dev \
    libhdf5-dev \
    libpng-dev \
    libjpeg-dev \
    libtiff-dev \
    libfontconfig1-dev \
    libfreetype6-dev \
    libharfbuzz-dev \
    libfribidi-dev \
    zlib1g-dev \
    tabix \
    && rm -rf /var/lib/apt/lists/*

RUN R -e "install.packages(c('remotes','pak','Matrix','dplyr','tidyr','stringr','readr','readxl','openxlsx','jsonlite','yaml','ggplot2','patchwork','pheatmap','VennDiagram','ComplexUpset','future'), repos='https://cloud.r-project.org')"

RUN R -e "install.packages('BiocManager', repos='https://cloud.r-project.org'); \
          BiocManager::install(c('GenomeInfoDb','GenomicRanges','EnsDb.Hsapiens.v86','BSgenome.Hsapiens.UCSC.hg38'), ask=FALSE, update=FALSE)"

# Preferred practical installation path for reproducibility.
RUN R -e "install.packages(c('Seurat','SeuratObject'), repos='https://cloud.r-project.org')"
RUN R -e "install.packages('Signac', repos='https://cloud.r-project.org')"

WORKDIR /workspace
```

Optional GitHub installation mode:

```dockerfile
# Use only if the experiment explicitly requires GitHub HEAD or a pinned SHA.
RUN R -e "pak::pkg_install('satijalab/seurat')"
RUN R -e "pak::pkg_install('stuart-lab/signac')"
```

Recommended behavior:

- Record the GitHub URLs in the Agent Registry.
- Install stable release packages unless `install_from_github=true` is passed.
- Always record installed package versions using `sessionInfo()` and `packageVersion()`.

### 6.2 Agent registry entries

```yaml
agents:
  - id: seurat_rna_marker_agent
    role: RNA marker gene finder
    tool_source:
      github_url: https://github.com/satijalab/seurat
      package: Seurat
    sandbox:
      image: agentcoop-r-singlecell:case2-lite
      working_dir: /workspace
    input_schema:
      annotated_object_rds: path
      cell_type_column: string
      marker_params: object
    output_schema:
      rna_markers_tsv: path
      rna_marker_sets_json: path
      rna_marker_qc_json: path
      session_info_txt: path
    capabilities:
      - Load Seurat object
      - Set RNA assay
      - Normalize RNA data if needed
      - Run FindAllMarkers with Wilcoxon rank-sum test
      - Export significant marker genes per cell type

  - id: signac_atac_marker_agent
    role: ATAC-derived marker gene finder
    tool_source:
      github_url: https://github.com/stuart-lab/signac
      package: Signac
    sandbox:
      image: agentcoop-r-singlecell:case2-lite
      working_dir: /workspace
    input_schema:
      annotated_object_rds: path
      fragments_tsv_gz: path
      fragments_tbi: path
      cell_type_column: string
      marker_params: object
    output_schema:
      atac_markers_tsv: path
      atac_marker_sets_json: path
      gene_activity_summary_json: path
      session_info_txt: path
    capabilities:
      - Load Seurat object with ChromatinAssay
      - Validate fragment file and tabix index
      - Compute Signac GeneActivity scores
      - Add gene activity assay
      - Run Seurat FindAllMarkers on gene activity matrix
      - Export significant marker genes per cell type

  - id: ensemble_evaluation_agent
    role: marker set ensemble and database evaluator
    sandbox:
      image: agentcoop-r-singlecell:case2-lite
    input_schema:
      rna_markers_tsv: path
      atac_markers_tsv: path
      cellmarker_xlsx: path
      panglaodb_tsv_gz: path
      celltype_mapping_yaml: path
    output_schema:
      per_celltype_metrics_tsv: path
      summary_metrics_tsv: path
      marker_lists_dir: path
      figures_dir: path
      evaluation_report_md: path
    capabilities:
      - Compute RNA/ATAC/intersection/union marker sets
      - Parse CellMarker and PanglaoDB
      - Apply cell-type name mapping
      - Compute precision and recall
      - Generate plots and final analysis tables
```

### 6.3 Filesystem layout

```text
case_study_2_lite/
  data/
    raw/
    processed/
  docker/
    Dockerfile.r_singlecell
  scripts/
    00_download.sh
    01_preprocess_annotate.R
    02_rna_marker_agent.R
    03_atac_marker_agent.R
    04_ensemble_evaluate.R
    05_visualize.R
  configs/
    agent_registry.yaml
    marker_params.yaml
    celltype_mapping.yaml
  artifacts/
    preflight/
    annotation/
    rna_agent/
    atac_agent/
    ensemble/
    evaluation/
    figures/
    marker_lists/
    reports/
  logs/
  notebooks/
```

---

## 7. Step-by-step experimental protocol

## Step 1: Download and validate input data

The Data Preflight Agent downloads all raw files and writes a manifest.

Required files:

```text
data/raw/pbmc_granulocyte_sorted_10k_filtered_feature_bc_matrix.h5
data/raw/pbmc_granulocyte_sorted_10k_atac_fragments.tsv.gz
data/raw/pbmc_granulocyte_sorted_10k_atac_fragments.tsv.gz.tbi
data/raw/pbmc_multimodal_2023.rds
data/raw/Cell_marker_All.xlsx
data/raw/PanglaoDB_markers_27_Mar_2020.tsv.gz
```

Validation gate:

- The h5 file must be readable by `Seurat::Read10X_h5()`.
- The h5 file must contain both `Gene Expression` and `Peaks` entries.
- The fragments file must pass `gzip -t`.
- The fragment index `.tbi` must exist next to the fragments file.
- The PBMC reference RDS must be readable by `readRDS()`.
- The reference object must contain `celltype.l2` in metadata.
- CellMarker must be readable by `readxl::read_excel()`.
- PanglaoDB must be readable by `readr::read_tsv()`.

Expected output:

```text
artifacts/preflight/download_manifest.json
artifacts/preflight/data_validation_report.md
```

---

## Step 2: Multiome preprocessing and cell-type annotation

This step follows the official Signac multiome tutorial structure.

### 2.1 Load the multiome h5 file

R script skeleton:

```r
library(Signac)
library(Seurat)
library(EnsDb.Hsapiens.v86)
library(BSgenome.Hsapiens.UCSC.hg38)
library(GenomeInfoDb)
library(future)

options(future.globals.maxSize = 20 * 1024^3)

h5_path <- "data/raw/pbmc_granulocyte_sorted_10k_filtered_feature_bc_matrix.h5"
frag_path <- "data/raw/pbmc_granulocyte_sorted_10k_atac_fragments.tsv.gz"
reference_path <- "data/raw/pbmc_multimodal_2023.rds"

counts <- Read10X_h5(h5_path)
stopifnot("Gene Expression" %in% names(counts))
stopifnot("Peaks" %in% names(counts))

annotation <- GetGRangesFromEnsDb(ensdb = EnsDb.Hsapiens.v86)
seqlevels(annotation) <- paste0("chr", seqlevels(annotation))
genome(annotation) <- "hg38"

pbmc <- CreateSeuratObject(
  counts = counts$`Gene Expression`,
  assay = "RNA"
)

pbmc[["ATAC"]] <- CreateChromatinAssay(
  counts = counts$Peaks,
  sep = c(":", "-"),
  fragments = frag_path,
  annotation = annotation
)
```

### 2.2 Quality control

Use the Signac multiome tutorial filters as the primary QC rule:

```r
DefaultAssay(pbmc) <- "ATAC"
pbmc <- NucleosomeSignal(pbmc)
pbmc <- TSSEnrichment(pbmc)

pbmc <- subset(
  pbmc,
  subset = nCount_ATAC < 100000 &
           nCount_RNA < 25000 &
           nCount_ATAC > 1800 &
           nCount_RNA > 1000 &
           nucleosome_signal < 2 &
           TSS.enrichment > 1
)
```

Write QC outputs:

```text
artifacts/annotation/qc_summary.tsv
artifacts/annotation/qc_violin.png
artifacts/annotation/qc_density_atac_tss.png
```

Validation gate:

- Report number of cells before and after QC.
- The post-QC cell number should be close to the tutorial expectation. If it is far outside the expected range, continue only after logging the reason.

### 2.3 RNA preprocessing for annotation

```r
DefaultAssay(pbmc) <- "RNA"
pbmc <- SCTransform(pbmc, verbose = FALSE)
pbmc <- RunPCA(pbmc, verbose = FALSE)
```

### 2.4 ATAC preprocessing for annotation

```r
DefaultAssay(pbmc) <- "ATAC"
pbmc <- FindTopFeatures(pbmc, min.cutoff = 5)
pbmc <- RunTFIDF(pbmc)
pbmc <- RunSVD(pbmc)
```

### 2.5 Transfer cell-type labels from Hao et al. PBMC reference

```r
reference <- readRDS(reference_path)
reference <- UpdateSeuratObject(reference)

if (!"celltype.l2" %in% colnames(reference@meta.data)) {
  stop("Reference object does not contain required metadata column: celltype.l2")
}

DefaultAssay(pbmc) <- "SCT"

transfer_anchors <- FindTransferAnchors(
  reference = reference,
  query = pbmc,
  normalization.method = "SCT",
  reference.reduction = "spca",
  recompute.residuals = FALSE,
  dims = 1:50
)

predictions <- TransferData(
  anchorset = transfer_anchors,
  refdata = reference$celltype.l2,
  weight.reduction = pbmc[["pca"]],
  dims = 1:50
)

pbmc <- AddMetaData(pbmc, metadata = predictions)
pbmc$predicted.celltype.l2 <- pbmc$predicted.id
```

Required annotation outputs:

```text
artifacts/annotation/pbmc_annotated.rds
artifacts/annotation/celltype_counts.tsv
artifacts/annotation/annotation_confidence_summary.tsv
artifacts/annotation/annotation_umap.png
artifacts/annotation/session_info.txt
```

Validation gate:

- `predicted.celltype.l2` must exist in `pbmc@meta.data`.
- At least 5 cell types should have enough cells for marker analysis.
- Each evaluated cell type should have at least `min_cells_per_type` cells. Recommended default: 30.
- If a cell type has fewer than 30 cells, exclude it from marker discovery and report the exclusion.

---

## Step 3: Agent 1 — Seurat RNA marker gene finder

### 3.1 Agent role

The RNA Marker Agent is a Seurat execution node. It receives the annotated Seurat object and identifies significant marker genes from the RNA modality only.

### 3.2 Input

```yaml
input:
  annotated_object_rds: artifacts/annotation/pbmc_annotated.rds
  cell_type_column: predicted.celltype.l2
  assay: RNA
  marker_params:
    test.use: wilcox
    only.pos: true
    min.pct: 0.10
    logfc.threshold: 0.25
    return.thresh: 0.05
    min_cells_per_type: 30
```

### 3.3 Method

Primary method:

```r
library(Seurat)
library(dplyr)
library(jsonlite)

pbmc <- readRDS("artifacts/annotation/pbmc_annotated.rds")
cell_type_column <- "predicted.celltype.l2"

stopifnot(cell_type_column %in% colnames(pbmc@meta.data))
Idents(pbmc) <- pbmc[[cell_type_column]][, 1]

# Remove small cell-type groups before DE.
cell_counts <- table(Idents(pbmc))
valid_types <- names(cell_counts[cell_counts >= 30])
pbmc <- subset(pbmc, idents = valid_types)
Idents(pbmc) <- pbmc[[cell_type_column]][, 1]

DefaultAssay(pbmc) <- "RNA"
pbmc <- NormalizeData(pbmc, verbose = FALSE)
pbmc <- FindVariableFeatures(pbmc, verbose = FALSE)

rna_markers <- FindAllMarkers(
  pbmc,
  assay = "RNA",
  test.use = "wilcox",
  only.pos = TRUE,
  min.pct = 0.10,
  logfc.threshold = 0.25,
  return.thresh = 0.05
)

rna_markers <- rna_markers %>%
  filter(p_val_adj < 0.05) %>%
  arrange(cluster, p_val_adj, desc(avg_log2FC))

write.table(
  rna_markers,
  file = "artifacts/rna_agent/rna_markers.tsv",
  sep = "\t",
  quote = FALSE,
  row.names = FALSE
)

marker_sets <- split(rna_markers$gene, rna_markers$cluster)
write_json(marker_sets, "artifacts/rna_agent/rna_marker_sets.json", pretty = TRUE)

writeLines(capture.output(sessionInfo()), "artifacts/rna_agent/session_info.txt")
```

### 3.4 Output schema

```text
artifacts/rna_agent/rna_markers.tsv
artifacts/rna_agent/rna_marker_sets.json
artifacts/rna_agent/rna_marker_counts.tsv
artifacts/rna_agent/top_rna_markers_by_celltype.tsv
artifacts/rna_agent/session_info.txt
```

`rna_markers.tsv` must contain at least these columns:

```text
gene
cluster
p_val
p_val_adj
avg_log2FC
pct.1
pct.2
```

Validation gate:

- Marker genes must be gene symbols, not Ensembl IDs unless all downstream databases are also converted.
- `cluster` must match values from `predicted.celltype.l2`.
- Only markers with `p_val_adj < 0.05` should be returned for the primary evaluation.
- Empty marker sets are allowed but must be recorded as empty, not missing.

---

## Step 4: Agent 2 — Signac ATAC-derived marker gene finder

### 4.1 Agent role

The ATAC Marker Agent is a Signac execution node. It receives the annotated Seurat object and the ATAC fragments file, computes gene activity scores, and identifies cell-type marker genes from the gene activity matrix.

The important methodological requirement is:

```text
Use Signac::GeneActivity(). Do not use ClosestFeature() as a substitute.
```

`ClosestFeature()` maps peaks to nearby genes. That is not the requested method. The requested method is gene-level activity scoring by counting fragments over gene bodies and promoter regions.

### 4.2 Input

```yaml
input:
  annotated_object_rds: artifacts/annotation/pbmc_annotated.rds
  fragments_tsv_gz: data/raw/pbmc_granulocyte_sorted_10k_atac_fragments.tsv.gz
  fragments_tbi: data/raw/pbmc_granulocyte_sorted_10k_atac_fragments.tsv.gz.tbi
  cell_type_column: predicted.celltype.l2
  marker_params:
    gene_activity:
      extend.upstream: 2000
      extend.downstream: 0
      biotypes: protein_coding
    find_all_markers:
      test.use: wilcox
      only.pos: true
      min.pct: 0.10
      logfc.threshold: 0.25
      return.thresh: 0.05
      min_cells_per_type: 30
```

### 4.3 Method

R script skeleton:

```r
library(Signac)
library(Seurat)
library(dplyr)
library(jsonlite)

pbmc <- readRDS("artifacts/annotation/pbmc_annotated.rds")
frag_path <- "data/raw/pbmc_granulocyte_sorted_10k_atac_fragments.tsv.gz"
cell_type_column <- "predicted.celltype.l2"

stopifnot(file.exists(frag_path))
stopifnot(file.exists(paste0(frag_path, ".tbi")))
stopifnot("ATAC" %in% names(pbmc@assays))
stopifnot(cell_type_column %in% colnames(pbmc@meta.data))

# Ensure the fragment object points to the current sandbox path.
DefaultAssay(pbmc) <- "ATAC"
Fragments(pbmc[["ATAC"]]) <- CreateFragmentObject(
  path = frag_path,
  cells = colnames(pbmc),
  validate.fragments = TRUE
)

Idents(pbmc) <- pbmc[[cell_type_column]][, 1]
cell_counts <- table(Idents(pbmc))
valid_types <- names(cell_counts[cell_counts >= 30])
pbmc <- subset(pbmc, idents = valid_types)
Idents(pbmc) <- pbmc[[cell_type_column]][, 1]

DefaultAssay(pbmc) <- "ATAC"

gene.activities <- GeneActivity(
  pbmc,
  assay = "ATAC",
  extend.upstream = 2000,
  extend.downstream = 0,
  biotypes = "protein_coding"
)

pbmc[["ACTIVITY"]] <- CreateAssayObject(counts = gene.activities)
DefaultAssay(pbmc) <- "ACTIVITY"
pbmc <- NormalizeData(pbmc, assay = "ACTIVITY", normalization.method = "LogNormalize", verbose = FALSE)

atac_markers <- FindAllMarkers(
  pbmc,
  assay = "ACTIVITY",
  test.use = "wilcox",
  only.pos = TRUE,
  min.pct = 0.10,
  logfc.threshold = 0.25,
  return.thresh = 0.05
)

atac_markers <- atac_markers %>%
  filter(p_val_adj < 0.05) %>%
  arrange(cluster, p_val_adj, desc(avg_log2FC))

write.table(
  atac_markers,
  file = "artifacts/atac_agent/atac_markers.tsv",
  sep = "\t",
  quote = FALSE,
  row.names = FALSE
)

marker_sets <- split(atac_markers$gene, atac_markers$cluster)
write_json(marker_sets, "artifacts/atac_agent/atac_marker_sets.json", pretty = TRUE)

activity_summary <- list(
  n_genes_in_activity_matrix = nrow(gene.activities),
  n_cells = ncol(gene.activities),
  extend_upstream = 2000,
  extend_downstream = 0,
  biotypes = "protein_coding"
)
write_json(activity_summary, "artifacts/atac_agent/gene_activity_summary.json", pretty = TRUE)

writeLines(capture.output(sessionInfo()), "artifacts/atac_agent/session_info.txt")
```

### 4.4 Output schema

```text
artifacts/atac_agent/atac_markers.tsv
artifacts/atac_agent/atac_marker_sets.json
artifacts/atac_agent/atac_marker_counts.tsv
artifacts/atac_agent/top_atac_markers_by_celltype.tsv
artifacts/atac_agent/gene_activity_summary.json
artifacts/atac_agent/session_info.txt
```

`atac_markers.tsv` must contain gene names, not peak IDs. Required columns:

```text
gene
cluster
p_val
p_val_adj
avg_log2FC
pct.1
pct.2
```

Validation gate:

- The output must be gene-level marker genes.
- The output must not contain genomic peak IDs such as `chr1:1000-2000`.
- The ATAC agent must prove that `GeneActivity()` was run by writing `gene_activity_summary.json`.
- If GeneActivity fails due to fragment/index path issues, AgentCo-Op should repair the fragment path or re-create the fragment object. It should not silently switch to `ClosestFeature()`.

---

## Step 5: Ensemble set operations

For each cell type shared between RNA and ATAC marker outputs:

```text
markers_rna   = significant RNA marker genes from Agent 1
markers_atac  = significant ATAC-derived marker genes from Agent 2
markers_inter = markers_rna ∩ markers_atac
markers_union = markers_rna ∪ markers_atac
atac_only     = markers_atac - markers_rna
rna_only      = markers_rna - markers_atac
```

Required outputs:

```text
artifacts/ensemble/marker_sets_by_celltype.json
artifacts/ensemble/marker_set_sizes.tsv
artifacts/ensemble/rna_atac_overlap_by_celltype.tsv
artifacts/ensemble/atac_only_markers_by_celltype.tsv
artifacts/ensemble/rna_only_markers_by_celltype.tsv
```

The overlap table should contain:

```text
cell_type
n_rna
n_atac
n_intersection
n_union
jaccard_intersection_union
n_rna_only
n_atac_only
```

Validation gate:

- Set operations should be case-insensitive for matching but preserve official gene-symbol capitalization in output.
- Gene aliases should not be automatically merged in the primary analysis unless the database parser explicitly standardizes aliases. Alias handling can be included as a sensitivity analysis.

---

## Step 6: Marker database processing

### 6.1 CellMarker 2.0 processing

Primary filter:

```text
species = Human
tissue_type or tissue field contains one of: Blood, Peripheral blood, PBMC, Immune system
```

Because CellMarker column names may vary across mirrors or versions, the parser should inspect columns and normalize them into the following schema:

```text
database
species
tissue
cell_type
gene_symbol
source_or_reference
```

Expected parser behavior:

1. Read the Excel file.
2. Convert column names to lowercase snake case.
3. Identify the species column.
4. Identify the tissue or tissue-type column.
5. Identify the cell-name/cell-type column.
6. Identify the marker/gene-symbol column.
7. Filter to human blood/PBMC-related rows.
8. Deduplicate `(cell_type, gene_symbol)` pairs.
9. Export the filtered marker list.

Output:

```text
artifacts/evaluation/db_cellmarker_filtered.tsv
artifacts/evaluation/db_cellmarker_summary.tsv
```

### 6.2 PanglaoDB processing

Primary filter:

```text
species contains Hs
organ contains Blood or Immune system
```

Expected PanglaoDB columns usually include:

```text
species
official gene symbol
cell type
organ
canonical marker
sensitivity_human
specificity_human
```

Parser behavior:

1. Read the gzipped TSV.
2. Normalize column names.
3. Filter rows where species contains `Hs`.
4. Filter rows where organ contains `Blood` or `Immune system`.
5. Use `official gene symbol` as `gene_symbol`.
6. Use `cell type` as `cell_type`.
7. Deduplicate `(cell_type, gene_symbol)` pairs.
8. Export the filtered marker list.

Output:

```text
artifacts/evaluation/db_panglaodb_filtered.tsv
artifacts/evaluation/db_panglaodb_summary.tsv
```

### 6.3 Combined database

For each mapped query cell type, combine the CellMarker and PanglaoDB marker sets:

```text
DB_combined(cell_type) = DB_CellMarker(cell_type) ∪ DB_PanglaoDB(cell_type)
```

Output:

```text
artifacts/evaluation/db_combined_filtered.tsv
artifacts/evaluation/db_combined_summary.tsv
```

---

## Step 7: Manual cell-type name mapping

Marker database labels will not exactly match Hao PBMC `celltype.l2` labels. A manual mapping file is required. AgentCo-Op should create a first draft and then allow manual review before final evaluation.

### 7.1 Mapping file format

`configs/celltype_mapping.yaml`:

```yaml
CD14 Mono:
  canonical_eval_name: Monocyte
  db_aliases:
    - Monocyte
    - CD14+ monocyte
    - Classical monocyte
  include: true
  rationale: CD14 Mono is a classical monocyte population in PBMC references.

CD16 Mono:
  canonical_eval_name: Monocyte
  db_aliases:
    - Monocyte
    - CD16+ monocyte
    - Non-classical monocyte
  include: true
  rationale: CD16 Mono corresponds to non-classical monocytes; if subtype markers are absent, evaluate against generic monocyte markers.

B naive:
  canonical_eval_name: B cell
  db_aliases:
    - B cell
    - Naive B cell
  include: true
  rationale: Naive B cells are a B-cell subtype; use subtype if present and generic B cell otherwise.

B memory:
  canonical_eval_name: B cell
  db_aliases:
    - B cell
    - Memory B cell
  include: true
  rationale: Memory B cells are a B-cell subtype.

Plasmablast:
  canonical_eval_name: Plasmablast
  db_aliases:
    - Plasmablast
    - Plasma cell
  include: true
  rationale: Plasmablast markers are often listed directly or under plasma-cell related names.

CD4 Naive:
  canonical_eval_name: CD4 T cell
  db_aliases:
    - T cell
    - CD4 T cell
    - CD4+ T cell
    - Naive T cell
    - Naive CD4 T cell
  include: true
  rationale: CD4 Naive is a CD4 T-cell subtype.

CD4 TCM:
  canonical_eval_name: CD4 T cell
  db_aliases:
    - T cell
    - CD4 T cell
    - CD4+ T cell
    - Central memory T cell
  include: true
  rationale: CD4 TCM is a central memory CD4 T-cell population.

CD4 TEM:
  canonical_eval_name: CD4 T cell
  db_aliases:
    - T cell
    - CD4 T cell
    - CD4+ T cell
    - Effector memory T cell
  include: true
  rationale: CD4 TEM is an effector memory CD4 T-cell population.

CD8 Naive:
  canonical_eval_name: CD8 T cell
  db_aliases:
    - T cell
    - CD8 T cell
    - CD8+ T cell
    - Naive T cell
    - Naive CD8 T cell
  include: true
  rationale: CD8 Naive is a CD8 T-cell subtype.

CD8 TCM:
  canonical_eval_name: CD8 T cell
  db_aliases:
    - T cell
    - CD8 T cell
    - CD8+ T cell
    - Central memory T cell
  include: true
  rationale: CD8 TCM is a central memory CD8 T-cell population.

CD8 TEM:
  canonical_eval_name: CD8 T cell
  db_aliases:
    - T cell
    - CD8 T cell
    - CD8+ T cell
    - Effector memory T cell
  include: true
  rationale: CD8 TEM is an effector memory CD8 T-cell population.

Treg:
  canonical_eval_name: Regulatory T cell
  db_aliases:
    - Regulatory T cell
    - Treg
    - T regulatory cell
  include: true
  rationale: Treg is a regulatory T-cell population.

NK:
  canonical_eval_name: Natural killer cell
  db_aliases:
    - Natural killer cell
    - NK cell
  include: true
  rationale: NK is a natural killer cell population.

MAIT:
  canonical_eval_name: T cell
  db_aliases:
    - MAIT cell
    - Mucosal-associated invariant T cell
    - T cell
  include: true
  rationale: Evaluate against MAIT-specific markers if present; otherwise generic T-cell markers.

cDC1:
  canonical_eval_name: Dendritic cell
  db_aliases:
    - Dendritic cell
    - Conventional dendritic cell
    - cDC1
  include: true
  rationale: cDC1 is a conventional dendritic-cell subtype.

cDC2:
  canonical_eval_name: Dendritic cell
  db_aliases:
    - Dendritic cell
    - Conventional dendritic cell
    - cDC2
  include: true
  rationale: cDC2 is a conventional dendritic-cell subtype.

pDC:
  canonical_eval_name: Plasmacytoid dendritic cell
  db_aliases:
    - Plasmacytoid dendritic cell
    - pDC
    - Dendritic cell
  include: true
  rationale: pDC is a plasmacytoid dendritic-cell population.
```

### 7.2 Mapping rules

The Database Evaluation Agent should:

1. Normalize names by lowercasing, stripping punctuation, and removing redundant spaces.
2. Match each query cell type to the union of all database rows whose normalized cell type matches any alias.
3. Exclude query cell types with no database entries after alias matching.
4. Write the exact marker genes used as ground truth for each query cell type and database mode.

Output:

```text
artifacts/evaluation/celltype_mapping_used.yaml
artifacts/evaluation/celltype_mapping_report.md
artifacts/evaluation/evaluated_celltypes.tsv
artifacts/evaluation/excluded_celltypes.tsv
```

---

## Step 8: Precision and recall evaluation

For each evaluated cell type and each database mode, compute:

```text
precision(method) = |predicted_markers(method) ∩ DB| / |predicted_markers(method)|
recall(method)    = |predicted_markers(method) ∩ DB| / |DB|
```

Methods:

```text
RNA
ATAC
Intersection
Union
```

Database modes:

```text
CellMarker
PanglaoDB
Combined
```

Metrics per cell type:

```text
cell_type
database_mode
n_db
n_rna
n_atac
n_intersection
n_union
precision_rna
precision_atac
precision_intersection
precision_union
recall_rna
recall_atac
recall_intersection
recall_union
```

The user-requested headline metrics are:

```text
precision_rna
precision_atac
precision_intersection
recall_rna
recall_atac
recall_union
```

The evaluation should also compute precision for union and recall for intersection for completeness, but these should not be the main claim.

### 8.1 Empty-set handling

- If a predicted marker set is empty, precision is `NA`, not zero.
- If a database marker set is empty after mapping, the cell type is excluded from that database mode.
- Mean and median should be computed over non-NA values only.
- The number of evaluated cell types must be reported for each database mode.

### 8.2 Summary statistics

Report:

```text
mean_precision_by_method_and_db
median_precision_by_method_and_db
mean_recall_by_method_and_db
median_recall_by_method_and_db
n_evaluated_celltypes_by_db
```

Required output files:

```text
artifacts/evaluation/per_celltype_precision_recall.tsv
artifacts/evaluation/summary_precision_recall.tsv
artifacts/evaluation/target_inequality_check.tsv
artifacts/evaluation/target_inequality_check.md
```

### 8.3 Statistical comparison

Recommended exploratory statistics:

- Paired Wilcoxon signed-rank test across cell types for:
  - `precision_intersection > precision_rna`
  - `precision_rna > precision_atac`
  - `recall_union > recall_rna`
  - `recall_rna > recall_atac`
- Bootstrap confidence intervals for the mean difference across cell types.

Output:

```text
artifacts/evaluation/paired_tests.tsv
artifacts/evaluation/bootstrap_ci.tsv
```

These tests should be treated as supportive analyses, not as the sole success criterion.

---

## Step 9: Required figures

### 9.1 Mean precision bar plot

Plot mean precision by method for each database mode.

Methods to show:

```text
RNA
ATAC
Intersection
Union
```

The main claim should highlight RNA, ATAC, and intersection.

Output:

```text
artifacts/figures/mean_precision_by_method.png
artifacts/figures/mean_precision_by_method.pdf
```

### 9.2 Mean recall bar plot

Plot mean recall by method for each database mode.

Methods to show:

```text
RNA
ATAC
Intersection
Union
```

The main claim should highlight RNA, ATAC, and union.

Output:

```text
artifacts/figures/mean_recall_by_method.png
artifacts/figures/mean_recall_by_method.pdf
```

### 9.3 Venn diagrams

Generate one Venn diagram per evaluated cell type showing RNA marker genes, ATAC marker genes, and their overlap.

Output:

```text
artifacts/figures/venn_by_celltype/<cell_type>_rna_atac_venn.png
```

If there are too many cell types, generate Venn diagrams for the top 8 most abundant cell types and write the rest as tables.

### 9.4 Overlap heatmap

Generate a heatmap of RNA/ATAC overlap statistics across cell types:

```text
rows: cell types
columns:
  n_rna
  n_atac
  n_intersection
  n_union
  jaccard_intersection_union
```

Output:

```text
artifacts/figures/rna_atac_overlap_heatmap.png
artifacts/figures/rna_atac_overlap_heatmap.pdf
```

### 9.5 ATAC-only known marker analysis

For each cell type:

```text
atac_only_known = (markers_atac - markers_rna) ∩ DB
```

Report whether ATAC identifies known markers that RNA marker discovery misses.

Required output:

```text
artifacts/evaluation/atac_only_known_markers.tsv
artifacts/reports/atac_only_marker_analysis.md
```

This is important because it provides a positive interpretation of ATAC even if ATAC alone has lower precision than RNA.

---

## 10. Sensitivity analyses

The primary analysis uses all significant markers with `p_val_adj < 0.05`, `only.pos = TRUE`, `min.pct = 0.10`, and `logfc.threshold = 0.25`.

However, marker-list size strongly affects precision and recall. Therefore, AgentCo-Op should run a predefined sensitivity grid, not an adaptive search:

```yaml
sensitivity_grid:
  - name: primary_all_significant
    p_val_adj: 0.05
    min_pct: 0.10
    logfc_threshold: 0.25
    top_k_per_celltype: null

  - name: top25
    p_val_adj: 0.05
    min_pct: 0.10
    logfc_threshold: 0.25
    top_k_per_celltype: 25

  - name: top50
    p_val_adj: 0.05
    min_pct: 0.10
    logfc_threshold: 0.25
    top_k_per_celltype: 50

  - name: top100
    p_val_adj: 0.05
    min_pct: 0.10
    logfc_threshold: 0.25
    top_k_per_celltype: 100
```

The final report should show whether the target inequalities hold consistently across this grid.

Output:

```text
artifacts/evaluation/sensitivity_summary.tsv
artifacts/figures/sensitivity_precision.png
artifacts/figures/sensitivity_recall.png
```

The sensitivity grid must be declared before execution. It must not be changed after looking at results.

---

## 11. AgentCo-Op prompts

This section gives prompts that should make the workflow execute as intended.

### 11.1 Main user prompt to AgentCo-Op

```text
You are AgentCo-Op, a task-conditioned multi-agent workflow compiler and executor.

Compile and execute a minimal multi-agent workflow for the following bioinformatics task.

Task:
Given a paired single-cell multiomics dataset containing scRNA-seq and scATAC-seq from the same cells, identify cell-type marker genes from RNA and ATAC independently using separate tool-agent nodes. Then compute the intersection and union of the marker sets for each cell type, and evaluate precision and recall against independent marker gene databases.

Dataset:
Use the 10x Genomics PBMC Multiome dataset, granulocytes removed, 10k. Download these files:
1. filtered feature barcode matrix h5:
   https://cf.10xgenomics.com/samples/cell-arc/1.0.0/pbmc_granulocyte_sorted_10k/pbmc_granulocyte_sorted_10k_filtered_feature_bc_matrix.h5
2. ATAC fragments:
   https://cf.10xgenomics.com/samples/cell-arc/1.0.0/pbmc_granulocyte_sorted_10k/pbmc_granulocyte_sorted_10k_atac_fragments.tsv.gz
3. ATAC fragments index:
   https://cf.10xgenomics.com/samples/cell-arc/1.0.0/pbmc_granulocyte_sorted_10k/pbmc_granulocyte_sorted_10k_atac_fragments.tsv.gz.tbi

Reference for annotation:
Use the Hao et al. PBMC multimodal reference RDS:
https://zenodo.org/records/7779017/files/pbmc_multimodal_2023.rds
Use the reference metadata column celltype.l2 to annotate query cells.

Tool repositories:
1. Seurat: https://github.com/satijalab/seurat
2. Signac: https://github.com/stuart-lab/signac

Tool requirements:
- Register Seurat as an RNA marker gene finder agent.
- Register Signac as an ATAC-derived marker gene finder agent.
- Build or reuse Docker/R sandboxes that can run Seurat and Signac.
- Keep the two marker-finding agents independent after shared preprocessing and annotation.
- Do not collapse the workflow into one monolithic script.

Annotation workflow:
Follow the Signac PBMC multiome tutorial. Load the h5 file with Read10X_h5, create a Seurat object with RNA and ATAC assays, create the ATAC ChromatinAssay using the fragments file and hg38 annotations, perform QC, process RNA with SCTransform and PCA, process ATAC with TF-IDF and LSI, and transfer labels from the Hao et al. PBMC reference using FindTransferAnchors and TransferData. Store labels in predicted.celltype.l2.

RNA Marker Agent:
Use Seurat FindAllMarkers on the RNA assay. Use cell identities from predicted.celltype.l2. Use Wilcoxon rank-sum test, only positive markers, min.pct = 0.10, logfc.threshold = 0.25, return.thresh = 0.05. Only return markers with adjusted p-value < 0.05. Output gene name, cluster/cell type, p_val, p_val_adj, avg_log2FC, pct.1, pct.2.

ATAC Marker Agent:
Use Signac GeneActivity, not ClosestFeature. Compute gene activity scores using the ATAC fragments file with 2kb upstream promoter extension and gene body counts. Add the gene activity matrix as a new assay, normalize it, and run Seurat FindAllMarkers with the same parameters as the RNA agent. Output gene names, not peak IDs, with the same output schema as the RNA agent.

Ensemble:
For each cell type, compute:
- markers_intersection = RNA markers intersect ATAC markers
- markers_union = RNA markers union ATAC markers
- ATAC-only markers = ATAC markers minus RNA markers

Ground-truth marker databases:
1. CellMarker 2.0:
   http://www.bio-bigdata.center/CellMarker_download_files/file/Cell_marker_All.xlsx
   Filter to Human and Blood/Peripheral blood/PBMC/Immune system entries.
2. PanglaoDB:
   https://panglaodb.se/markers/PanglaoDB_markers_27_Mar_2020.tsv.gz
   Filter to species containing Hs and organ containing Blood or Immune system.
3. Combined database:
   For each mapped cell type, take the union of CellMarker and PanglaoDB marker genes.

Cell-type mapping:
Create a manual cell-type mapping file from predicted.celltype.l2 labels to database cell-type names. Examples: CD14 Mono to Monocyte, CD16 Mono to Monocyte, B naive to B cell, CD4 Naive to CD4 T cell, CD8 TEM to CD8 T cell, NK to Natural killer cell. Exclude cell types with no database entries after mapping. Save the final mapping used.

Evaluation:
For each evaluated cell type and each database mode, compute precision and recall for RNA, ATAC, intersection, and union. Report mean and median across cell types. Check whether precision_intersection > precision_rna > precision_atac and recall_union > recall_rna > recall_atac. These target inequalities are hypotheses, not constraints. Do not tune parameters after seeing results to force them to hold.

Required outputs:
1. Per-cell-type precision/recall table for each method and database mode.
2. Summary mean/median precision and recall table.
3. Bar plot of mean precision by method.
4. Bar plot of mean recall by method.
5. Venn diagrams or overlap summaries for RNA vs ATAC markers per cell type.
6. Heatmap of RNA/ATAC overlap statistics per cell type.
7. ATAC-only known marker analysis.
8. Used marker lists from CellMarker, PanglaoDB, and combined database.
9. Used marker lists from RNA and ATAC agents.
10. Cell-type name mapping file.
11. Docker build logs, package sessionInfo, and complete artifact manifest.
12. Final markdown report with conclusions and caveats.

Validation rules:
- Validate all links and local files before analysis.
- If GeneActivity fails, repair fragment path/index problems. Do not replace GeneActivity with ClosestFeature.
- If database parsing fails, inspect column names and adapt the parser while logging the change.
- If a cell type has no database mapping, exclude it and report it.
- If target inequalities do not hold, report the failure honestly and include sensitivity analyses using top 25, top 50, and top 100 markers per cell type.
```

### 11.2 RNA Marker Agent prompt

```text
You are the RNA Marker Agent. You are a Seurat specialist responsible only for RNA-based cell-type marker discovery.

Input:
- An annotated Seurat object at artifacts/annotation/pbmc_annotated.rds
- Cell identities stored in predicted.celltype.l2

Task:
Set the active assay to RNA, normalize RNA data if needed, and run Seurat FindAllMarkers using Wilcoxon rank-sum test with only.pos = TRUE, min.pct = 0.10, logfc.threshold = 0.25, return.thresh = 0.05. Keep only markers with p_val_adj < 0.05.

Output:
Write artifacts/rna_agent/rna_markers.tsv and artifacts/rna_agent/rna_marker_sets.json. The TSV must include gene, cluster, p_val, p_val_adj, avg_log2FC, pct.1, and pct.2. The marker sets JSON must map each cell type to a list of marker gene symbols.

Do not use ATAC data. Do not perform set operations. Do not evaluate against marker databases. Return only RNA marker outputs and QC information.
```

### 11.3 ATAC Marker Agent prompt

```text
You are the ATAC Marker Agent. You are a Signac specialist responsible only for ATAC-derived gene-level marker discovery.

Input:
- An annotated Seurat object at artifacts/annotation/pbmc_annotated.rds
- ATAC fragments file at data/raw/pbmc_granulocyte_sorted_10k_atac_fragments.tsv.gz
- ATAC fragments index at data/raw/pbmc_granulocyte_sorted_10k_atac_fragments.tsv.gz.tbi
- Cell identities stored in predicted.celltype.l2

Task:
Use Signac::GeneActivity() to compute gene-level activity scores from the ATAC fragments file. Use extend.upstream = 2000 and extend.downstream = 0. Add the gene activity matrix as a new Seurat assay named ACTIVITY, normalize it, and run Seurat FindAllMarkers with the same parameters as the RNA agent: Wilcoxon rank-sum test, only.pos = TRUE, min.pct = 0.10, logfc.threshold = 0.25, return.thresh = 0.05. Keep only markers with p_val_adj < 0.05.

Critical instruction:
Use GeneActivity, not ClosestFeature. The output must be gene symbols, not peak IDs.

Output:
Write artifacts/atac_agent/atac_markers.tsv, artifacts/atac_agent/atac_marker_sets.json, and artifacts/atac_agent/gene_activity_summary.json. The TSV must include gene, cluster, p_val, p_val_adj, avg_log2FC, pct.1, and pct.2.

Do not use RNA marker results. Do not perform set operations. Do not evaluate against marker databases. Return only ATAC-derived marker outputs and QC information.
```

### 11.4 Ensemble/Evaluation Agent prompt

```text
You are the Ensemble and Evaluation Agent. You receive independent marker outputs from the RNA Marker Agent and ATAC Marker Agent.

Input:
- artifacts/rna_agent/rna_markers.tsv
- artifacts/atac_agent/atac_markers.tsv
- CellMarker 2.0 xlsx
- PanglaoDB marker tsv.gz
- celltype_mapping.yaml

Task:
For every mapped cell type, compute RNA markers, ATAC markers, their intersection, their union, RNA-only markers, and ATAC-only markers. Parse CellMarker and PanglaoDB separately, filter them to human blood/PBMC/immune entries, apply the cell-type mapping aliases, and compute precision and recall for RNA, ATAC, intersection, and union. Repeat evaluation for CellMarker only, PanglaoDB only, and the combined union of both databases.

Output:
Write per-cell-type metrics, summary metrics, target inequality checks, database marker lists used, predicted marker lists used, ATAC-only known markers, and all required figures.

Do not change marker thresholds. Do not hide failed target inequalities. If a cell type cannot be mapped to the databases, exclude it and record why.
```

### 11.5 Evaluator prompt

```text
You are the Evaluator. Check whether the executed workflow satisfies the case-study contract.

Required checks:
1. The workflow used separate RNA and ATAC marker agents.
2. The RNA agent used Seurat FindAllMarkers on RNA gene expression.
3. The ATAC agent used Signac GeneActivity followed by Seurat FindAllMarkers.
4. ATAC output is gene symbols, not peak IDs.
5. Cell type labels come from transferred Hao PBMC reference celltype.l2 annotations.
6. CellMarker, PanglaoDB, and combined database modes were evaluated separately.
7. Per-cell-type and summary precision/recall tables exist.
8. Mean precision and recall plots exist.
9. RNA/ATAC overlap and ATAC-only known marker analyses exist.
10. The final report states whether the target inequalities hold.
11. Docker logs, package versions, and artifact manifest exist.

Route to REPORT only if the core outputs exist and the methods match the instructions. Route to REPLAN if GeneActivity was not used, if RNA and ATAC marker discovery were merged into one undifferentiated script without agent separation, if database evaluation is missing, or if marker outputs are peak IDs rather than gene symbols.
```

---

## 12. Expected final output package

AgentCo-Op should return a directory with this structure:

```text
case_study_2_lite_results/
  artifact_manifest.json
  final_report.md
  final_report.html

  preflight/
    download_manifest.json
    download_manifest.tsv
    link_check_report.md
    data_validation_report.md

  annotation/
    pbmc_annotated.rds
    celltype_counts.tsv
    annotation_confidence_summary.tsv
    qc_summary.tsv
    qc_violin.png
    annotation_umap.png
    session_info.txt

  rna_agent/
    rna_markers.tsv
    rna_marker_sets.json
    rna_marker_counts.tsv
    top_rna_markers_by_celltype.tsv
    session_info.txt

  atac_agent/
    atac_markers.tsv
    atac_marker_sets.json
    atac_marker_counts.tsv
    top_atac_markers_by_celltype.tsv
    gene_activity_summary.json
    session_info.txt

  ensemble/
    marker_sets_by_celltype.json
    marker_set_sizes.tsv
    rna_atac_overlap_by_celltype.tsv
    atac_only_markers_by_celltype.tsv
    rna_only_markers_by_celltype.tsv

  evaluation/
    db_cellmarker_filtered.tsv
    db_panglaodb_filtered.tsv
    db_combined_filtered.tsv
    celltype_mapping_used.yaml
    celltype_mapping_report.md
    evaluated_celltypes.tsv
    excluded_celltypes.tsv
    per_celltype_precision_recall.tsv
    summary_precision_recall.tsv
    target_inequality_check.tsv
    paired_tests.tsv
    bootstrap_ci.tsv
    atac_only_known_markers.tsv
    sensitivity_summary.tsv

  figures/
    mean_precision_by_method.png
    mean_precision_by_method.pdf
    mean_recall_by_method.png
    mean_recall_by_method.pdf
    rna_atac_overlap_heatmap.png
    rna_atac_overlap_heatmap.pdf
    venn_by_celltype/

  marker_lists/
    predicted_rna_by_celltype/
    predicted_atac_by_celltype/
    predicted_intersection_by_celltype/
    predicted_union_by_celltype/
    db_cellmarker_by_celltype/
    db_panglaodb_by_celltype/
    db_combined_by_celltype/

  logs/
    docker_build.log
    download.log
    annotation.log
    rna_agent.log
    atac_agent.log
    evaluation.log

  notebooks/
    reproducible_workflow.Rmd
    reproducible_workflow.html
```

---

## 13. Final report template

The final report should contain the following sections.

### 13.1 Executive summary

State:

- Dataset used.
- Number of cells before and after QC.
- Number of predicted cell types.
- Number of cell types evaluated against each database mode.
- Whether the target precision and recall inequalities hold.

### 13.2 Workflow provenance

Report:

- Tool repositories and package versions.
- Docker image IDs.
- Input file paths and sizes.
- Agent registry entries.
- Execution status of each plan step.

### 13.3 Cell-type annotation summary

Report:

- Cell type counts.
- Annotation confidence summary.
- Any excluded cell types.

### 13.4 Marker discovery summary

Report:

- Number of RNA markers per cell type.
- Number of ATAC-derived markers per cell type.
- Overlap statistics.
- Most abundant/representative marker examples.

### 13.5 Database evaluation results

Report:

- Per-cell-type precision and recall table.
- Summary mean/median precision and recall.
- Separate results for CellMarker, PanglaoDB, and Combined.
- Target inequality check.
- Statistical comparison results.

### 13.6 ATAC-only marker analysis

Report:

- ATAC-only markers that are known database markers.
- Which cell types have useful ATAC-only evidence.
- Biological interpretation.

### 13.7 Sensitivity analysis

Report:

- Primary all-significant marker analysis.
- Top 25, top 50, top 100 sensitivity analyses.
- Whether conclusions are robust to marker-list size.

### 13.8 Caveats

Required caveats:

- Marker databases are incomplete and may use broader or different cell-type names than Hao PBMC `celltype.l2` labels.
- ATAC gene activity is an approximation from chromatin accessibility and does not directly measure expression.
- RNA and ATAC marker rankings are not perfectly comparable because they come from different modalities.
- The target inequalities are hypotheses; failure to observe them is an informative result, not an execution failure.

---

## 14. Success criteria

This case study is successful if AgentCo-Op demonstrates the following engineering and scientific capabilities.

### 14.1 Engineering success criteria

1. AgentCo-Op accepts tool GitHub URLs, dataset URLs, marker database URLs, and task text.
2. It compiles a workflow with separate Seurat and Signac execution agents.
3. It creates or reuses Docker/R sandboxes.
4. It validates downloads and input schemas.
5. It passes typed artifacts between agents rather than relying on unstructured chat.
6. It records package versions, execution logs, and artifact provenance.
7. It detects and repairs likely failures such as broken fragment paths, missing tabix index, inconsistent database columns, and unmapped cell-type names.

### 14.2 Scientific success criteria

1. RNA marker genes are produced per cell type using Seurat.
2. ATAC-derived marker genes are produced per cell type using Signac GeneActivity plus Seurat FindAllMarkers.
3. Intersection and union marker sets are computed per cell type.
4. Precision and recall are evaluated against CellMarker, PanglaoDB, and the combined database.
5. The final report honestly states whether the expected precision and recall ordering holds.
6. ATAC-only known markers are reported and interpreted.

---

## 15. Common failure modes and required repairs

### 15.1 Direct binary URL appears unavailable in a web renderer

Cause:

- The URL points to a binary file, and the web renderer cannot display it.

Repair:

- Use `wget` or `curl -L`.
- Validate file size and readability locally.
- Record that text rendering failed but command-line download succeeded.

### 15.2 Fragment file path breaks after loading the Seurat object

Cause:

- The saved Seurat object may contain an old fragment path.

Repair:

- Re-create the fragment object inside the ATAC agent sandbox:

```r
Fragments(pbmc[["ATAC"]]) <- CreateFragmentObject(
  path = frag_path,
  cells = colnames(pbmc),
  validate.fragments = TRUE
)
```

### 15.3 GeneActivity is slow or memory intensive

Cause:

- Gene activity quantifies many genes over many fragments.

Repair options:

1. Ensure enough memory. Recommended: at least 32 GB RAM.
2. Run full primary analysis if possible.
3. If memory fails, use a predeclared fallback: stratified downsample to 5,000 cells while preserving cell-type proportions, and clearly label all outputs as downsampled.
4. Do not replace GeneActivity with ClosestFeature.

### 15.4 CellMarker xlsx columns differ from expected names

Cause:

- Different CellMarker mirrors or versions may have different column names.

Repair:

- Inspect column names.
- Normalize them.
- Use heuristic matching for species, tissue, cell type, and gene marker columns.
- Log the resolved schema.

### 15.5 PanglaoDB contains broad cell-type names

Cause:

- PanglaoDB cell-type labels may not match Hao PBMC l2 labels exactly.

Repair:

- Use the manual mapping file.
- Evaluate subtype-specific matches when present.
- Fall back to broader parent types only if documented.

### 15.6 Target inequalities fail

Cause:

- Marker databases may be incomplete.
- ATAC gene activity may recover different marker classes.
- Marker-list size may affect precision/recall.

Repair:

- Do not tune adaptively.
- Report the failure.
- Run the predefined sensitivity grid.
- Analyze which cell types and database modes deviate from the expected pattern.

---

## 16. Minimal command-line shape for AgentCo-Op

The exact CLI depends on the AgentCo-Op implementation. A reasonable target interface is:

```bash
agentcoop run \
  --task prompts/case_study_2_lite_main_prompt.txt \
  --agent-registry configs/agent_registry.yaml \
  --data-root case_study_2_lite/data \
  --workdir case_study_2_lite \
  --output case_study_2_lite_results \
  --max-replans 2 \
  --enable-docker true \
  --enable-link-preflight true \
  --enable-artifact-trace true
```

The resulting workflow should be reproducible without requiring the LLM to remember hidden state. Every important decision should be encoded in config files, scripts, manifests, or the final report.

---

## 17. References and source links

Dataset and workflow:

- 10x Genomics PBMC Multiome dataset page: https://www.10xgenomics.com/datasets/pbmc-from-a-healthy-donor-granulocytes-removed-through-cell-sorting-10-k-1-standard-1-0-0
- Signac PBMC multiome tutorial: https://stuartlab.org/signac/articles/pbmc_multiomic
- Signac GeneActivity documentation: https://stuartlab.org/signac/reference/geneactivity
- Seurat GitHub: https://github.com/satijalab/seurat
- Seurat PBMC tutorial: https://satijalab.org/seurat/articles/pbmc3k_tutorial.html
- Signac GitHub: https://github.com/stuart-lab/signac

Reference annotation:

- Hao et al. PBMC multimodal reference RDS: https://zenodo.org/records/7779017/files/pbmc_multimodal_2023.rds

Marker databases:

- CellMarker 2.0 download page: https://xteam.xbio.top/CellMarker/download.jsp
- CellMarker 2.0 all-marker file: http://www.bio-bigdata.center/CellMarker_download_files/file/Cell_marker_All.xlsx
- PanglaoDB markers page: https://panglaodb.se/markers.html
- PanglaoDB marker TSV: https://panglaodb.se/markers/PanglaoDB_markers_27_Mar_2020.tsv.gz

Agentic workflow reference:

- TissueAgent manuscript design pattern: role-based agents, agent registry, evolving plan, external-agent collaboration, artifact provenance, evaluation, and targeted replanning.
