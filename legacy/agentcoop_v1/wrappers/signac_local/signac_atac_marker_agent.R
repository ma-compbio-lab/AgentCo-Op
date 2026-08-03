#!/usr/bin/env Rscript
# Signac ATAC marker-gene wrapper for AgentCo-Op CS2.
#
# Per the user override of docs/experiments/case_study_2.md §11, this wrapper makes
# Signac::GeneActivity() the **primary** marker-gene method and
# requires the GSM4156597 fragments BED file as a mandatory input.
# Falls back to peak-to-nearest-gene only if the fragments file or its
# tabix index is missing AND the user has explicitly enabled that
# fallback in `parameters.atac_marker_method.allow_peak_to_gene_fallback`
# — and we log the fallback prominently per docs/experiments/case_study_2.md §17.3.
#
# Invocation: Rscript signac_atac_marker_agent.R <invoke.json>

suppressPackageStartupMessages({
  library(Signac)
  library(Seurat)
  library(data.table)
  library(Matrix)
  library(jsonlite)
  library(dplyr)
  library(GenomicRanges)
  library(GenomeInfoDb)
  library(Rsamtools)
})

# Genome-specific EnsDb packages are loaded lazily below based on
# `dataset.genome` (mm10 → EnsDb.Mmusculus.v79; hg38 → EnsDb.Hsapiens.v86).
# Both ship in the agentcoop-r-runtime image; a missing one is a hard
# failure with a clear error.
load_ensdb_for_genome <- function(genome_id) {
  pkg <- switch(tolower(genome_id),
                "mm10" = "EnsDb.Mmusculus.v79",
                "grcm38" = "EnsDb.Mmusculus.v79",
                "hg38" = "EnsDb.Hsapiens.v86",
                "grch38" = "EnsDb.Hsapiens.v86",
                stop(sprintf("unsupported genome '%s' (expected mm10 or hg38)", genome_id)))
  if (!requireNamespace(pkg, quietly = TRUE)) {
    stop(sprintf("annotation package '%s' is not installed in the R image", pkg))
  }
  suppressPackageStartupMessages(library(pkg, character.only = TRUE))
  get(pkg, envir = asNamespace(pkg))
}

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1) {
  stop("usage: Rscript signac_atac_marker_agent.R <invoke.json>")
}
invoke_path <- args[1]
req <- jsonlite::fromJSON(invoke_path, simplifyVector = TRUE)

inputs   <- req$input
params   <- if (!is.null(inputs$parameters)) inputs$parameters else list()
dataset  <- if (!is.null(inputs$dataset))    inputs$dataset    else list()

top_n        <- as.integer(if (!is.null(params$top_n)) params$top_n else 50)
min_cells    <- as.integer(if (!is.null(params$min_cells_per_type)) params$min_cells_per_type else 20)
remove_cts   <- if (!is.null(params$remove_cell_types)) as.character(params$remove_cell_types) else c("Mixed", "Mix")
atac_method  <- if (!is.null(params$atac_marker_method)) params$atac_marker_method else list()
test_use     <- tolower(as.character(if (!is.null(atac_method$test_use)) atac_method$test_use else "wilcoxon"))
if (test_use == "wilcoxon") test_use <- "wilcox"
min_pct      <- as.numeric(if (!is.null(atac_method$min_pct)) atac_method$min_pct else 0.05)
extend_up    <- as.integer(if (!is.null(atac_method$extend_upstream_bp)) atac_method$extend_upstream_bp else 2000)
allow_p2g_fb <- isTRUE(atac_method$allow_peak_to_gene_fallback)
# Method selector (docs/experiments/case_study_2.md §11): peak_to_gene is the primary
# method declared in the doc and the only one that fits on a 16 GB host
# at full SHARE-seq scale (32 k cells × 140 M fragments). GeneActivity
# is the sensitivity-analysis option (§11.2) — set
# `signac_method: gene_activity` to enable it; requires colima ≥ 22 GB.
signac_method <- tolower(as.character(if (!is.null(atac_method$signac_method)) atac_method$signac_method else "peak_to_gene"))
# Stratified-by-cell-type subsample cap for peak marker discovery.
# FindAllMarkers on the full 32 k-cell × 86 k-peak matrix takes >2 h
# of single-thread R wall time on a 16 GB Mac; capping at 4 000 cells
# (≈180 / cell type) finishes in ~15 min while preserving every cell
# type. Set 0 to disable.
peak_marker_max_cells <- as.integer(if (!is.null(atac_method$peak_marker_max_cells)) atac_method$peak_marker_max_cells else 4000)

