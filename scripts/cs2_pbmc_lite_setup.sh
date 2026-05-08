#!/usr/bin/env bash
# AgentCo-Op CS2 Lite (10x PBMC multiome — granulocytes-sorted 10k)
# end-to-end setup. See `docs/experiments/case_study_2_lite.md` for the design.
#
# Idempotent. Runs everything the case-study Docker run needs that
# `agentcoop collaborate` doesn't do itself:
#
#   1. download the 10x PBMC multiome H5 + ATAC fragments + .tbi index.
#   2. download the Hao et al. PBMC multimodal reference RDS.
#   3. download CellMarker 2.0 + PanglaoDB markers (with bundled fallbacks).
#   4. run the label-transfer annotation R script inside the
#      agentcoop-r-runtime image to produce a metadata CSV with
#      predicted celltype.l2 labels (this replaces the
#      author-provided metadata that other CS2 variants have).
#   5. verify the agentcoop-r-runtime + agentcoop-runtime images are
#      present (rebuilds if missing).
#
# After this script exits, launch the end-to-end run with:
#
#   set -a; source .secrets/api-key; set +a
#   export AGENTCOOP_BRANCH_CONCURRENCY=1
#   python -m agentcoop.cli collaborate \
#       --request case_study_2_lite.request.yaml \
#       --workdir runs/case2/pbmc_lite_docker \
#       --docker
#
# Requirements (set up once on the host):
#   - colima with at least 14 GB memory + 6 CPU + 120 GB disk.
#   - Docker images already built by Session 13's CS2-heart setup
#     (this script does NOT rebuild them; it just verifies presence).
#   - .secrets/api-key with OPENAI_API_KEY=...
#
# All paths are relative to the repo root.

set -euo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

DATA=data/pbmc_lite
PDB=data/panglaodb
mkdir -p "$DATA" "$PDB"

dl() {
  # dl <out_path> <url>
  local out="$1"; local url="$2"
  if [[ -s "$out" ]]; then
    echo "  [skip] $out (already $(du -h "$out" | cut -f1))"
    return 0
  fi
  echo "  [dl]   $out"
  curl -L --retry 5 --retry-delay 10 -A "Mozilla/5.0" -o "$out" "$url" \
    || { rm -f "$out"; echo "ERR download $url"; exit 1; }
}

step() { echo; echo "=== $* ==="; }

step "1) 10x Genomics PBMC multiome (H5 + ATAC fragments + tabix index)"
TENX_BASE="https://cf.10xgenomics.com/samples/cell-arc/1.0.0/pbmc_granulocyte_sorted_10k"
PIDS=()
dl "$DATA/pbmc_granulocyte_sorted_10k_filtered_feature_bc_matrix.h5" \
   "${TENX_BASE}/pbmc_granulocyte_sorted_10k_filtered_feature_bc_matrix.h5" & PIDS+=($!)
dl "$DATA/pbmc_granulocyte_sorted_10k_atac_fragments.tsv.gz" \
   "${TENX_BASE}/pbmc_granulocyte_sorted_10k_atac_fragments.tsv.gz" & PIDS+=($!)
dl "$DATA/pbmc_granulocyte_sorted_10k_atac_fragments.tsv.gz.tbi" \
   "${TENX_BASE}/pbmc_granulocyte_sorted_10k_atac_fragments.tsv.gz.tbi" & PIDS+=($!)
fail=0
for pid in "${PIDS[@]}"; do wait "$pid" || fail=1; done
[[ $fail -eq 0 ]] || { echo "ERR one or more 10x downloads failed"; exit 1; }

step "2) Hao et al. PBMC multimodal reference (~1.9 GB; spec §3.4 primary)"
# The annotation script's PRIMARY path uses Seurat label transfer from
# this RDS (celltype.l2). If the download is rate-limited (Zenodo
# returns HTTP 403 "unusual traffic" intermittently), the annotation
# script falls back to SingleR + celldex::MonacoImmuneData and clearly
# logs the substitution in metadata.summary.json.
HAO="$DATA/pbmc_multimodal_2023.rds"
HAO_URL="https://zenodo.org/records/7779017/files/pbmc_multimodal_2023.rds"
if [[ -s "$HAO" ]] && [[ $(stat -f%z "$HAO" 2>/dev/null || stat -c%s "$HAO") -ge 1000000000 ]]; then
  echo "  [skip] $HAO (already $(du -h "$HAO" | cut -f1))"
else
  rm -f "$HAO"
  echo "  [dl]   $HAO (1.9 GB, ~3-5 min on a fast link)"
  # Use the wget UA (Zenodo's CDN distinguishes; Mozilla UA gets 403
  # more often than wget). --retry covers transient cloudfront blips.
  curl -L -A "wget/1.21" --retry 5 --retry-delay 30 --max-time 1800 -o "$HAO" "$HAO_URL" \
    || { rm -f "$HAO"; echo "  [warn] Hao download failed; annotation will fall back to SingleR+Monaco"; }
fi

step "3) CellMarker 2.0 + PanglaoDB"
CM="$DATA/Cell_marker_All.xlsx"
# Reuse the heart variant's xlsx if already cached (same file).
if [[ ! -s "$CM" ]] && [[ -s "data/heart_human/Cell_marker_All.xlsx" ]]; then
  cp "data/heart_human/Cell_marker_All.xlsx" "$CM"
  echo "  [link] data/heart_human/Cell_marker_All.xlsx → $CM"
fi
dl "$CM" "https://bio-bigdata.hrbmu.edu.cn/CellMarker/CellMarker_download_files/file/Cell_marker_All.xlsx"

