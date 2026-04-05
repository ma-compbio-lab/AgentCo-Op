from __future__ import annotations

from agentcoop.config import load_hydra_config, run_configured_experiment


if __name__ == "__main__":
    config = load_hydra_config()
    _, report = run_configured_experiment(config)
    print(report.model_dump_json(indent=2))

