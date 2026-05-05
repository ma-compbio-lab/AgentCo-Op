# Combined R/Bioconductor runtime for the CS2 Seurat × Signac case study.
# A single image hosts both R wrappers, dispatched by `agent_name` at
# `docker run` time so the orchestrator's invocation contract stays
# identical to the Python runtime image.
#
# Build context: repo root.  Build cost: 30-60 min on aarch64 (CRAN +
# Bioconductor compile from source).
FROM rocker/r-ver:4.3.3

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        git wget curl ca-certificates build-essential \
        libcurl4-openssl-dev libssl-dev libxml2-dev \
        libhdf5-dev libz-dev libbz2-dev liblzma-dev \
        libpng-dev libjpeg-dev libfreetype6-dev libfontconfig1-dev \
        libtiff5-dev libcairo2-dev libxt-dev \
        zlib1g-dev libglpk-dev libgmp-dev libmpfr-dev \
        libgit2-dev pandoc \
    && rm -rf /var/lib/apt/lists/*

# Use Posit's binary repo to avoid recompiling everything from source on
# aarch64. Falls back to CRAN source if a binary is unavailable for the
# host architecture.
RUN R -e "options(repos = c(P3M = 'https://packagemanager.posit.co/cran/__linux__/jammy/latest', CRAN = 'https://cloud.r-project.org')); install.packages(c('remotes','BiocManager','data.table','Matrix','jsonlite','dplyr','ggplot2','Rcpp','RcppEigen','RcppArmadillo'))"

# Bioconductor packages needed by Signac's GeneActivity + closest-feature
# paths. Pinning Bioc 3.18 (matches R 4.3).
RUN R -e "BiocManager::install(version='3.18', ask=FALSE, update=FALSE)" \
 && R -e "BiocManager::install(c('GenomicRanges','IRanges','GenomeInfoDb','Rsamtools','EnsDb.Mmusculus.v79','org.Mm.eg.db'), ask=FALSE, update=FALSE, version='3.18')"

# Seurat + Signac. Both come from CRAN; Posit's binary repo serves them.
RUN R -e "install.packages(c('Seurat','Signac'))"

# R.utils is required by data.table::fread to transparently read .gz
# inputs (the Seurat/Signac wrappers do this); add it (and a small set
# of likely-needed extras) in a leaf layer so adding/removing them
# doesn't invalidate the heavy Bioc / CRAN layers above. biovizBase is
# pulled in by Signac's GetGRangesFromEnsDb path.
RUN R -e "options(repos = c(P3M = 'https://packagemanager.posit.co/cran/__linux__/jammy/latest', CRAN = 'https://cloud.r-project.org')); install.packages(c('R.utils','tidyr'))" \
 && R -e "BiocManager::install(c('biovizBase'), ask=FALSE, update=FALSE, version='3.18')" \
 && R -e "remotes::install_github('immunogenomics/presto', upgrade='never')"

# Sanity: confirm packages load + EnsDb GR path works.
RUN R -e "suppressPackageStartupMessages({library(Seurat); library(Signac); library(EnsDb.Mmusculus.v79); library(biovizBase); library(R.utils)}); gr <- Signac::GetGRangesFromEnsDb(ensdb = EnsDb.Mmusculus.v79); cat('R packages OK; EnsDb GR length=', length(gr), '\n')"

# Human (hg38) annotation packages, added in a leaf layer so the heavy
# CRAN + mm10 layers above stay cached. Required by CS2 human-heart
# variant (case_study_2_human_heart.md). Same image serves both mouse
# and human variants — wrappers select annotation by genome parameter.
RUN R -e "BiocManager::install(c('EnsDb.Hsapiens.v86','BSgenome.Hsapiens.UCSC.hg38','org.Hs.eg.db'), ask=FALSE, update=FALSE, version='3.18')" \
 && R -e "suppressPackageStartupMessages({library(EnsDb.Hsapiens.v86); library(BSgenome.Hsapiens.UCSC.hg38)}); gr <- Signac::GetGRangesFromEnsDb(ensdb = EnsDb.Hsapiens.v86); cat('hg38 EnsDb GR length=', length(gr), '\n')"

# hdf5r — required by Seurat::Read10X_h5 to load 10x H5 multiome files.
# Used by both Seurat and Signac wrappers when dataset.format=tenx_h5_multiome.
RUN R -e "options(repos = c(P3M = 'https://packagemanager.posit.co/cran/__linux__/jammy/latest', CRAN = 'https://cloud.r-project.org')); install.packages('hdf5r')" \
 && R -e "suppressPackageStartupMessages(library(hdf5r)); cat('hdf5r OK\n')"

# SingleR + celldex — fallback annotation path used by the CS2-Lite
# annotation script when the Hao Zenodo reference is unavailable.
RUN R -e "BiocManager::install(c('SingleR','celldex','SummarizedExperiment','SingleCellExperiment'), ask=FALSE, update=FALSE, version='3.18')" \
 && R -e "suppressPackageStartupMessages({library(SingleR); library(celldex)}); cat('SingleR + celldex OK\n')"

# NOTE: glmGamPoi (the fast SCTransform backend) is not available in
# binary form for R 4.3 / aarch64 and the source build needs a newer
# C++ standard than rocker/r-ver:4.3.3 ships. The CS2-Lite annotation
# script handles this by stratified downsampling to 5,000 cells before
# SCTransform (spec §15.3 fallback) — avoids the SCT memory blow-up
# without changing the methodology.

# Copy the R wrappers in.
WORKDIR /workspace
COPY agentcoop/wrappers/seurat_local/seurat_rna_marker_agent.R /workspace/agentcoop/wrappers/seurat_local/seurat_rna_marker_agent.R
COPY agentcoop/wrappers/signac_local/signac_atac_marker_agent.R /workspace/agentcoop/wrappers/signac_local/signac_atac_marker_agent.R

# Dispatcher mirrors the agentcoop-runtime entrypoint contract:
# `agent_name <invoke.json>` → routes to the correct Rscript.
COPY docker/agentcoop-r-dispatch.sh /usr/local/bin/agentcoop-r-dispatch
RUN chmod +x /usr/local/bin/agentcoop-r-dispatch

ENTRYPOINT ["/usr/local/bin/agentcoop-r-dispatch"]
