#!/usr/bin/env bash
# AgentCo-Op CS2 (human heart 10x multiome — GSE270788 MA7) end-to-end
# setup. See `docs/experiments/case_study_2_human_heart.md` for the design.
#
# Idempotent. Runs everything the case-study Docker run needs that
# `agentcoop collaborate` doesn't do itself:
#
#   1. download GSE270788 MA7 GEX H5 (filtered_feature_bc_matrix.h5),
#      MA7 ATAC fragments TSV.gz, GSE270788 metadata.csv.gz from GEO
#      (skips files already on disk).
#   2. download CellMarker 2.0 all-cell-marker xlsx.
#   3. download PanglaoDB markers TSV (with bundled fallback).
#   4. tabix-index the ATAC fragments file (sort + bgzip first if needed).
#   5. verify the agentcoop-r-runtime + agentcoop-runtime Docker images
#      are present (rebuilds if missing — ~15 min for the hg38 layer).
#
# After this script exits, launch the end-to-end run with:
#
#   set -a; source .secrets/api-key; set +a
#   export AGENTCOOP_BRANCH_CONCURRENCY=1
#   python -m agentcoop.cli collaborate \
#       --request case_study_2_human_heart.request.yaml \
#       --workdir runs/case2/human_heart_ma7_docker \
#       --docker
#
# Requirements (set up once on the host):
#   - colima with at least 14 GB memory + 6 CPU + 120 GB disk:
#         colima start --runtime docker --cpu 6 --memory 14 --disk 120
#   - bgzip + tabix:   brew install htslib
#   - GNU sort (gsort): brew install coreutils
#   - .secrets/api-key with OPENAI_API_KEY=...
#
# All paths are relative to the repo root; run from there:
#   bash scripts/cs2_human_heart_setup.sh

set -euo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

DATA=data/heart_human
PDB=data/panglaodb
MA7="$DATA/MA7"
mkdir -p "$MA7" "$PDB"

GEO_BASE="https://www.ncbi.nlm.nih.gov/geo/download"
GEX_GSM=GSM8352050
ATAC_GSM=GSM8352073
SERIES=GSE270788

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

step "1) GSE270788 MA7 GEX H5 + ATAC fragments + metadata (parallel)"
PIDS=()
dl "$MA7/${GEX_GSM}_MA7_filtered_feature_bc_matrix.h5" \
   "${GEO_BASE}/?acc=${GEX_GSM}&file=${GEX_GSM}_MA7_filtered_feature_bc_matrix.h5&format=file" & PIDS+=($!)
dl "$MA7/${ATAC_GSM}_MA7_atac_fragments.tsv.gz" \
   "${GEO_BASE}/?acc=${ATAC_GSM}&file=${ATAC_GSM}_MA7_atac_fragments.tsv.gz&format=file" & PIDS+=($!)
dl "$DATA/${SERIES}_metadata.csv.gz" \
   "${GEO_BASE}/?acc=${SERIES}&file=${SERIES}_metadata.csv.gz&format=file" & PIDS+=($!)
fail=0
for pid in "${PIDS[@]}"; do wait "$pid" || fail=1; done
[[ $fail -eq 0 ]] || { echo "ERR one or more GEO downloads failed"; exit 1; }

step "2) CellMarker 2.0 (Cell_marker_All.xlsx)"
CM="$DATA/Cell_marker_All.xlsx"
dl "$CM" "https://bio-bigdata.hrbmu.edu.cn/CellMarker/CellMarker_download_files/file/Cell_marker_All.xlsx"

step "3) PanglaoDB markers"
PDB_TSV="$PDB/PanglaoDB_markers_27_Mar_2020.tsv"
PDB_FALLBACK=external/SpatialAgent/data/PanglaoDB_markers_27_Mar_2020.tsv
if [[ -s "$PDB_TSV" ]]; then
  echo "  [skip] $PDB_TSV"
elif [[ -s "$PDB_FALLBACK" ]]; then
  echo "  [link] $PDB_FALLBACK -> $PDB_TSV"
  cp "$PDB_FALLBACK" "$PDB_TSV"
else
  dl "$PDB_TSV.gz" "https://panglaodb.se/markers/PanglaoDB_markers_27_Mar_2020.tsv.gz"
  gunzip -f "$PDB_TSV.gz"
fi