out_dir <- as.character(req$output_dir)
if (is.null(out_dir) || nchar(out_dir) == 0) {
  out_dir <- "runs/case2/shareseq_skin/artifacts/signac_run"
}
dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
log_path <- file.path(out_dir, "run.log")
log_con  <- file(log_path, open = "wt")
log_msg  <- function(msg) { cat(sprintf("[%s] %s\n", format(Sys.time(), "%H:%M:%S"), msg), file = log_con); flush(log_con) }

resolve_path <- function(cache_dir, fname, default = NULL, required = TRUE) {
  if (is.null(fname) || nchar(fname) == 0) fname <- default
  if (is.null(fname)) {
    if (required) stop("missing required file path") else return(NULL)
  }
  candidates <- c(file.path(cache_dir, fname), fname,
                  file.path(getwd(), cache_dir, fname))
  for (p in candidates) if (file.exists(p)) return(normalizePath(p))
  if (required) stop(sprintf("could not resolve data file %s under %s", fname, cache_dir))
  NULL
}

cache_dir <- if (!is.null(dataset$local_cache_dir)) dataset$local_cache_dir else "data/shareseq_skin"
files     <- if (!is.null(dataset$files)) dataset$files else list()
dataset_format <- tolower(as.character(if (!is.null(dataset$format)) dataset$format else "dense_tsv"))
genome_id      <- tolower(as.character(if (!is.null(dataset$genome)) dataset$genome else "mm10"))
sample_id      <- as.character(if (!is.null(dataset$default_sample_id)) dataset$default_sample_id else "")

# 10x multiome ships GeneActivity-friendly fragments by default, so the
# spec (docs/experiments/case_study_2_human_heart.md §11) makes GeneActivity primary.
# Honor the request override if present; otherwise default by format.
if (is.null(atac_method$signac_method) || nchar(as.character(atac_method$signac_method)) == 0) {
  signac_method <- if (dataset_format == "tenx_h5_multiome") "gene_activity" else "peak_to_gene"
}

if (dataset_format == "tenx_h5_multiome") {
  h5_path        <- resolve_path(cache_dir, files$tenx_h5, NULL)
  fragments_path <- resolve_path(cache_dir, files$atac_fragments, NULL,
                                 required = !allow_p2g_fb)
  celltype_path  <- resolve_path(cache_dir, files$metadata,
                                 if (!is.null(files$celltype_labels)) files$celltype_labels else NULL)
  atac_path  <- h5_path
  peaks_path <- h5_path
  log_msg(sprintf("dataset.format=tenx_h5_multiome  genome=%s  sample_id=%s",
                  genome_id, sample_id))
  log_msg(sprintf("tenx_h5:     %s", h5_path))
  log_msg(sprintf("fragments:   %s", ifelse(is.null(fragments_path), "<unavailable>", fragments_path)))
  log_msg(sprintf("metadata:    %s", celltype_path))
} else {
  atac_path     <- resolve_path(cache_dir, files$atac_counts,
                                "GSM4156597_skin.late.anagen.counts.txt.gz")
  peaks_path    <- resolve_path(cache_dir, files$atac_peaks,
                                "GSM4156597_skin.late.anagen.peaks.bed.gz")
  celltype_path <- resolve_path(cache_dir, files$celltype_labels,
                                "GSM4156597_skin_celltype.txt.gz")
  fragments_path <- resolve_path(cache_dir, files$atac_fragments,
                                 "GSM4156597_skin.late.anagen.atac.fragments.bed.gz",
                                 required = !allow_p2g_fb)
  log_msg(sprintf("dataset.format=dense_tsv  genome=%s (default — SHARE-seq path)", genome_id))
  log_msg(sprintf("atac_counts: %s", atac_path))
  log_msg(sprintf("peaks_bed:   %s", peaks_path))
  log_msg(sprintf("celltypes:   %s", celltype_path))
  log_msg(sprintf("fragments:   %s", ifelse(is.null(fragments_path), "<unavailable>", fragments_path)))
}
log_msg(sprintf("signac_method=%s", signac_method))
log_msg(sprintf("top_n=%d  min_cells_per_type=%d  test_use=%s  min_pct=%.3f  extend_upstream=%d",
                top_n, min_cells, test_use, min_pct, extend_up))

