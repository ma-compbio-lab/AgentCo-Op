#!/bin/bash
# Submit from repo root, for example:
#   BENCHMARK_COMMAND=math sbatch scripts/slurm_benchmark_full_array.sbatch
#   BENCHMARK_COMMAND=humaneval sbatch scripts/slurm_benchmark_full_array.sbatch
#   BENCHMARK_COMMAND=medqa BENCHMARK_MODEL=openai_gpt5_mini sbatch scripts/slurm_benchmark_full_array.sbatch

#SBATCH -J benchmark-full
#SBATCH -p cpu
#SBATCH --cpus-per-task=1
#SBATCH --time=02:00:00
#SBATCH --array=0-7

set -euo pipefail

REPO_ROOT="${SLURM_SUBMIT_DIR:-$(pwd)}"
cd "${REPO_ROOT}"

if [[ ! -x ".venv/bin/python" ]]; then
  echo "Missing project virtualenv at .venv/bin/python" >&2
  exit 1
fi

BENCHMARK_COMMAND="${BENCHMARK_COMMAND:-math}"
BENCHMARK_EXPERIMENT="${BENCHMARK_EXPERIMENT:-${BENCHMARK_COMMAND}}"
BENCHMARK_BASE_DIR="${BENCHMARK_BASE_DIR:-runs}"
BENCHMARK_NUM_SHARDS="${BENCHMARK_NUM_SHARDS:-8}"
SHARD_INDEX="${SLURM_ARRAY_TASK_ID}"
BENCHMARK_RESUME_LATEST="${BENCHMARK_RESUME_LATEST:-0}"

case "${BENCHMARK_COMMAND}" in
  medqa)
    BENCHMARK_MODEL="${BENCHMARK_MODEL:-openai_gpt4o_mini}"
    BENCHMARK_SUBSET="${BENCHMARK_SUBSET:-full}"
    ;;
  math|humaneval)
    BENCHMARK_MODEL="${BENCHMARK_MODEL:-openai_gpt4o_mini}"
    BENCHMARK_SUBSET="${BENCHMARK_SUBSET:-test}"
    ;;
  *)
    echo "Unsupported BENCHMARK_COMMAND=${BENCHMARK_COMMAND}" >&2
    exit 1
    ;;
esac

RUN_SLUG="$(printf "%s-%s-shard%02dof%02d" "${BENCHMARK_COMMAND}" "${BENCHMARK_SUBSET}" "${SHARD_INDEX}" "${BENCHMARK_NUM_SHARDS}")"
RESUME_ARGS=()
if [[ "${BENCHMARK_RESUME_LATEST}" == "1" ]]; then
  LATEST_RUN_DIR="$(find "${BENCHMARK_BASE_DIR}/${RUN_SLUG}" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort | tail -n 1 || true)"
  if [[ -n "${LATEST_RUN_DIR}" ]]; then
    RESUME_ARGS=(--resume-run-dir "${LATEST_RUN_DIR}")
    echo "Resuming existing shard run from ${LATEST_RUN_DIR}"
  fi
fi

echo "Running ${BENCHMARK_COMMAND} ${BENCHMARK_SUBSET} shard ${SHARD_INDEX}/${BENCHMARK_NUM_SHARDS} with model ${BENCHMARK_MODEL}"

.venv/bin/python -m dynaforge.experiment_cli "${BENCHMARK_COMMAND}" \
  --experiment "${BENCHMARK_EXPERIMENT}" \
  --subset "${BENCHMARK_SUBSET}" \
  --num-shards "${BENCHMARK_NUM_SHARDS}" \
  --shard-index "${SHARD_INDEX}" \
  --base-dir "${BENCHMARK_BASE_DIR}" \
  --model "${BENCHMARK_MODEL}" \
  "${RESUME_ARGS[@]}"
