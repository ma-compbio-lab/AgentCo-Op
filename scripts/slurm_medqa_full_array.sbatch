#!/bin/bash
# Submit from repo root:
#   sbatch scripts/slurm_medqa_full_array.sbatch
#
# Override defaults with env vars, for example:
#   MEDQA_MODEL=openai_gpt5_mini MEDQA_NUM_SHARDS=8 sbatch scripts/slurm_medqa_full_array.sbatch
#   MEDQA_RESUME_LATEST=1 sbatch scripts/slurm_medqa_full_array.sbatch
# Or override Slurm resources directly at submit time:
#   sbatch -p cpu --array=0-15 scripts/slurm_medqa_full_array.sbatch

#SBATCH -J medqa-full
#SBATCH -p cpu
#SBATCH --cpus-per-task=1
#SBATCH --time=02:00:00
#SBATCH --array=0-7

set -euo pipefail

REPO_ROOT="${SLURM_SUBMIT_DIR:-$(pwd)}"
cd "${REPO_ROOT}"

mkdir -p runs/slurm

EXPERIMENT="${MEDQA_EXPERIMENT:-medqa}"
MODEL="${MEDQA_MODEL:-openai_gpt5_mini}"
BASE_DIR="${MEDQA_BASE_DIR:-runs}"
NUM_SHARDS="${MEDQA_NUM_SHARDS:-8}"
SHARD_INDEX="${SLURM_ARRAY_TASK_ID}"
RESUME_LATEST="${MEDQA_RESUME_LATEST:-0}"

if [[ ! -x ".venv/bin/python" ]]; then
  echo "Missing project virtualenv at .venv/bin/python" >&2
  exit 1
fi

RUN_SLUG="$(printf "medqa-full-shard%02dof%02d" "${SHARD_INDEX}" "${NUM_SHARDS}")"
RESUME_ARGS=()
if [[ "${RESUME_LATEST}" == "1" ]]; then
  LATEST_RUN_DIR="$(find "${BASE_DIR}/${RUN_SLUG}" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort | tail -n 1 || true)"
  if [[ -n "${LATEST_RUN_DIR}" ]]; then
    RESUME_ARGS=(--resume-run-dir "${LATEST_RUN_DIR}")
    echo "Resuming existing shard run from ${LATEST_RUN_DIR}"
  else
    echo "No existing shard run found under ${BASE_DIR}/${RUN_SLUG}; starting a fresh shard"
  fi
fi

echo "Running MedQA full shard ${SHARD_INDEX}/${NUM_SHARDS} with experiment ${EXPERIMENT} and model ${MODEL}"

.venv/bin/python -m dynaforge.experiment_cli medqa \
  --experiment "${EXPERIMENT}" \
  --subset full \
  --num-shards "${NUM_SHARDS}" \
  --shard-index "${SHARD_INDEX}" \
  --base-dir "${BASE_DIR}" \
  --model "${MODEL}" \
  "${RESUME_ARGS[@]}"