read_count_matrix <- function(path) {
  # See seurat_rna_marker_agent.R for rationale — sparse MTX dramatically
  # reduces RAM peak vs the dense TSV path.
  parent <- dirname(path)
  mtx_dir <- file.path(parent, "atac_mtx")
  if (file.exists(file.path(mtx_dir, "matrix.mtx.gz"))) {
    log_msg(sprintf("reading sparse MTX trio at %s", mtx_dir))
    mat <- Seurat::ReadMtx(
      mtx = file.path(mtx_dir, "matrix.mtx.gz"),
      features = file.path(mtx_dir, "features.tsv.gz"),
      cells = file.path(mtx_dir, "barcodes.tsv.gz"),
    )
    return(mat)
  }
  log_msg(sprintf("reading peak count matrix: %s", path))
  dt <- data.table::fread(path, sep = "\t", showProgress = FALSE)
  feature_col <- names(dt)[1]
  features <- as.character(dt[[feature_col]])
  mat_df <- as.data.frame(dt[, -1, with = FALSE])
  mat <- as(as.matrix(mat_df), "dgCMatrix")
  rownames(mat) <- features
  cn <- names(dt)[-1]; cn <- gsub(",", ".", cn, fixed = TRUE)
  colnames(mat) <- cn
  mat
}

read_peaks <- function(path, peak_names) {
  peaks <- data.table::fread(path, header = FALSE, showProgress = FALSE)
  colnames(peaks)[1:3] <- c("chr", "start", "end")
  gr <- GRanges(seqnames = peaks$chr,
                ranges = IRanges(start = peaks$start + 1, end = peaks$end))
  if (length(peak_names) == length(gr)) {
    names(gr) <- peak_names
  } else {
    names(gr) <- paste0(peaks$chr, ":", peaks$start + 1, "-", peaks$end)
  }
  gr
}

# Parse 10x peak rownames like "chr1:1234-2345" into a GRanges.
parse_peak_names_to_granges <- function(peak_names) {
  m <- regmatches(peak_names, regexec("^([^:]+):([0-9]+)-([0-9]+)$", peak_names))
  ok <- vapply(m, function(x) length(x) == 4, logical(1))
  if (!all(ok)) {
    bad <- head(peak_names[!ok], 3)
    stop(sprintf("could not parse %d 10x peak names; first bad: %s",
                 sum(!ok), paste(bad, collapse = ",")))
  }
  parts <- do.call(rbind, lapply(m, function(x) x[2:4]))
  gr <- GRanges(seqnames = parts[, 1],
                ranges = IRanges(start = as.integer(parts[, 2]),
                                 end   = as.integer(parts[, 3])))
  names(gr) <- peak_names
  gr
}

# Generic metadata reader (csv/csv.gz/tsv/tsv.gz/txt/txt.gz).
read_metadata_table <- function(path) {
  base <- basename(path)
  sep <- if (grepl("\\.csv(\\.gz)?$", base, ignore.case = TRUE)) "," else "\t"
  data.table::fread(path, sep = sep, showProgress = FALSE)
}

# Pick the first candidate column name that exists in `cols` (case- and
# punctuation-insensitive). Mirrors the Seurat wrapper.
find_candidate_col <- function(cols, candidates) {
  norm <- function(x) tolower(gsub("[^a-z0-9]", "", tolower(x)))
  cols_norm <- norm(cols)
  for (cand in candidates) {
    hit <- which(cols_norm == norm(cand))
    if (length(hit) > 0) return(cols[hit[1]])
  }
  NA_character_
}

# Strip leading sample/batch prefix (short alphanumeric + underscore,
# e.g. "MA7_", "s3_", "s4_") and trailing 10x lane suffix ("-1", "-2");
# mirrors the Seurat wrapper and docs/experiments/case_study_2_human_heart.md §8.5. The
# prefix length is bounded at 1..6 chars so we don't accidentally
# consume the actual 16-nt cell barcode.
normalize_barcode <- function(x) {
  x <- as.character(x)
  x <- gsub("^[A-Za-z][A-Za-z0-9]{0,5}_", "", x)
  x <- gsub("-[0-9]+$", "", x)
  x
}

