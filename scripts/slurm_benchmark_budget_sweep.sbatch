#!/bin/bash
# Submit from repo root, for example:
#   BENCHMARK_COMMAND=math sbatch scripts/slurm_benchmark_budget_sweep.sbatch
#   BENCHMARK_COMMAND=humaneval sbatch scripts/slurm_benchmark_budget_sweep.sbatch

#SBATCH -J benchmark-budget
#SBATCH -p cpu
#SBATCH --cpus-per-task=1
#SBATCH --time=02:00:00
#SBATCH --array=0-71

set -euo pipefail

REPO_ROOT="${SLURM_SUBMIT_DIR:-$(pwd)}"
cd "${REPO_ROOT}"

if [[ ! -x ".venv/bin/python" ]]; then
  echo "Missing project virtualenv at .venv/bin/python" >&2
  exit 1
fi

BENCHMARK_COMMAND="${BENCHMARK_COMMAND:-math}"
BENCHMARK_EXPERIMENT="${BENCHMARK_EXPERIMENT:-${BENCHMARK_COMMAND}}"
BENCHMARK_NUM_SHARDS="${BENCHMARK_NUM_SHARDS:-8}"
BENCHMARK_RESUME_LATEST="${BENCHMARK_RESUME_LATEST:-0}"

case "${BENCHMARK_COMMAND}" in
  math|humaneval)
    MODELS_CSV="${MODELS_CSV:-openai_gpt4o_mini,openai_gpt41_mini,openai_gpt5_mini}"
    BENCHMARK_SUBSET="${BENCHMARK_SUBSET:-test}"
    BASE_DIR_ROOT="${BASE_DIR_ROOT:-runs/${BENCHMARK_COMMAND}-budget-sweeps}"
    ;;
  *)
    echo "Unsupported BENCHMARK_COMMAND=${BENCHMARK_COMMAND}" >&2
    exit 1
    ;;
esac

BUDGET_TIERS_CSV="${BUDGET_TIERS_CSV:-tight,balanced,generous}"

IFS=',' read -r -a MODELS <<< "${MODELS_CSV}"
IFS=',' read -r -a BUDGET_TIERS <<< "${BUDGET_TIERS_CSV}"

TOTAL_CONFIGS=$(( ${#MODELS[@]} * ${#BUDGET_TIERS[@]} ))
CONFIG_INDEX=$(( SLURM_ARRAY_TASK_ID / BENCHMARK_NUM_SHARDS ))
SHARD_INDEX=$(( SLURM_ARRAY_TASK_ID % BENCHMARK_NUM_SHARDS ))

if (( CONFIG_INDEX >= TOTAL_CONFIGS )); then
  echo "SLURM_ARRAY_TASK_ID=${SLURM_ARRAY_TASK_ID} exceeds configured model/budget grid" >&2
  exit 1
fi

MODEL_INDEX=$(( CONFIG_INDEX / ${#BUDGET_TIERS[@]} ))
TIER_INDEX=$(( CONFIG_INDEX % ${#BUDGET_TIERS[@]} ))
MODEL="${MODELS[$MODEL_INDEX]}"
BUDGET_TIER="${BUDGET_TIERS[$TIER_INDEX]}"
BASE_DIR="${BASE_DIR_ROOT}/${BENCHMARK_EXPERIMENT}/${MODEL}/${BUDGET_TIER}"

OVERRIDES=()
case "${BUDGET_TIER}" in
  tight)
    OVERRIDES=(
      "blueprint.budget.max_total_usd=0.0015"
      "blueprint.budget.max_total_tokens=2200"
      "+blueprint.base_nodes.0.max_steps=4"
    )
    ;;
  balanced)
    OVERRIDES=(
      "blueprint.budget.max_total_usd=0.0030"
      "blueprint.budget.max_total_tokens=4000"
      "+blueprint.base_nodes.0.max_steps=6"
    )
    ;;
  generous)
    OVERRIDES=(
      "blueprint.budget.max_total_usd=0.0060"
      "blueprint.budget.max_total_tokens=8000"
      "+blueprint.base_nodes.0.max_steps=8"
    )
    ;;
  *)
    echo "Unknown BUDGET_TIER=${BUDGET_TIER}" >&2
    exit 1
    ;;
esac

if [[ "${BENCHMARK_COMMAND}" == "math" || "${BENCHMARK_COMMAND}" == "humaneval" ]]; then
  case "${BUDGET_TIER}" in
    tight)
      OVERRIDES+=("blueprint.gates.0.trigger.all_of.1.value=0.65")
      ;;
    balanced)
      OVERRIDES+=("blueprint.gates.0.trigger.all_of.1.value=0.70")
      ;;
    generous)
      OVERRIDES+=("blueprint.gates.0.trigger.all_of.1.value=0.80")
      ;;
  esac
fi

RUN_SLUG="$(printf "%s-%s-shard%02dof%02d" "${BENCHMARK_COMMAND}" "${BENCHMARK_SUBSET}" "${SHARD_INDEX}" "${BENCHMARK_NUM_SHARDS}")"
RESUME_ARGS=()
if [[ "${BENCHMARK_RESUME_LATEST}" == "1" ]]; then
  LATEST_RUN_DIR="$(find "${BASE_DIR}/${RUN_SLUG}" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort | tail -n 1 || true)"
  if [[ -n "${LATEST_RUN_DIR}" ]]; then
    RESUME_ARGS=(--resume-run-dir "${LATEST_RUN_DIR}")
    echo "Resuming existing sweep shard from ${LATEST_RUN_DIR}"
  fi
fi

echo "Running ${BENCHMARK_COMMAND} budget sweep shard ${SHARD_INDEX}/${BENCHMARK_NUM_SHARDS}"
echo "Experiment=${BENCHMARK_EXPERIMENT} Model=${MODEL} BudgetTier=${BUDGET_TIER} BaseDir=${BASE_DIR}"

.venv/bin/python -m agentcoop.experiment_cli "${BENCHMARK_COMMAND}" \
  --experiment "${BENCHMARK_EXPERIMENT}" \
  --subset "${BENCHMARK_SUBSET}" \
  --num-shards "${BENCHMARK_NUM_SHARDS}" \
  --shard-index "${SHARD_INDEX}" \
  --base-dir "${BASE_DIR}" \
  --model "${MODEL}" \
  "${RESUME_ARGS[@]}" \
  "${OVERRIDES[@]}"
