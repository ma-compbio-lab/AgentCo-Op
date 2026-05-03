#!/usr/bin/env bash
# AgentCo-Op CS2 (SHARE-seq mouse skin multiome) — end-to-end setup.
#
# Idempotent. Runs everything the case-study Path C run needs that
# `agentcoop collaborate` doesn't do itself:
#
#   1. download SHARE-seq RNA + ATAC + barcodes + peaks + celltype + fragments BED
#      from GEO (skips files that already exist).
#   2. download PanglaoDB mouse markers (with fallback to the bundled
#      external/SpatialAgent cache if the live URL 403s).
#   3. ensure CellMarker 2.0 mouse marker xlsx is in place
#      (relies on prior session's cache; warns if missing).
#   4. convert the dense gzipped RNA TSV → 10X-style sparse MTX trio
#      (skips if the trio already exists).
#   5. assemble the ATAC MTX trio from the GEO-provided sparse counts
#      (the .txt.gz IS already MatrixMarket — we just rename + supply
#      features.tsv.gz from peaks.bed.gz and barcodes.tsv.gz from the
#      raw barcodes file).
#   6. sort, bgzip, tabix-index the fragments BED so Signac::GeneActivity
#      can stream-read it.
#   7. build the agentcoop-runtime + agentcoop-r-runtime Docker images
#      (skips if cached; takes ~50 min on a fresh aarch64 host).
#
# After this script exits the user can launch the end-to-end run with
# nothing more than:
#
#   set -a; source .secrets/api-key; set +a
#   export AGENTCOOP_BRANCH_CONCURRENCY=1
#   python -m agentcoop.cli collaborate \
#       --request case_study_2.request.yaml \
#       --workdir runs/case2/shareseq_skin_docker_c \
#       --docker
#
# Requirements (set up once on the host):
#   - colima with at least 14 GB memory + 6 CPU + 120 GB disk
#     (Signac::GeneActivity peaks at ~12 GB on the 32 k-cell × 140 M-fragment
#     SHARE-seq dataset; 12 GB OOMs at the "Extracting reads overlapping
#     genomic regions" step):
#         colima start --runtime docker --cpu 6 --memory 14 --disk 120
#   - bgzip + tabix in $PATH:   brew install htslib
#   - GNU sort (gsort):         brew install coreutils
#   - Python with numpy/scipy/pandas (the agentcoop venv covers this)
#   - .secrets/api-key with OPENAI_API_KEY=...
#
# All paths are relative to the AgentCo-Op repo root; run from there:
#   bash scripts/cs2_setup.sh

set -euo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

DATA=data/shareseq_skin
PDB=data/panglaodb
mkdir -p "$DATA" "$PDB"

GEO_BASE="https://www.ncbi.nlm.nih.gov/geo/download"
RNA_GSM=GSM4156608
ATAC_GSM=GSM4156597

dl() {
  # dl <out_path> <url>
  local out="$1"; local url="$2"
  if [[ -s "$out" ]]; then
    echo "  [skip] $out (already $(du -h "$out" | cut -f1))"
    return 0
  fi
  echo "  [dl]   $out"
  wget -q --show-progress -O "$out" "$url" || { rm -f "$out"; echo "ERR download $url"; exit 1; }
}

step() { echo; echo "=== $* ==="; }

step "1) GEO files (downloads run in parallel; each skips if already on disk)"
# Downloads are independent — fan out and `wait`. The 5 GB fragments BED is
# the long pole; the 5 small files together (~440 MB) finish well before it,
# so parallelising shaves a few minutes off the cold-start path.
PIDS=()
dl "$DATA/${RNA_GSM}_skin.late.anagen.rna.counts.txt.gz" \
   "${GEO_BASE}/?acc=${RNA_GSM}&file=${RNA_GSM}_skin.late.anagen.rna.counts.txt.gz&format=file" & PIDS+=($!)