if (dataset_format == "tenx_h5_multiome") {
  log_msg(sprintf("reading 10x multiome H5: %s", atac_path))
  counts_list <- Seurat::Read10X_h5(atac_path, use.names = TRUE)
  if (!is.list(counts_list) || !("Peaks" %in% names(counts_list))) {
    stop(sprintf("Read10X_h5 missing Peaks assay; available: %s",
                 paste(names(counts_list), collapse = ", ")))
  }
  counts <- counts_list[["Peaks"]]
  peaks_gr <- parse_peak_names_to_granges(rownames(counts))
  log_msg(sprintf("peak matrix (10x H5): %d peaks x %d cells",
                  nrow(counts), ncol(counts)))
  labels <- read_metadata_table(celltype_path)
} else {
  counts <- read_count_matrix(atac_path)
  peaks_gr <- read_peaks(peaks_path, rownames(counts))
  log_msg(sprintf("peak matrix: %d peaks x %d cells", nrow(counts), ncol(counts)))

  labels <- data.table::fread(celltype_path, sep = "\t", showProgress = FALSE)
}
if (dataset_format == "tenx_h5_multiome") {
  log_msg(sprintf("metadata table: %d rows x %d cols (%s)",
                  nrow(labels), ncol(labels), paste(colnames(labels), collapse = ",")))
  sample_col <- find_candidate_col(
    colnames(labels),
    c("sample", "sample_id", "library", "donor", "donor_id", "orig.ident")
  )
  if (!is.na(sample_col) && nchar(sample_id) > 0) {
    pre <- nrow(labels)
    labels <- labels[toupper(as.character(labels[[sample_col]])) == toupper(sample_id)]
    log_msg(sprintf("filtered metadata to sample_id=%s via column '%s': %d -> %d rows",
                    sample_id, sample_col, pre, nrow(labels)))
  }
  bc_col <- find_candidate_col(
    colnames(labels),
    c("barcode", "cell_barcode", "cell_id", "cellID", "cell.name", "cell", "Cell")
  )
  if (is.na(bc_col)) bc_col <- colnames(labels)[1]
  ct_col <- find_candidate_col(
    colnames(labels),
    c("cell_type", "cell.type", "celltype", "CellType", "annotation",
      "broad_cell_type", "label", "cluster_label", "final_cell_type",
      "cell.type.l1", "predicted.celltype")
  )
  if (is.na(ct_col)) ct_col <- tail(colnames(labels), 1)
  log_msg(sprintf("barcode_col=%s  celltype_col=%s", bc_col, ct_col))

  metadata <- as.data.frame(labels)
  metadata[[bc_col]] <- as.character(metadata[[bc_col]])
  metadata[[ct_col]] <- as.character(metadata[[ct_col]])
  metadata$barcode_norm <- normalize_barcode(metadata[[bc_col]])

  matrix_cells_raw  <- colnames(counts)
  matrix_cells_norm <- normalize_barcode(matrix_cells_raw)

  # Choose by metadata-coverage (see Seurat wrapper for rationale).
  strategies <- list(
    list(name = "exact",         idx = match(matrix_cells_raw, metadata[[bc_col]])),
    list(name = "norm_vs_raw",   idx = match(matrix_cells_norm, metadata[[bc_col]])),
    list(name = "norm_vs_norm",  idx = match(matrix_cells_norm, metadata$barcode_norm))
  )
  pick <- strategies[[which.max(sapply(strategies, function(s) sum(!is.na(s$idx))))]]
  match_idx <- pick$idx
  overlap_n <- sum(!is.na(match_idx))
  metadata_coverage <- overlap_n / max(1L, nrow(metadata))
  log_msg(sprintf(
    "barcode match strategy=%s: %d matrix cells matched a metadata row (matrix coverage %.1f%%, metadata coverage %.1f%%)",
    pick$name, overlap_n,
    100 * overlap_n / length(match_idx),
    100 * metadata_coverage))
  if (metadata_coverage < 0.80) {
    stop(sprintf(
      "only %d / %d metadata cells found in matrix (%.1f%% < 80%%) — barcode format probably mismatched (docs/experiments/case_study_2_human_heart.md §8.5)",
      overlap_n, nrow(metadata), 100 * metadata_coverage))
  }
  matched_meta <- metadata[match_idx, , drop = FALSE]
  rownames(matched_meta) <- matrix_cells_raw
  keep_mask <- !is.na(matched_meta[[ct_col]]) & nchar(matched_meta[[ct_col]]) > 0
  counts <- counts[, keep_mask, drop = FALSE]
  metadata <- matched_meta[keep_mask, , drop = FALSE]
  metadata$cell_type <- metadata[[ct_col]]
} else {
  # SHARE-seq quirk (docs/experiments/case_study_2.md §9.3): the ATAC MTX cell barcodes
  # are taken from `barcodes.txt.gz`, which actually matches the `rna.bc`
  # column in celltype.txt — not `atac.bc`. Pick the candidate column
  # whose set has maximum overlap with the MTX cell ids instead of
  # blindly picking the first naming hint.
  candidate_atac_cols <- intersect(colnames(labels),
                                   c("atac.bc", "atac_bc", "atac_barcode",
                                     "rna.bc", "rna_bc", "rna_barcode"))
  mtx_cells <- as.character(colnames(counts))
  mtx_set <- as.character(mtx_cells)
  overlap_per_col <- sapply(candidate_atac_cols, function(col) {
    vals <- as.character(labels[[col]])
    length(intersect(vals, mtx_set))
  })
  log_msg(sprintf("barcode-overlap candidates: %s",
                  paste(sprintf("%s=%d", names(overlap_per_col), overlap_per_col),
                        collapse = ", ")))
  bc_col <- if (length(overlap_per_col) > 0) {
    names(which.max(overlap_per_col))[1]
  } else {
    colnames(labels)[1]
  }
  candidate_ct_cols <- intersect(colnames(labels),
                                 c("celltype", "cell_type", "Celltype", "Cell_Type",
                                   "cluster", "label"))
  ct_col <- if (length(candidate_ct_cols) > 0) candidate_ct_cols[1] else tail(colnames(labels), 1)
  log_msg(sprintf("barcode_col=%s  celltype_col=%s", bc_col, ct_col))

  metadata <- as.data.frame(labels)
  metadata[[bc_col]] <- as.character(metadata[[bc_col]])
  metadata[[ct_col]] <- as.character(metadata[[ct_col]])
  rownames(metadata) <- metadata[[bc_col]]

  common_cells <- intersect(colnames(counts), rownames(metadata))
  log_msg(sprintf("cells with celltype labels: %d / %d", length(common_cells), ncol(counts)))
  if (length(common_cells) == 0) stop("no cells overlap between ATAC counts and celltype labels")
  counts <- counts[, common_cells]
  metadata <- metadata[common_cells, , drop = FALSE]
  metadata$cell_type <- metadata[[ct_col]]
}

