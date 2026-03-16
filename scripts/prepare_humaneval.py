from __future__ import annotations

import json

from dynaforge.benchmarks import prepare_humaneval_dataset
from dynaforge.config import load_hydra_config


def main() -> int:
    config = load_hydra_config(overrides=["experiment=humaneval"])
    result = prepare_humaneval_dataset(config)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
