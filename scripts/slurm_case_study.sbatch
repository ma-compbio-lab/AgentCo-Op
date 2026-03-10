#!/bin/bash
# Submit from repo root:
#   sbatch scripts/slurm_case_study.sbatch
#
# Useful overrides:
#   CASE_STUDY_EXPERIMENT=scanpy_pbmc3k_case sbatch scripts/slurm_case_study.sbatch
#   CASE_STUDY_EXPERIMENT=squidpy_visium_case CASE_STUDY_MODEL=openai_gpt5_mini sbatch scripts/slurm_case_study.sbatch
#   CASE_STUDY_BASE_DIR=runs/case-study-archive sbatch scripts/slurm_case_study.sbatch

#SBATCH -J case-study
#SBATCH -p cpu
#SBATCH --cpus-per-task=2
#SBATCH --time=02:00:00

set -euo pipefail

REPO_ROOT="${SLURM_SUBMIT_DIR:-$(pwd)}"
cd "${REPO_ROOT}"

mkdir -p runs/slurm

EXPERIMENT="${CASE_STUDY_EXPERIMENT:-scanpy_pbmc3k_case}"
MODEL="${CASE_STUDY_MODEL:-openai_gpt5_mini}"
BASE_DIR="${CASE_STUDY_BASE_DIR:-runs}"

if [[ ! -x ".venv/bin/python" ]]; then
  echo "Missing project virtualenv at .venv/bin/python" >&2
  exit 1
fi

echo "Running case study experiment=${EXPERIMENT} model=${MODEL} base_dir=${BASE_DIR}"

.venv/bin/python -m dynaforge.experiment_cli case-study \
  --experiment "${EXPERIMENT}" \
  --base-dir "${BASE_DIR}" \
  --model "${MODEL}"