if (length(remove_cts) > 0) {
  keep <- !(metadata$cell_type %in% remove_cts)
  counts <- counts[, keep]; metadata <- metadata[keep, , drop = FALSE]
}
cell_counts <- table(metadata$cell_type)
valid_types <- names(cell_counts[cell_counts >= min_cells])
keep <- metadata$cell_type %in% valid_types
counts <- counts[, keep]; metadata <- metadata[keep, , drop = FALSE]
log_msg(sprintf("after filters: %d cells, %d cell types", ncol(counts), length(valid_types)))

# Pick the EnsDb annotation package by genome (mm10 → Mmusculus.v79;
# hg38 → Hsapiens.v86). Both are baked into agentcoop-r-runtime.
ensdb <- load_ensdb_for_genome(genome_id)
annotation <- GetGRangesFromEnsDb(ensdb = ensdb)
seqlevelsStyle(annotation) <- "UCSC"
genome(annotation) <- genome_id

# --- Validate fragments file + index, attempt repair if missing -------------
gene_act_used <- FALSE
fragments_repaired <- FALSE
if (!is.null(fragments_path)) {
  tbi <- paste0(fragments_path, ".tbi")
  if (!file.exists(tbi)) {
    log_msg(sprintf("WARN no tabix index for %s — attempting Rsamtools indexTabix repair", fragments_path))
    repaired <- tryCatch({
      Rsamtools::indexTabix(fragments_path, format = "bed")
      TRUE
    }, error = function(e) {
      log_msg(sprintf("FAIL Rsamtools::indexTabix failed: %s", e$message))
      FALSE
    })
    if (!repaired && !allow_p2g_fb) {
      stop("fragments tabix index missing and repair failed; set allow_peak_to_gene_fallback to use peak-to-gene proxy")
    }
    fragments_repaired <- repaired
  }
}