dl "$DATA/${ATAC_GSM}_skin.late.anagen.counts.txt.gz" \
   "${GEO_BASE}/?acc=${ATAC_GSM}&file=${ATAC_GSM}_skin.late.anagen.counts.txt.gz&format=file" & PIDS+=($!)
dl "$DATA/${ATAC_GSM}_skin.late.anagen.peaks.bed.gz" \
   "${GEO_BASE}/?acc=${ATAC_GSM}&file=${ATAC_GSM}_skin.late.anagen.peaks.bed.gz&format=file" & PIDS+=($!)
dl "$DATA/${ATAC_GSM}_skin.late.anagen.barcodes.txt.gz" \
   "${GEO_BASE}/?acc=${ATAC_GSM}&file=${ATAC_GSM}_skin.late.anagen.barcodes.txt.gz&format=file" & PIDS+=($!)
dl "$DATA/${ATAC_GSM}_skin_celltype.txt.gz" \
   "${GEO_BASE}/?acc=${ATAC_GSM}&file=${ATAC_GSM}_skin_celltype.txt.gz&format=file" & PIDS+=($!)
dl "$DATA/${ATAC_GSM}_skin.late.anagen.atac.fragments.bed.gz" \
   "${GEO_BASE}/?acc=${ATAC_GSM}&file=${ATAC_GSM}_skin.late.anagen.atac.fragments.bed.gz&format=file" & PIDS+=($!)
fail=0
for pid in "${PIDS[@]}"; do wait "$pid" || fail=1; done
[[ $fail -eq 0 ]] || { echo "ERR one or more GEO downloads failed; check above"; exit 1; }

step "2) PanglaoDB markers"
PDB_TSV="$PDB/PanglaoDB_markers_27_Mar_2020.tsv"
PDB_FALLBACK=external/SpatialAgent/data/PanglaoDB_markers_27_Mar_2020.tsv
if [[ -s "$PDB_TSV" ]]; then
  echo "  [skip] $PDB_TSV"
elif [[ -s "$PDB_FALLBACK" ]]; then
  echo "  [link] $PDB_FALLBACK -> $PDB_TSV"
  cp "$PDB_FALLBACK" "$PDB_TSV"
else
  echo "  [dl]   $PDB_TSV"
  curl -L -A "Mozilla/5.0" -e "https://panglaodb.se/markers.html" \
       -o "$PDB_TSV.gz" \
       "https://panglaodb.se/markers/PanglaoDB_markers_27_Mar_2020.tsv.gz"
  gunzip -f "$PDB_TSV.gz"
fi

step "3) CellMarker 2.0"
CM="$DATA/Cell_marker_Mouse.xlsx"
if [[ ! -s "$CM" ]]; then
  echo "WARNING: CellMarker file missing at $CM"
  echo "  Download from http://www.bio-bigdata.center/CellMarker_download.html"
  echo "  (Mouse cell markers .xlsx) and place at $CM"
  echo "  Path C will still run but CellMarker eval will degrade to PanglaoDB-only."
else
  echo "  [skip] $CM"
fi

step "4) RNA dense TSV → sparse MTX trio"
if [[ -s "$DATA/rna_mtx/matrix.mtx.gz" ]] && [[ -s "$DATA/rna_mtx/features.tsv.gz" ]] && [[ -s "$DATA/rna_mtx/barcodes.tsv.gz" ]]; then
  echo "  [skip] $DATA/rna_mtx"
else
  python scripts/dense_tsv_to_mtx.py \
    "$DATA/${RNA_GSM}_skin.late.anagen.rna.counts.txt.gz" \
    "$DATA/rna_mtx"
fi

step "5) ATAC MTX trio (already MTX format from GEO)"
mkdir -p "$DATA/atac_mtx"
if [[ -s "$DATA/atac_mtx/matrix.mtx.gz" ]] && [[ -s "$DATA/atac_mtx/features.tsv.gz" ]] && [[ -s "$DATA/atac_mtx/barcodes.tsv.gz" ]]; then
  echo "  [skip] $DATA/atac_mtx"
