from __future__ import annotations

import sys
from typing import Sequence

from dynaforge.config import load_hydra_config, run_configured_experiment
from dynaforge.secrets import load_local_secrets


HELP_TEXT = """Usage:
  dynaforge-run [hydra_override ...]

Examples:
  dynaforge-run
  dynaforge-run model=openai_gpt41
  dynaforge-run experiment=minimal_repair executor.repair_enabled=true
"""


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    if any(arg in {"-h", "--help"} for arg in args):
        print(HELP_TEXT)
        return 0

    load_local_secrets()
    config = load_hydra_config(overrides=args)
    _, report = run_configured_experiment(config)
    print(report.model_dump_json(indent=2))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