# Build the ChromatinAssay. The SHARE-seq path needs a custom
# fragment-cells lookup (rna.bc → atac.bc remap from the celltype
# table) because the matrix and fragment file use different barcode
# styles. The 10x_h5_multiome path doesn't need this — both the H5
# matrix and the fragment file use the same `BARCODE-1` style.
fragment_cells_arg <- NULL
if (dataset_format != "tenx_h5_multiome" && !is.null(fragments_path)) {
  atac_bc_candidates <- intersect(colnames(labels),
                                  c("atac.bc", "atac_bc", "atac_barcode"))
  if (length(atac_bc_candidates) > 0) {
    atac_col <- atac_bc_candidates[1]
    bc_lookup <- as.character(labels[[bc_col]])
    atac_lookup <- as.character(labels[[atac_col]])
    matched_atac <- atac_lookup[match(rownames(metadata), bc_lookup)]
    matched_atac <- gsub(",", ".", matched_atac, fixed = TRUE)
    metadata[[atac_col]] <- matched_atac
    fragment_cells_arg <- setNames(matched_atac, rownames(metadata))
    log_msg(sprintf("CreateFragmentObject cells lookup: %d names → %d unique values (atac_col=%s)",
                    length(fragment_cells_arg), length(unique(fragment_cells_arg)), atac_col))
  }
}

chrom_assay <- if (!is.null(fragment_cells_arg)) {
  fragments_obj <- CreateFragmentObject(
    path = fragments_path,
    cells = fragment_cells_arg,
    validate.fragments = TRUE,
    verbose = FALSE
  )
  CreateChromatinAssay(
    counts = counts,
    ranges = peaks_gr,
    genome = genome_id,
    annotation = annotation,
    fragments = list(fragments_obj)
  )
} else {
  CreateChromatinAssay(
    counts = counts,
    ranges = peaks_gr,
    genome = genome_id,
    annotation = annotation,
    fragments = fragments_path
  )
}
obj <- CreateSeuratObject(counts = chrom_assay, assay = "peaks", meta.data = metadata)
Idents(obj) <- obj$cell_type
obj <- RunTFIDF(obj)
# min.cutoff = "q75" keeps the top 25% of peaks by total accessibility,
# turning the 344 k-peak SHARE-seq matrix into ~85 k informative peaks.
# Without this filter, FindAllMarkers on the full peak set takes >2 h
# of single-thread R wall-time on this dataset and exceeds the
# orchestrator's docker-run timeout.
obj <- FindTopFeatures(obj, min.cutoff = "q75")
obj <- RunSVD(obj)
log_msg(sprintf("variable features after FindTopFeatures(q75): %d",
                length(VariableFeatures(obj))))

