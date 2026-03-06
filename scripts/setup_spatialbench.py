from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dynaforge.benchmarks import setup_spatialbench_workspace
from dynaforge.config import load_hydra_config


if __name__ == "__main__":
    config = load_hydra_config(overrides=["experiment=spatialbench"])
    result = setup_spatialbench_workspace(config)
    print(result)
