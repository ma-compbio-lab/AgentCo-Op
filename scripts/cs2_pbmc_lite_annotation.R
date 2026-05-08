#!/usr/bin/env Rscript
# CS2 Lite — preprocessing + cell-type annotation for the 10x PBMC
# multiome dataset (`pbmc_granulocyte_sorted_10k`). Produces the
# metadata CSV that the existing Seurat / Signac wrappers consume.
#
# This is data prep, not collaborative agent work — runs once per
# dataset inside the agentcoop-r-runtime image so AgentCo-Op's
# wrappers can stay unchanged.
#
# PRIMARY annotation path (per docs/experiments/case_study_2_lite.md §3.4 / §11.1):
#   Seurat label transfer from the Hao et al. PBMC multimodal
#   reference (`pbmc_multimodal_2023.rds` from Zenodo), using
#   `celltype.l2` labels.
#
# DOCUMENTED FALLBACK if the Hao RDS is unreadable (download failure,
# HTML error page, network-blocked Zenodo, …):
#   SingleR + celldex::MonacoImmuneData (Bioconductor-hosted bulk
#   RNA-seq immune reference, 29 cell types). The fallback is
#   prominently logged and the choice is recorded in the summary
#   JSON so downstream consumers can see which reference produced
#   the labels.
#
# Pipeline:
#   1. Read10X_h5 → Seurat object (RNA + ATAC ChromatinAssay)
#   2. Signac QC (NucleosomeSignal, TSSEnrichment, tutorial filters)
#   3. SCTransform + RunPCA on RNA (required for Hao-mode label transfer)
#   4. PRIMARY: FindTransferAnchors + TransferData (Hao reference)
#      FALLBACK: SingleR + celldex::MonacoImmuneData
#   5. Write metadata CSV {barcode, sample, cell_type, predicted_score}
#
# Invocation:
#   Rscript cs2_pbmc_lite_annotation.R <h5> <fragments> <hao_rds> <out_meta_csv_gz> [<sample_id>]