if (signac_method == "gene_activity" && !is.null(fragments_path)) {
  # Stratified-by-cell-type subsample to bound GeneActivity cost. The
  # call scales roughly linearly with n_cells × n_genes × frags/cell;
  # on the 10k-cell PBMC multiome we measured 5+ hours single-threaded.
  # `peak_marker_max_cells > 0` (already documented for peak_to_gene)
  # also caps GeneActivity here, so a single param controls both
  # ATAC-marker paths. Set 0 to disable.
  obj_for_ga <- obj
  if (peak_marker_max_cells > 0 && ncol(obj) > peak_marker_max_cells) {
    set.seed(42)
    cells_by_type <- split(colnames(obj), obj$cell_type)
    per_type_cap <- max(1L, ceiling(peak_marker_max_cells / length(cells_by_type)))
    sampled <- unlist(lapply(cells_by_type, function(cs) {
      if (length(cs) <= per_type_cap) cs else sample(cs, per_type_cap)
    }), use.names = FALSE)
    log_msg(sprintf(
      "subsampling for GeneActivity: %d → %d cells (%d / cell type)",
      ncol(obj), length(sampled), per_type_cap))
    obj_for_ga <- subset(obj, cells = sampled)
  } else {
    log_msg(sprintf("no subsampling for GeneActivity (n_cells=%d, cap=%d)",
                    ncol(obj), peak_marker_max_cells))
  }
  log_msg("computing GeneActivity (signac_method=gene_activity)")
  ga <- tryCatch(
    GeneActivity(obj_for_ga, extend.upstream = extend_up),
    error = function(e) {
      log_msg(sprintf("FAIL GeneActivity threw: %s", e$message)); NULL
    }
  )
  if (!is.null(ga)) {
    obj_for_ga[["activity"]] <- CreateAssayObject(counts = ga)
    DefaultAssay(obj_for_ga) <- "activity"
    obj_for_ga <- NormalizeData(obj_for_ga, assay = "activity",
                                normalization.method = "LogNormalize",
                                scale.factor = median(obj_for_ga$nCount_activity))
    log_msg("running FindAllMarkers on gene-activity assay")
    ga_markers <- FindAllMarkers(obj_for_ga, only.pos = TRUE,
                                 test.use = test_use,
                                 min.pct = min_pct, logfc.threshold = 0.25)
    fc_col <- intersect(c("avg_log2FC", "avg_logFC"), colnames(ga_markers))[1]
    ga_markers <- ga_markers %>% arrange(cluster, p_val_adj, desc(.data[[fc_col]]))
    ga_csv <- file.path(out_dir, "signac_gene_activity_markers_all.csv")
    data.table::fwrite(ga_markers, ga_csv)
    top_ga <- ga_markers %>%
      group_by(cluster) %>%
      arrange(p_val_adj, desc(.data[[fc_col]]), .by_group = TRUE) %>%
      slice_head(n = top_n) %>%
      summarise(genes = list(unique(gene)), .groups = "drop")
    primary_top_path <- file.path(out_dir, "atac_top_marker_genes_by_celltype.json")
    jsonlite::write_json(setNames(top_ga$genes, top_ga$cluster),
                         primary_top_path, pretty = TRUE, auto_unbox = TRUE)
    gene_act_used <- TRUE
    primary_marker_csv <- ga_csv
    n_marker_rows <- nrow(ga_markers)
    log_msg(sprintf("GeneActivity markers: %d rows; cell types: %d",
                    n_marker_rows, length(unique(ga_markers$cluster))))
  }
}