step "4) Tabix-index ATAC fragments"
RAW_FRAG="$MA7/${ATAC_GSM}_MA7_atac_fragments.tsv.gz"
SORTED_BGZ="$MA7/${ATAC_GSM}_MA7_atac_fragments.sorted.tsv.bgz"
TBI="$SORTED_BGZ.tbi"
if [[ -s "$SORTED_BGZ" ]] && [[ -s "$TBI" ]]; then
  echo "  [skip] $SORTED_BGZ (already indexed)"
else
  command -v bgzip >/dev/null || { echo "ERR install htslib (brew install htslib)"; exit 1; }
  command -v tabix >/dev/null || { echo "ERR install htslib"; exit 1; }
  command -v gsort >/dev/null || { echo "ERR install GNU coreutils (brew install coreutils)"; exit 1; }
  echo "  [run]  fast path: try in-place tabix on the raw GEO file"
  if tabix -p bed "$RAW_FRAG" 2>/dev/null; then
    echo "  [done] tabix-indexed in place: $RAW_FRAG.tbi"
    # Repoint downstream paths to the raw file via symlink for convenience.
    ln -sf "$(basename "$RAW_FRAG")" "$SORTED_BGZ" 2>/dev/null || cp "$RAW_FRAG" "$SORTED_BGZ"
    ln -sf "$(basename "$RAW_FRAG").tbi" "$TBI" 2>/dev/null || cp "$RAW_FRAG.tbi" "$TBI"
  else
    echo "  [fall] raw file not bgzip+sorted; sort → bgzip → tabix"
    gzip -dc "$RAW_FRAG" \
      | grep -v '^#' \
      | gsort -k1,1 -k2,2n -S 4G --parallel=4 -T /tmp \
      | bgzip -@ 4 > "$SORTED_BGZ"
    tabix -p bed "$SORTED_BGZ"
    echo "  [done] $(ls -lh "$SORTED_BGZ" "$TBI" | awk '{print $5,$9}')"
  fi
fi

step "5) Checksums"
(cd "$MA7" && sha256sum * > "../checksums_MA7.sha256" 2>/dev/null) || true
(cd "$DATA" && sha256sum *.csv.gz *.xlsx > "checksums_db.sha256" 2>/dev/null) || true
echo "  [done] checksums_MA7.sha256, checksums_db.sha256"

step "6) Docker images"
if docker image inspect agentcoop-runtime:case-study >/dev/null 2>&1; then
  echo "  [skip] agentcoop-runtime:case-study"
else
  echo "  [build] agentcoop-runtime:case-study"
  docker build -f docker/agentcoop-runtime.Dockerfile -t agentcoop-runtime:case-study .
fi
if docker image inspect agentcoop-r-runtime:case-study >/dev/null 2>&1; then
  # Ensure the image actually has hg38 packages baked in (Session 13 leaf
  # layer). If not, rebuild.
  if docker run --rm --entrypoint Rscript agentcoop-r-runtime:case-study \
       -e "suppressPackageStartupMessages(library(EnsDb.Hsapiens.v86))" >/dev/null 2>&1; then
    echo "  [skip] agentcoop-r-runtime:case-study (hg38 verified)"
  else
    echo "  [rebuild] agentcoop-r-runtime:case-study (hg38 layer missing)"
    docker build -f docker/agentcoop-r-runtime.Dockerfile -t agentcoop-r-runtime:case-study .
  fi
else
  echo "  [build] agentcoop-r-runtime:case-study (~50 min on aarch64)"
  docker build -f docker/agentcoop-r-runtime.Dockerfile -t agentcoop-r-runtime:case-study .
fi

step "DONE"
echo
echo "Now launch the end-to-end run with:"
echo
echo "  set -a; source .secrets/api-key; set +a"
echo "  export AGENTCOOP_BRANCH_CONCURRENCY=1   # serialise R Seurat + R Signac on a 14 GB colima"
echo "  python -m agentcoop.cli collaborate \\"
echo "      --request case_study_2_human_heart.request.yaml \\"
echo "      --workdir runs/case2/human_heart_ma7_docker \\"
echo "      --docker"
echo
echo "Expected wall time on aarch64 / 14 GB colima:"
echo "  Seurat::FindAllMarkers: ~5-12 min"
echo "  Signac::GeneActivity + FindAllMarkers: ~15-30 min"
echo "  Integrator + figures: ~1-2 min"
echo "  Total: ~30-50 min"
