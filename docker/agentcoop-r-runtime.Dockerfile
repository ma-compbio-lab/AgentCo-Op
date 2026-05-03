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

# Copy the R wrappers in.
WORKDIR /workspace
COPY agentcoop/wrappers/seurat_local/seurat_rna_marker_agent.R /workspace/agentcoop/wrappers/seurat_local/seurat_rna_marker_agent.R
COPY agentcoop/wrappers/signac_local/signac_atac_marker_agent.R /workspace/agentcoop/wrappers/signac_local/signac_atac_marker_agent.R

# Dispatcher mirrors the agentcoop-runtime entrypoint contract:
# `agent_name <invoke.json>` → routes to the correct Rscript.
COPY docker/agentcoop-r-dispatch.sh /usr/local/bin/agentcoop-r-dispatch
RUN chmod +x /usr/local/bin/agentcoop-r-dispatch

ENTRYPOINT ["/usr/local/bin/agentcoop-r-dispatch"]