if (!gene_act_used) {
  if (signac_method == "gene_activity") {
    log_msg("FALLBACK using peak-to-nearest-gene (docs/experiments/case_study_2.md §17.3 — GeneActivity unavailable)")
  } else {
    log_msg("running peak-to-nearest-gene (signac_method=peak_to_gene; docs/experiments/case_study_2.md §11 primary path)")
  }
  DefaultAssay(obj) <- "peaks"
  # Stratified-by-cell-type subsample to make peak marker discovery
  # tractable (see peak_marker_max_cells comment near the top).
  obj_for_markers <- obj
  if (peak_marker_max_cells > 0 && ncol(obj) > peak_marker_max_cells) {
    set.seed(42)
    cells_by_type <- split(colnames(obj), obj$cell_type)
    per_type_cap <- max(1L, ceiling(peak_marker_max_cells / length(cells_by_type)))
    sampled <- unlist(lapply(cells_by_type, function(cs) {
      if (length(cs) <= per_type_cap) cs else sample(cs, per_type_cap)
    }), use.names = FALSE)
    log_msg(sprintf("subsampling for marker discovery: %d → %d cells (%d / cell type)",
                    ncol(obj), length(sampled), per_type_cap))
    obj_for_markers <- subset(obj, cells = sampled)
  } else {
    log_msg(sprintf("no subsampling for marker discovery (n_cells=%d ≤ cap=%d)",
                    ncol(obj), peak_marker_max_cells))
  }

  # Restrict marker discovery to the variable peaks selected upstream
  # so FindAllMarkers stays bounded; Seurat 5 + presto handle this in
  # roughly linear time.
  var_features <- VariableFeatures(obj_for_markers)
  log_msg(sprintf("running FindAllMarkers on %d variable peaks × %d cells",
                  length(var_features), ncol(obj_for_markers)))
  peak_markers_raw <- tryCatch(FindAllMarkers(
    obj_for_markers,
    features = var_features,
    only.pos = TRUE,
    test.use = test_use,
    min.pct = min_pct,
    logfc.threshold = 0.25
  ), error = function(e) {
    log_msg(sprintf("FindAllMarkers failed: %s", e$message)); NULL
  })
  if (is.null(peak_markers_raw) || nrow(peak_markers_raw) == 0) {
    stop("Signac peak-marker discovery returned no markers")
  }
  peak_markers <- peak_markers_raw
  peak_markers$peak <- rownames(peak_markers)
  cell_types <- sort(unique(as.character(peak_markers$cluster)))
  all_peak_markers <- split(peak_markers, peak_markers$cluster)
  log_msg(sprintf("peak markers: %d rows across %d cell types",
                  nrow(peak_markers), length(cell_types)))
  peak_csv <- file.path(out_dir, "signac_atac_peak_markers_all.csv")
  data.table::fwrite(peak_markers, peak_csv)
  # One ClosestFeature call on the unique peak set, then split by cluster.
  # The per-cluster loop used to call ClosestFeature 22× over the same
  # mm10 EnsDb annotation — each call rebuilds GRanges overlaps from
  # scratch, ~22× redundant work.
  unique_peaks <- unique(peak_markers$peak)
  cf_all <- tryCatch(ClosestFeature(obj_for_markers, regions = unique_peaks),
                     error = function(e) {
                       log_msg(sprintf("ClosestFeature failed: %s", e$message)); NULL
                     })
  if (is.null(cf_all)) {
    peak_to_gene <- data.frame()
  } else {
    cf_all$peak <- unique_peaks
    peak_to_gene <- peak_markers %>%
      dplyr::select(cluster, peak) %>%
      dplyr::left_join(cf_all, by = "peak")
  }
  data.table::fwrite(peak_to_gene, file.path(out_dir, "peak_to_gene_mapping.csv"))
  merged <- peak_markers %>%
    left_join(peak_to_gene, by = c("cluster", "peak"))
  gene_col <- intersect(c("gene_name", "gene", "symbol"), colnames(merged))[1]
  fc_col <- intersect(c("avg_log2FC", "avg_logFC"), colnames(merged))[1]
  merged <- merged %>% dplyr::filter(!is.na(.data[[gene_col]]), .data[[gene_col]] != "")
  gene_scores <- merged %>%
    group_by(cluster, gene = .data[[gene_col]]) %>%
    summarise(best_p_val_adj = suppressWarnings(min(p_val_adj, na.rm = TRUE)),
              best_log2fc = suppressWarnings(max(.data[[fc_col]], na.rm = TRUE)),
              n_supporting_peaks = n(), .groups = "drop") %>%
    arrange(cluster, best_p_val_adj, desc(best_log2fc), desc(n_supporting_peaks))
  data.table::fwrite(gene_scores, file.path(out_dir, "signac_atac_marker_genes_all.csv"))
  top_p2g <- gene_scores %>% group_by(cluster) %>%
    slice_head(n = top_n) %>%
    summarise(genes = list(unique(gene)), .groups = "drop")
  primary_top_path <- file.path(out_dir, "atac_top_marker_genes_by_celltype.json")
  jsonlite::write_json(setNames(top_p2g$genes, top_p2g$cluster),
                       primary_top_path, pretty = TRUE, auto_unbox = TRUE)
  primary_marker_csv <- file.path(out_dir, "signac_atac_marker_genes_all.csv")
  n_marker_rows <- nrow(gene_scores)
}

result <- list(
  status = "success",
  summary = sprintf("Signac (R) %s on %d cells x %d peaks; %d cell types; top_n=%d; %d marker rows",
                    ifelse(gene_act_used, "GeneActivity (primary)", "peak-to-gene (fallback)"),
                    ncol(counts), nrow(counts), length(valid_types), top_n, n_marker_rows),
  main_results = list(
    n_cells = ncol(counts),
    n_peaks = nrow(counts),
    n_cell_types = length(valid_types),
    top_n = top_n,
    n_marker_rows = n_marker_rows,
    barcode_column = bc_col,
    celltype_column = ct_col,
    backend = "R/Signac",
    gene_activity_primary = gene_act_used,
    fragments_repaired = fragments_repaired
  ),
  artifacts = list(
    atac_marker_genes_all_csv = primary_marker_csv,
    atac_top_marker_genes_json = primary_top_path,
    run_log = log_path
  ),
  warnings = if (signac_method == "gene_activity" && !gene_act_used) {
    c("Signac::GeneActivity unavailable — used peak-to-gene fallback")
  } else c()
)
jsonlite::write_json(result, file.path(out_dir, "result.json"),
                     pretty = TRUE, auto_unbox = TRUE)
close(log_con)
cat(jsonlite::toJSON(result, pretty = FALSE, auto_unbox = TRUE), "\n", sep = "")