else
  cp "$DATA/${ATAC_GSM}_skin.late.anagen.counts.txt.gz" "$DATA/atac_mtx/matrix.mtx.gz"
  gzip -dc "$DATA/${ATAC_GSM}_skin.late.anagen.peaks.bed.gz" \
    | awk -v OFS='\t' '{name=$1"-"$2"-"$3; print name, name, "Peaks"}' \
    | gzip > "$DATA/atac_mtx/features.tsv.gz"
  cp "$DATA/${ATAC_GSM}_skin.late.anagen.barcodes.txt.gz" "$DATA/atac_mtx/barcodes.tsv.gz"
  echo "  [done] $DATA/atac_mtx (3 files)"
fi

step "6) Fragments BED → sorted bgzip + tabix index"
FRAG_BGZ="$DATA/${ATAC_GSM}_skin.late.anagen.atac.fragments.tsv.bgz"
if [[ -s "$FRAG_BGZ" ]] && [[ -s "$FRAG_BGZ.tbi" ]]; then
  echo "  [skip] $FRAG_BGZ"
else
  command -v bgzip  >/dev/null || { echo "ERR: install htslib (brew install htslib)"; exit 1; }
  command -v tabix  >/dev/null || { echo "ERR: install htslib (brew install htslib)"; exit 1; }
  command -v gsort  >/dev/null || { echo "ERR: install GNU coreutils (brew install coreutils)"; exit 1; }
  echo "  [run]  gsort | bgzip (this takes ~20 min on aarch64 for the 5 GB BED)"
  # NOTE: column 4 is the cell barcode. The GEO BED uses comma-separated
  # tokens (R1.52,R2.48,R3.53,P1.05), but the SHARE-seq barcodes file
  # (used by atac_mtx/barcodes.tsv.gz) uses dot-separated tokens
  # (R1.52.R2.48.R3.53.P1.05). Signac::CreateFragmentObject validates
  # exact-match between cell ids and fragment-file barcodes, so we
  # normalise commas → dots here once.
  gzip -dc "$DATA/${ATAC_GSM}_skin.late.anagen.atac.fragments.bed.gz" \
    | awk 'BEGIN{OFS="\t"} NF>=4 {gsub(",", ".", $4); print $1, $2, $3, $4, 1}' \
    | gsort -k1,1 -k2,2n -S 6G --parallel=8 -T /tmp \
    | bgzip -@ 4 > "$FRAG_BGZ"
  tabix -p bed "$FRAG_BGZ"
  echo "  [done] $(ls -lh "$FRAG_BGZ" "$FRAG_BGZ.tbi")"
fi

step "7) Docker images (Python runtime + R/Bioconductor runtime)"
if docker image inspect agentcoop-runtime:case-study >/dev/null 2>&1; then
  echo "  [skip] agentcoop-runtime:case-study"
else
  echo "  [build] agentcoop-runtime:case-study (~2 min)"
  docker build -f docker/agentcoop-runtime.Dockerfile -t agentcoop-runtime:case-study .
fi
if docker image inspect agentcoop-r-runtime:case-study >/dev/null 2>&1; then
  echo "  [skip] agentcoop-r-runtime:case-study"
else
  echo "  [build] agentcoop-r-runtime:case-study (~50 min on aarch64; rocker/r-ver:4.3.3 + Bioc + Seurat + Signac)"
  docker build -f docker/agentcoop-r-runtime.Dockerfile -t agentcoop-r-runtime:case-study .
fi

step "DONE"
echo
echo "Now launch the end-to-end run with:"
echo
echo "  set -a; source .secrets/api-key; set +a"
echo "  export AGENTCOOP_BRANCH_CONCURRENCY=1   # serialise R Seurat + R Signac on a 12 GB colima"
echo "  python -m agentcoop.cli collaborate \\"
echo "      --request case_study_2.request.yaml \\"
echo "      --workdir runs/case2/shareseq_skin_docker_c \\"
echo "      --docker"
echo
echo "Expected wall time: ~50 min (Seurat 12-15 min, Signac::GeneActivity 25-35 min, integrator 1-2 min)."
