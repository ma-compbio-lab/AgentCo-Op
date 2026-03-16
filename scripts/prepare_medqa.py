from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dynaforge.benchmarks import prepare_medqa_assets, prepare_medqa_dataset
from dynaforge.config import load_hydra_config


if __name__ == "__main__":
    config = load_hydra_config(overrides=["experiment=medqa"])
    result = {
        "assets": prepare_medqa_assets(config),
        "dataset": prepare_medqa_dataset(config),
    }
    print(json.dumps(result, ensure_ascii=True, indent=2))