PDB_TSV="$PDB/PanglaoDB_markers_27_Mar_2020.tsv"
PDB_FALLBACK=external/SpatialAgent/data/PanglaoDB_markers_27_Mar_2020.tsv
if [[ -s "$PDB_TSV" ]]; then
  echo "  [skip] $PDB_TSV"
elif [[ -s "$PDB_FALLBACK" ]]; then
  cp "$PDB_FALLBACK" "$PDB_TSV"; echo "  [link] $PDB_FALLBACK → $PDB_TSV"
else
  dl "$PDB_TSV.gz" "https://panglaodb.se/markers/PanglaoDB_markers_27_Mar_2020.tsv.gz"
  gunzip -f "$PDB_TSV.gz"
fi

step "4) Tabix index sanity"
TBI="$DATA/pbmc_granulocyte_sorted_10k_atac_fragments.tsv.gz.tbi"
if [[ ! -s "$TBI" ]]; then
  echo "ERR tabix index missing at $TBI (10x usually ships it)"; exit 1
fi
echo "  [ok] $(du -h "$TBI" | cut -f1) tabix index"

step "5) Verify Docker images"
if ! docker image inspect agentcoop-r-runtime:case-study >/dev/null 2>&1; then
  echo "  [build] agentcoop-r-runtime:case-study (one-time, ~50 min on aarch64)"
  docker build -f docker/agentcoop-r-runtime.Dockerfile -t agentcoop-r-runtime:case-study .
else
  if ! docker run --rm --entrypoint Rscript agentcoop-r-runtime:case-study \
       -e "suppressPackageStartupMessages({library(EnsDb.Hsapiens.v86); library(hdf5r)})" >/dev/null 2>&1; then
    echo "  [rebuild] agentcoop-r-runtime:case-study (hg38 / hdf5r missing)"
    docker build -f docker/agentcoop-r-runtime.Dockerfile -t agentcoop-r-runtime:case-study .
  else
    echo "  [skip] agentcoop-r-runtime:case-study (hg38 + hdf5r verified)"
  fi
fi
if ! docker image inspect agentcoop-runtime:case-study >/dev/null 2>&1; then
  echo "  [build] agentcoop-runtime:case-study"
  docker build -f docker/agentcoop-runtime.Dockerfile -t agentcoop-runtime:case-study .
else
  echo "  [skip] agentcoop-runtime:case-study"
fi

step "6) Run PBMC cell-type annotation (Hao label transfer; SingleR+Monaco fallback)"
META="$DATA/pbmc_predicted_celltype_metadata.csv.gz"
if [[ -s "$META" ]]; then
  echo "  [skip] $META (already $(du -h "$META" | cut -f1))"
else
  HAO_REF="/workspace/data/pbmc_lite/pbmc_multimodal_2023.rds"
  echo "  [run]  cs2_pbmc_lite_annotation.R PRIMARY path (Hao + celltype.l2)"
  ANN_OK=0
  docker run --rm \
    -v "$PWD:/workspace" \
    --entrypoint Rscript \
    agentcoop-r-runtime:case-study \
    /workspace/scripts/cs2_pbmc_lite_annotation.R \
    /workspace/data/pbmc_lite/pbmc_granulocyte_sorted_10k_filtered_feature_bc_matrix.h5 \
    /workspace/data/pbmc_lite/pbmc_granulocyte_sorted_10k_atac_fragments.tsv.gz \
    "$HAO_REF" \
    /workspace/data/pbmc_lite/pbmc_predicted_celltype_metadata.csv.gz \
    PBMC10K \
    && ANN_OK=1 || ANN_OK=0
  if [[ $ANN_OK -eq 0 ]] || [[ ! -s "$META" ]]; then
    rm -f "$META" "$DATA/pbmc_predicted_celltype_metadata.summary.json"
    echo "  [warn] Hao path failed (exit non-zero or no output) — likely OOM on $(docker info 2>/dev/null | awk '/Total Memory/ {print $3}') host"
    echo "  [retry] cs2_pbmc_lite_annotation.R FALLBACK path (SingleR + celldex::MonacoImmuneData; spec §15.3)"
    docker run --rm \
      -v "$PWD:/workspace" \
      --entrypoint Rscript \
      agentcoop-r-runtime:case-study \
      /workspace/scripts/cs2_pbmc_lite_annotation.R \
      /workspace/data/pbmc_lite/pbmc_granulocyte_sorted_10k_filtered_feature_bc_matrix.h5 \
      /workspace/data/pbmc_lite/pbmc_granulocyte_sorted_10k_atac_fragments.tsv.gz \
      /workspace/data/pbmc_lite/_force_fallback_no_hao.rds \
      /workspace/data/pbmc_lite/pbmc_predicted_celltype_metadata.csv.gz \
      PBMC10K
  fi
fi

step "7) Checksums"
(cd "$DATA" && sha256sum *.h5 *.tsv.gz *.tsv.gz.tbi *.rds *.xlsx *.csv.gz > checksums.sha256 2>/dev/null) || true
echo "  [done] $DATA/checksums.sha256"

step "DONE"
echo
echo "Now launch the end-to-end run with:"
echo
echo "  set -a; source .secrets/api-key; set +a"
echo "  export AGENTCOOP_BRANCH_CONCURRENCY=1"
echo "  python -m agentcoop.cli collaborate \\"
echo "      --request case_study_2_lite.request.yaml \\"
echo "      --workdir runs/case2/pbmc_lite_docker \\"
echo "      --docker"
echo
echo "Expected wall time for the pipeline (steady-state, no rebuild/redownload):"
echo "  Seurat::FindAllMarkers:                ~3-5 min"
echo "  Signac::GeneActivity + FindAllMarkers: ~10-20 min on ~10k cells"
echo "  CellMarker + PanglaoDB evaluator:      ~30 s"
echo "  Total:                                 ~15-25 min"
