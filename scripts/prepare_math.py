from __future__ import annotations

import json

from agentcoop.benchmarks import prepare_math_dataset
from agentcoop.config import load_hydra_config


def main() -> int:
    config = load_hydra_config(overrides=["experiment=math"])
    result = prepare_math_dataset(config)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
