#!/usr/bin/env Rscript
# Case Study 1: bulk RNA-seq differential expression on the Bioconductor
# `airway` dataset per docs/experiments/case_study.md §2.5.
#
# Usage:
#   Rscript scripts/case1_airway_de.R [output_dir]
#
# Output (under output_dir, default = artifacts/case1/):
#   - counts.tsv
#   - metadata.tsv
#   - de_results.tsv  (DESeq2 with apeglm shrinkage + HGNC symbols)
#   - session_info.txt

suppressPackageStartupMessages({
  req <- function(pkg) {
    if (!requireNamespace(pkg, quietly = TRUE)) {
      stop(sprintf(
        "Missing R package: %s. Install via BiocManager::install('%s').", pkg, pkg
      ))
    }
  }
  for (pkg in c("airway", "DESeq2", "apeglm", "org.Hs.eg.db", "AnnotationDbi")) {
    req(pkg)
  }
  library(airway)
  library(DESeq2)
  library(apeglm)
  library(org.Hs.eg.db)
  library(AnnotationDbi)
})

args <- commandArgs(trailingOnly = TRUE)
out_dir <- if (length(args) >= 1) args[[1]] else "artifacts/case1"
dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)

data(airway)
se <- airway
se$cell <- factor(se$cell)
se$dex <- relevel(factor(se$dex), ref = "untrt")

write.table(
  as.data.frame(assay(se, "counts"))[1:100, , drop = FALSE],
  file = file.path(out_dir, "counts_head.tsv"),
  sep = "\t", quote = FALSE, row.names = TRUE
)
write.table(
  as.data.frame(colData(se)),
  file = file.path(out_dir, "metadata.tsv"),
  sep = "\t", quote = FALSE, row.names = TRUE
)

dds <- DESeqDataSet(se, design = ~ cell + dex)
dds <- dds[rowSums(counts(dds)) >= 10, ]
dds <- DESeq(dds)

res <- results(dds, contrast = c("dex", "trt", "untrt"))
res <- lfcShrink(dds, coef = "dex_trt_vs_untrt", type = "apeglm")
res_df <- as.data.frame(res)
res_df$ensembl_id <- rownames(res_df)
res_df$symbol <- mapIds(
  org.Hs.eg.db,
  keys = res_df$ensembl_id,
  column = "SYMBOL",
  keytype = "ENSEMBL",
  multiVals = "first"
)
res_df <- res_df[
  order(res_df$padj, na.last = TRUE),
  c("ensembl_id", "symbol", "log2FoldChange", "pvalue", "padj", "baseMean")
]
write.table(
  res_df,
  file = file.path(out_dir, "de_results.tsv"),
  sep = "\t", quote = FALSE, row.names = FALSE
)

writeLines(capture.output(sessionInfo()), file.path(out_dir, "session_info.txt"))
cat(sprintf("[case1] wrote DE results with %d rows → %s\n",
            nrow(res_df), file.path(out_dir, "de_results.tsv")))
