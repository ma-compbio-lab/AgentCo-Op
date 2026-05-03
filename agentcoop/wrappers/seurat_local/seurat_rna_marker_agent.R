#!/usr/bin/env Rscript
# Seurat RNA marker discovery wrapper for AgentCo-Op CS2.
#
# Implements case_study_2.md §10.2: load gene-by-cell counts, attach
# celltype labels, FindAllMarkers per cell type (only.pos, wilcox), export
# the full marker table + per-cell-type top-N JSON + a result.json.
#
# Invocation: Rscript seurat_rna_marker_agent.R <invoke.json>
# The invoke JSON has the same shape the Python adapter receives;
# we pull the bits we need and emit the same artifact filenames so
# downstream nodes don't have to care which backend produced them.

suppressPackageStartupMessages({
  library(Seurat)
  library(data.table)
  library(Matrix)
  library(jsonlite)
  library(dplyr)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1) {
  stop("usage: Rscript seurat_rna_marker_agent.R <invoke.json>")
}
invoke_path <- args[1]
req <- jsonlite::fromJSON(invoke_path, simplifyVector = TRUE)

inputs   <- req$input
params   <- if (!is.null(inputs$parameters)) inputs$parameters else list()
dataset  <- if (!is.null(inputs$dataset))    inputs$dataset    else list()

top_n        <- as.integer(if (!is.null(params$top_n)) params$top_n else 50)
min_cells    <- as.integer(if (!is.null(params$min_cells_per_type)) params$min_cells_per_type else 20)
remove_cts   <- if (!is.null(params$remove_cell_types)) as.character(params$remove_cell_types) else c("Mixed", "Mix")
rna_method   <- if (!is.null(params$rna_marker_method)) params$rna_marker_method else list()
test_use     <- tolower(as.character(if (!is.null(rna_method$test_use)) rna_method$test_use else "wilcoxon"))
if (test_use == "wilcoxon") test_use <- "wilcox"
min_pct        <- as.numeric(if (!is.null(rna_method$min_pct)) rna_method$min_pct else 0.10)
logfc_thresh   <- as.numeric(if (!is.null(rna_method$logfc_threshold)) rna_method$logfc_threshold else 0.25)
only_pos       <- as.logical(if (!is.null(rna_method$only_pos)) rna_method$only_pos else TRUE)

out_dir <- as.character(req$output_dir)
if (is.null(out_dir) || nchar(out_dir) == 0) {
  out_dir <- "runs/case2/shareseq_skin/artifacts/seurat_run"
}
dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
log_path <- file.path(out_dir, "run.log")
log_con  <- file(log_path, open = "wt")
log_msg  <- function(msg) { cat(sprintf("[%s] %s\n", format(Sys.time(), "%H:%M:%S"), msg), file = log_con); flush(log_con) }

resolve_path <- function(cache_dir, fname, default) {
  if (is.null(fname) || nchar(fname) == 0) fname <- default
  candidates <- c(file.path(cache_dir, fname), fname,
                  file.path(getwd(), cache_dir, fname))
  for (p in candidates) if (file.exists(p)) return(normalizePath(p))
  stop(sprintf("could not resolve data file %s under %s", fname, cache_dir))
}

cache_dir <- if (!is.null(dataset$local_cache_dir)) dataset$local_cache_dir else "data/shareseq_skin"
files     <- if (!is.null(dataset$files)) dataset$files else list()
rna_path      <- resolve_path(cache_dir, files$rna_counts,
                              "GSM4156608_skin.late.anagen.rna.counts.txt.gz")
celltype_path <- resolve_path(cache_dir, files$celltype_labels,
                              "GSM4156597_skin_celltype.txt.gz")
log_msg(sprintf("rna_counts:    %s", rna_path))
log_msg(sprintf("celltypes:     %s", celltype_path))
log_msg(sprintf("top_n=%d  min_cells_per_type=%d  remove=%s",
                top_n, min_cells, paste(remove_cts, collapse = ",")))
log_msg(sprintf("test_use=%s  min_pct=%.3f  logfc_threshold=%.3f",
                test_use, min_pct, logfc_thresh))

read_count_matrix <- function(path) {
  # Prefer a sibling 10X MTX dir if present — the dense gzipped TSV
  # blows up R's RAM (~24 GB transient on a 32k-cell matrix), but
  # ReadMtx streams sparse so it stays well under 4 GB. The MTX trio
  # is produced offline by `scripts/dense_tsv_to_mtx.py`.
  parent <- dirname(path)
  mtx_dir <- file.path(parent, "rna_mtx")
  if (file.exists(file.path(mtx_dir, "matrix.mtx.gz"))) {
    log_msg(sprintf("reading sparse MTX trio at %s", mtx_dir))
    mat <- Seurat::ReadMtx(
      mtx = file.path(mtx_dir, "matrix.mtx.gz"),
      features = file.path(mtx_dir, "features.tsv.gz"),
      cells = file.path(mtx_dir, "barcodes.tsv.gz"),
    )
    return(mat)
  }
  log_msg(sprintf("reading count matrix: %s", path))
  dt <- data.table::fread(path, sep = "\t", showProgress = FALSE)
  feature_col <- names(dt)[1]
  features <- as.character(dt[[feature_col]])
  mat_df <- as.data.frame(dt[, -1, with = FALSE])
  mat <- as(as.matrix(mat_df), "dgCMatrix")
  rownames(mat) <- make.unique(features)
  cn <- names(dt)[-1]
  cn <- gsub(",", ".", cn, fixed = TRUE)
  colnames(mat) <- cn
  mat
}

counts <- read_count_matrix(rna_path)
log_msg(sprintf("count matrix: %d genes x %d cells", nrow(counts), ncol(counts)))

labels <- data.table::fread(celltype_path, sep = "\t", showProgress = FALSE)
log_msg(sprintf("celltypes table: %d rows x %d cols (%s)",
                nrow(labels), ncol(labels), paste(colnames(labels), collapse = ",")))

# --- column inference (mirrors Python adapter) -------------------------------
candidate_rna_cols <- intersect(colnames(labels),
                                c("rna.bc", "rna_bc", "rna_barcode", "rna"))
rna_col <- if (length(candidate_rna_cols) > 0) candidate_rna_cols[1] else colnames(labels)[1]
candidate_ct_cols <- intersect(colnames(labels),
                               c("celltype", "cell_type", "Celltype", "Cell_Type", "cluster", "label"))
celltype_col <- if (length(candidate_ct_cols) > 0) candidate_ct_cols[1] else tail(colnames(labels), 1)
log_msg(sprintf("barcode_col=%s  celltype_col=%s", rna_col, celltype_col))

metadata <- as.data.frame(labels)
metadata[[rna_col]] <- as.character(metadata[[rna_col]])
metadata[[celltype_col]] <- as.character(metadata[[celltype_col]])
rownames(metadata) <- metadata[[rna_col]]

common_cells <- intersect(colnames(counts), rownames(metadata))
log_msg(sprintf("cells with celltype labels: %d / %d", length(common_cells), ncol(counts)))
if (length(common_cells) == 0) stop("no cells overlap between counts and celltype labels")
counts <- counts[, common_cells]
metadata <- metadata[common_cells, , drop = FALSE]
metadata$cell_type <- metadata[[celltype_col]]

if (length(remove_cts) > 0) {
  keep <- !(metadata$cell_type %in% remove_cts)
  counts <- counts[, keep]; metadata <- metadata[keep, , drop = FALSE]
}

cell_counts <- table(metadata$cell_type)
valid_types <- names(cell_counts[cell_counts >= min_cells])
keep <- metadata$cell_type %in% valid_types
counts <- counts[, keep]; metadata <- metadata[keep, , drop = FALSE]
log_msg(sprintf("after filters: %d cells, %d cell types", ncol(counts), length(valid_types)))

obj <- CreateSeuratObject(counts = counts, meta.data = metadata)
Idents(obj) <- obj$cell_type
obj <- NormalizeData(obj, normalization.method = "LogNormalize", scale.factor = 10000)
obj <- FindVariableFeatures(obj)

log_msg("running FindAllMarkers")
markers <- FindAllMarkers(
  obj,
  only.pos = only_pos,
  test.use = test_use,
  min.pct = min_pct,
  logfc.threshold = logfc_thresh
)
fc_col <- intersect(c("avg_log2FC", "avg_logFC"), colnames(markers))[1]
if (is.na(fc_col)) stop("No avg_log2FC/avg_logFC column in marker table")
markers <- markers %>% arrange(cluster, p_val_adj, desc(.data[[fc_col]]))
log_msg(sprintf("marker rows: %d", nrow(markers)))

markers_csv <- file.path(out_dir, "seurat_rna_markers_all.csv")
data.table::fwrite(markers, markers_csv)

top <- markers %>%
  group_by(cluster) %>%
  arrange(p_val_adj, desc(.data[[fc_col]]), .by_group = TRUE) %>%
  slice_head(n = top_n) %>%
  summarise(genes = list(unique(gene)), .groups = "drop")
top_json_path <- file.path(out_dir, "rna_top_markers_by_celltype.json")
top_list <- setNames(top$genes, top$cluster)
jsonlite::write_json(top_list, top_json_path, pretty = TRUE, auto_unbox = TRUE)

ct_dist <- data.frame(
  cell_type = names(table(obj$cell_type)),
  n_cells   = as.integer(table(obj$cell_type))
)
ct_csv <- file.path(out_dir, "celltype_distribution.csv")
data.table::fwrite(ct_dist, ct_csv)

result <- list(
  status = "success",
  summary = sprintf("Seurat (R) FindAllMarkers on %d cells x %d genes; %d cell types; top_n=%d; %d marker rows",
                    ncol(counts), nrow(counts), length(valid_types), top_n, nrow(markers)),
  main_results = list(
    n_cells = ncol(counts),
    n_genes = nrow(counts),
    n_cell_types = length(valid_types),
    top_n = top_n,
    n_marker_rows = nrow(markers),
    barcode_column = rna_col,
    celltype_column = celltype_col,
    backend = "R/Seurat"
  ),
  artifacts = list(
    rna_markers_all_csv      = markers_csv,
    rna_top_markers_json     = top_json_path,
    celltype_distribution_csv = ct_csv,
    run_log                  = log_path
  ),
  warnings = c()
)
jsonlite::write_json(result, file.path(out_dir, "result.json"),
                     pretty = TRUE, auto_unbox = TRUE)
close(log_con)

# Echo the response to stdout so the orchestrator's docker-run path can
# parse it directly if needed.
cat(jsonlite::toJSON(result, pretty = FALSE, auto_unbox = TRUE), "\n", sep = "")