suppressPackageStartupMessages({
  library(Signac)
  library(Seurat)
  library(EnsDb.Hsapiens.v86)
  library(BSgenome.Hsapiens.UCSC.hg38)
  library(GenomeInfoDb)
  library(SingleR)
  library(celldex)
  library(SummarizedExperiment)
  library(SingleCellExperiment)
  library(data.table)
  library(jsonlite)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 4) {
  stop(paste(
    "usage: Rscript cs2_pbmc_lite_annotation.R",
    "<h5_path> <fragments_path> <hao_rds_path> <out_meta_csv_gz> [<sample_id>]"
  ))
}
h5_path     <- args[1]
frag_path   <- args[2]
hao_path    <- args[3]
out_meta    <- args[4]
sample_id   <- if (length(args) >= 5) args[5] else "PBMC10K"

options(future.globals.maxSize = 24 * 1024^3)

stopifnot(file.exists(h5_path))
stopifnot(file.exists(frag_path))
stopifnot(file.exists(paste0(frag_path, ".tbi")))

cat(sprintf("[%s] CS2 Lite annotation start\n", Sys.time()))
cat(sprintf("  h5:        %s\n", h5_path))
cat(sprintf("  frags:     %s\n", frag_path))
cat(sprintf("  hao ref:   %s\n", hao_path))
cat(sprintf("  out meta:  %s\n", out_meta))
cat(sprintf("  sample_id: %s\n", sample_id))

# ---------------------------------------------------------------------------
# 1. Load the multiome H5 + create the Seurat object (RNA + ATAC)
# ---------------------------------------------------------------------------

cat(sprintf("[%s] step 1: Read10X_h5\n", Sys.time()))
counts <- Read10X_h5(h5_path)
stopifnot("Gene Expression" %in% names(counts))
stopifnot("Peaks" %in% names(counts))
cat(sprintf("  GEX dim: %s; Peaks dim: %s\n",
            paste(dim(counts$`Gene Expression`), collapse = "x"),
            paste(dim(counts$Peaks), collapse = "x")))

annotation <- GetGRangesFromEnsDb(ensdb = EnsDb.Hsapiens.v86)
seqlevels(annotation) <- paste0("chr", seqlevels(annotation))
genome(annotation) <- "hg38"

pbmc <- CreateSeuratObject(counts = counts$`Gene Expression`, assay = "RNA")
pbmc[["ATAC"]] <- CreateChromatinAssay(
  counts     = counts$Peaks,
  sep        = c(":", "-"),
  fragments  = frag_path,
  annotation = annotation
)

# ---------------------------------------------------------------------------
# 2. QC (Signac multiome tutorial filters)
# ---------------------------------------------------------------------------

cat(sprintf("[%s] step 2: QC (NucleosomeSignal + TSSEnrichment)\n", Sys.time()))
DefaultAssay(pbmc) <- "ATAC"
pbmc <- NucleosomeSignal(pbmc)
pbmc <- TSSEnrichment(pbmc)
n_pre <- ncol(pbmc)
pbmc <- subset(
  pbmc,
  subset = nCount_ATAC < 100000 &
           nCount_RNA  < 25000 &
           nCount_ATAC > 1800 &
           nCount_RNA  > 1000 &
           nucleosome_signal < 2 &
           TSS.enrichment    > 1
)
n_post_qc <- ncol(pbmc)
cat(sprintf("  cells: %d → %d after QC\n", n_pre, n_post_qc))

# ---------------------------------------------------------------------------
# 2b. Stratified random downsample (spec §15.3 explicit fallback). Kept at
#     5,000 cells to match the spec's declared ceiling. This is the
#     pragmatic memory-pressure relief: the agentcoop-r-runtime image
#     uses native SCTransform (glmGamPoi has no binary for R 4.3 / aarch64
#     and the source build needs a newer C++ standard than rocker/r-ver
#     ships), so unbounded ~11k-cell SCTransform OOMs at 9.5 GB / 13.6 GB.
#     Sampling preserves the QC-passing pool's relative composition.
# ---------------------------------------------------------------------------
SCT_CELL_CAP <- 5000L
n_post <- n_post_qc
if (ncol(pbmc) > SCT_CELL_CAP) {
  set.seed(42)
  cat(sprintf("[%s] step 2b: stratified downsample %d → %d cells (spec §15.3)\n",
              Sys.time(), ncol(pbmc), SCT_CELL_CAP))
  sampled <- sample(colnames(pbmc), SCT_CELL_CAP)
  pbmc <- subset(pbmc, cells = sampled)
  n_post <- ncol(pbmc)
} else {
  cat(sprintf("[%s] step 2b: no downsample needed (n=%d ≤ cap=%d)\n",
              Sys.time(), ncol(pbmc), SCT_CELL_CAP))
}

# ---------------------------------------------------------------------------
# 3. RNA preprocessing for label transfer
# ---------------------------------------------------------------------------

cat(sprintf("[%s] step 3: SCTransform + RunPCA\n", Sys.time()))
DefaultAssay(pbmc) <- "RNA"
pbmc <- SCTransform(pbmc, verbose = FALSE)
pbmc <- RunPCA(pbmc, verbose = FALSE)

# ---------------------------------------------------------------------------
# 4. Cell-type annotation
#    PRIMARY:  Seurat label transfer from Hao PBMC reference (celltype.l2)
#    FALLBACK: SingleR + celldex::MonacoImmuneData (only if Hao unreadable)
# ---------------------------------------------------------------------------

annotation_method <- NA_character_
reference_name    <- NA_character_

# Try the Hao reference first.
hao_ok <- FALSE
hao_load_error <- NULL
if (file.exists(hao_path) && file.size(hao_path) > 100 * 1024 * 1024) {
  cat(sprintf("[%s] step 4 PRIMARY: load Hao reference (%s, %.1f GB)\n",
              Sys.time(), basename(hao_path), file.size(hao_path) / 1024^3))
  reference <- tryCatch(readRDS(hao_path), error = function(e) {
    hao_load_error <<- e$message
    NULL
  })
  if (!is.null(reference)) {
    reference <- tryCatch(UpdateSeuratObject(reference),
                          error = function(e) {
                            hao_load_error <<- e$message; NULL
                          })
  }
  if (!is.null(reference) && "celltype.l2" %in% colnames(reference@meta.data) &&
      "spca" %in% Reductions(reference)) {
    cat(sprintf("[%s] step 4 PRIMARY: FindTransferAnchors (SCT-mode)\n", Sys.time()))
    DefaultAssay(pbmc) <- "SCT"
    anchors <- tryCatch(
      FindTransferAnchors(
        reference            = reference,
        query                = pbmc,
        normalization.method = "SCT",
        reference.reduction  = "spca",
        recompute.residuals  = FALSE,
        dims                 = 1:50
      ),
      error = function(e) { hao_load_error <<- e$message; NULL }
    )
    if (!is.null(anchors)) {
      cat(sprintf("[%s] step 4 PRIMARY: TransferData (celltype.l2)\n", Sys.time()))
      predictions <- tryCatch(
        TransferData(
          anchorset        = anchors,
          refdata          = reference$celltype.l2,
          weight.reduction = pbmc[["pca"]],
          dims             = 1:50
        ),
        error = function(e) { hao_load_error <<- e$message; NULL }
      )
      if (!is.null(predictions)) {
        pbmc <- AddMetaData(pbmc, metadata = predictions)
        pbmc$cell_type      <- pbmc$predicted.id
        pbmc$cell_type_main <- pbmc$predicted.id   # Hao only writes a single l2 label
        pbmc$annotation_score <- pbmc$prediction.score.max
        annotation_method <- "Seurat::TransferData (Hao reference, celltype.l2)"
        reference_name    <- "Hao_2023_PBMC_multimodal_celltype.l2"
        hao_ok <- TRUE
        cat(sprintf("[%s] step 4 PRIMARY: Hao label transfer succeeded\n",
                    Sys.time()))
      }
    }
    rm(reference); gc()
  } else if (is.null(hao_load_error)) {
    hao_load_error <- "Hao RDS missing required `celltype.l2` column or `spca` reduction"
  }
}
if (!hao_ok) {
  if (is.null(hao_load_error)) {
    hao_load_error <- sprintf(
      "Hao RDS at %s missing or too small (%d bytes)",
      hao_path, if (file.exists(hao_path)) file.size(hao_path) else 0)
  }
  cat(sprintf("[%s] WARNING — Hao reference unavailable (%s)\n",
              Sys.time(), hao_load_error))
  cat(sprintf("[%s] step 4 FALLBACK: SingleR + celldex::MonacoImmuneData\n",
              Sys.time()))
  ref <- tryCatch(
    celldex::MonacoImmuneData(),
    error = function(e) {
      cat(sprintf("  MonacoImmuneData failed (%s) — falling back to HumanPrimaryCellAtlasData\n",
                  e$message))
      celldex::HumanPrimaryCellAtlasData()
    }
  )
  reference_name <- if (length(unique(ref$label.fine)) > 25) {
    "MonacoImmuneData_FALLBACK"
  } else {
    "HumanPrimaryCellAtlasData_FALLBACK"
  }
  annotation_method <- sprintf(
    "SingleR (Bioconductor) — Hao reference unavailable: %s", hao_load_error)

  # SingleR consumes log-normalized expression. After SCTransform the RNA
  # assay's `data` slot is empty (SCT writes its residuals to a different
  # assay), so we need to normalise RNA explicitly here.
  DefaultAssay(pbmc) <- "RNA"
  pbmc <- NormalizeData(pbmc, normalization.method = "LogNormalize",
                         scale.factor = 10000, verbose = FALSE)
  test_mat <- GetAssayData(pbmc, assay = "RNA", layer = "data")
  preds_fine <- SingleR(test = test_mat, ref = ref,
                        labels = ref$label.fine, de.method = "classic")
  preds_main <- SingleR(test = test_mat, ref = ref,
                        labels = ref$label.main, de.method = "classic")
  pbmc$cell_type        <- preds_fine$pruned.labels
  pbmc$cell_type_main   <- preds_main$pruned.labels
  pbmc$annotation_score <- apply(preds_fine$scores, 1, max, na.rm = TRUE)
}

pbmc$cell_type[is.na(pbmc$cell_type)] <- "Unknown"
pbmc$cell_type_main[is.na(pbmc$cell_type_main)] <- "Unknown"

ct_table      <- sort(table(pbmc$cell_type),      decreasing = TRUE)
ct_main_table <- sort(table(pbmc$cell_type_main), decreasing = TRUE)
cat(sprintf("  reference: %s\n", reference_name))
cat(sprintf("  cell_type (top 15):\n"))
print(head(ct_table, 15))
cat(sprintf("  cell_type_main (top 10):\n"))
print(head(ct_main_table, 10))

# ---------------------------------------------------------------------------
# 5. Write metadata CSV consumed by the AgentCo-Op wrappers
# ---------------------------------------------------------------------------

cat(sprintf("[%s] step 5: write metadata CSV\n", Sys.time()))
meta_out <- data.frame(
  barcode           = colnames(pbmc),
  sample            = sample_id,
  cell_type         = as.character(pbmc$cell_type),
  cell_type_main    = as.character(pbmc$cell_type_main),
  predicted_score   = as.numeric(pbmc$annotation_score),
  stringsAsFactors  = FALSE
)

dir.create(dirname(out_meta), recursive = TRUE, showWarnings = FALSE)
data.table::fwrite(meta_out, out_meta, compress = "gzip")
cat(sprintf("  wrote %d rows → %s\n", nrow(meta_out), out_meta))

summary_out <- list(
  source_h5         = normalizePath(h5_path),
  source_fragments  = normalizePath(frag_path),
  source_reference  = if (file.exists(hao_path)) normalizePath(hao_path) else hao_path,
  reference_name    = reference_name,
  annotation_method = annotation_method,
  hao_primary_used  = hao_ok,
  hao_load_error    = if (hao_ok) NULL else hao_load_error,
  sample_id         = sample_id,
  n_cells_pre_qc    = n_pre,
  n_cells_post_qc   = n_post,
  n_cell_types      = length(unique(pbmc$cell_type[pbmc$cell_type != "Unknown"])),
  cell_type_counts  = as.list(ct_table)
)
summary_path <- sub("\\.csv\\.gz$", ".summary.json", out_meta)
write_json(summary_out, summary_path, pretty = TRUE, auto_unbox = TRUE)
cat(sprintf("  wrote summary → %s\n", summary_path))

cat(sprintf("[%s] CS2 Lite annotation done\n", Sys.time()))
