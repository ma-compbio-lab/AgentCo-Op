#!/bin/bash
# Submit from repo root:
#   sbatch scripts/slurm_medqa_budget_sweep.sbatch
#
# Defaults assume:
#   - models: openai_gpt5_nano, openai_gpt5_mini, openai_gpt5
#   - budget tiers: tight, balanced, generous
#   - shards: 8
#   => 3 * 3 * 8 = 72 array tasks
#
# Useful overrides:
#   MEDQA_EXPERIMENT=medqa_gpt5_review sbatch scripts/slurm_medqa_budget_sweep.sbatch
#   MEDQA_RESUME_LATEST=1 sbatch scripts/slurm_medqa_budget_sweep.sbatch
#   sbatch --array=0-23 scripts/slurm_medqa_budget_sweep.sbatch

#SBATCH -J medqa-budget
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

EXPERIMENT="${MEDQA_EXPERIMENT:-medqa}"
MODELS_CSV="${MEDQA_MODELS_CSV:-openai_gpt5_nano,openai_gpt5_mini,openai_gpt5}"
BUDGET_TIERS_CSV="${MEDQA_BUDGET_TIERS_CSV:-tight,balanced,generous}"
BASE_DIR_ROOT="${MEDQA_BASE_DIR_ROOT:-runs/medqa-budget-sweeps}"
NUM_SHARDS="${MEDQA_NUM_SHARDS:-8}"
RESUME_LATEST="${MEDQA_RESUME_LATEST:-0}"

IFS=',' read -r -a MODELS <<< "${MODELS_CSV}"
IFS=',' read -r -a BUDGET_TIERS <<< "${BUDGET_TIERS_CSV}"

if [[ "${#MODELS[@]}" -eq 0 || "${#BUDGET_TIERS[@]}" -eq 0 ]]; then
  echo "MODELS_CSV and BUDGET_TIERS_CSV must each contain at least one entry" >&2
  exit 1
fi

TOTAL_CONFIGS=$(( ${#MODELS[@]} * ${#BUDGET_TIERS[@]} ))
CONFIG_INDEX=$(( SLURM_ARRAY_TASK_ID / NUM_SHARDS ))
SHARD_INDEX=$(( SLURM_ARRAY_TASK_ID % NUM_SHARDS ))

if (( CONFIG_INDEX >= TOTAL_CONFIGS )); then
  echo "SLURM_ARRAY_TASK_ID=${SLURM_ARRAY_TASK_ID} exceeds configured model/budget grid" >&2
  exit 1
fi

MODEL_INDEX=$(( CONFIG_INDEX / ${#BUDGET_TIERS[@]} ))
TIER_INDEX=$(( CONFIG_INDEX % ${#BUDGET_TIERS[@]} ))

MODEL="${MODELS[$MODEL_INDEX]}"
BUDGET_TIER="${BUDGET_TIERS[$TIER_INDEX]}"
BASE_DIR="${BASE_DIR_ROOT}/${EXPERIMENT}/${MODEL}/${BUDGET_TIER}"

OVERRIDES=()
case "${BUDGET_TIER}" in
  tight)
    OVERRIDES=(
      "blueprint.budget.max_total_usd=0.0015"
      "blueprint.budget.max_total_tokens=2200"
      "+blueprint.base_nodes.0.max_steps=4"
      "+blueprint.base_nodes.1.max_steps=4"
      "blueprint.gates.0.trigger.all_of.1.any_of.0.value=0.80"
    )
    ;;
  balanced)
    OVERRIDES=(
      "blueprint.budget.max_total_usd=0.0025"
      "blueprint.budget.max_total_tokens=3500"
      "+blueprint.base_nodes.0.max_steps=6"
      "+blueprint.base_nodes.1.max_steps=6"
      "blueprint.gates.0.trigger.all_of.1.any_of.0.value=0.86"
    )
    ;;
  generous)
    OVERRIDES=(
      "blueprint.budget.max_total_usd=0.0050"
      "blueprint.budget.max_total_tokens=7000"
      "+blueprint.base_nodes.0.max_steps=8"
      "+blueprint.base_nodes.1.max_steps=8"
      "blueprint.gates.0.trigger.all_of.1.any_of.0.value=0.90"
    )
    ;;
  *)
    echo "Unknown BUDGET_TIER=${BUDGET_TIER}" >&2
    exit 1
    ;;
esac

RUN_SLUG="$(printf "medqa-full-shard%02dof%02d" "${SHARD_INDEX}" "${NUM_SHARDS}")"
RESUME_ARGS=()
if [[ "${RESUME_LATEST}" == "1" ]]; then
  LATEST_RUN_DIR="$(find "${BASE_DIR}/${RUN_SLUG}" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort | tail -n 1 || true)"
  if [[ -n "${LATEST_RUN_DIR}" ]]; then
    RESUME_ARGS=(--resume-run-dir "${LATEST_RUN_DIR}")
    echo "Resuming existing sweep shard from ${LATEST_RUN_DIR}"
  else
    echo "No existing shard run found under ${BASE_DIR}/${RUN_SLUG}; starting fresh"
  fi
fi

echo "Running MedQA budget sweep shard ${SHARD_INDEX}/${NUM_SHARDS}"
echo "Experiment=${EXPERIMENT} Model=${MODEL} BudgetTier=${BUDGET_TIER} BaseDir=${BASE_DIR}"

.venv/bin/python -m dynaforge.experiment_cli medqa \
  --experiment "${EXPERIMENT}" \
  --subset full \
  --num-shards "${NUM_SHARDS}" \
  --shard-index "${SHARD_INDEX}" \
  --base-dir "${BASE_DIR}" \
  --model "${MODEL}" \
  "${RESUME_ARGS[@]}" \
  "${OVERRIDES[@]}"
