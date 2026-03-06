from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dynaforge.benchmarks import prepare_medqa_assets
from dynaforge.config import load_hydra_config


if __name__ == "__main__":
    config = load_hydra_config(overrides=["experiment=medqa"])
    result = prepare_medqa_assets(config)
    print(result)
