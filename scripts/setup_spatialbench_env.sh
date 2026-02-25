#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_NAME="${ENV_NAME:-spatialbench}"
PYTHON_VERSION="${PYTHON_VERSION:-3.11}"

if [[ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]]; then
  source "$HOME/miniconda3/etc/profile.d/conda.sh"
elif command -v conda >/dev/null 2>&1; then
  eval "$(conda shell.bash hook)"
else
  echo "ERROR: conda not found." >&2
  exit 1
fi

if conda env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
  echo "Conda env '$ENV_NAME' already exists."
else
  echo "Creating conda env '$ENV_NAME' (python=$PYTHON_VERSION)..."
  conda create -y -n "$ENV_NAME" "python=$PYTHON_VERSION"
fi

conda activate "$ENV_NAME"

python -m pip install --upgrade pip setuptools wheel

echo "Installing Agent-Cop benchmark runtime dependencies..."
python -m pip install -r scripts/requirements_spatialbench_env.txt

echo "Installing SpatialBench from third_party/spatialbench..."
python -m pip install -e third_party/spatialbench

echo "Installing latch CLI..."
python -m pip install "latch>=2,<3"

echo
echo "Setup complete."
echo "Next:"
echo "  conda activate $ENV_NAME"
echo "  export OPENAI_API_KEY=..."
echo "  latch login"
echo "  latch workspace    # select the correct workspace if needed"
echo "  python eval/spatialbench_prefetch.py --eval-dir third_party/spatialbench/evals_canonical --max-samples 1"
echo "  python eval/spatialbench_eval.py --config conf/config_w_api.yaml --dry-run"
