from __future__ import annotations

import hydra
from hydra.core.config_store import ConfigStore
from omegaconf import DictConfig, OmegaConf

from config import AppConfig, ModelConfig, TaskConfig
from core.contracts import TaskSpec
from methods.dispatcher import run_method
from models import get_model_backend
from runtime import build_runtime


def to_app_config(cfg: DictConfig) -> AppConfig:
    # Normalize Hydra config into typed dataclasses for safe access.
    cfg_obj = OmegaConf.to_object(cfg)
    if isinstance(cfg_obj, AppConfig):
        return cfg_obj
    if isinstance(cfg_obj, dict):
        task_dict = cfg_obj.get("task", {})
        model_dict = cfg_obj.get("model", {})
        return AppConfig(
            task=TaskConfig(**task_dict),
            method=cfg_obj.get("method", "orchestrated"),
            model=ModelConfig(**model_dict),
            log_dir=cfg_obj.get("log_dir", "logs"),
        )
    raise TypeError("Config is not compatible with AppConfig.")


ConfigStore.instance().store(name="config", node=AppConfig)


@hydra.main(version_base=None, config_path="conf", config_name="config")
def main(cfg: DictConfig) -> None:
    app_cfg = to_app_config(cfg)

    task_cfg = app_cfg.task
    task = TaskSpec(
        goal=task_cfg.goal,
        constraints=task_cfg.constraints,
        success_criteria=task_cfg.success_criteria,
        budget_tokens=task_cfg.budget_tokens,
        input_modalities=task_cfg.input_modalities,
        output_modalities=task_cfg.output_modalities,
    )

    model = get_model_backend(
        app_cfg.model.backend,
        model=app_cfg.model.name,
        api_key=app_cfg.model.api_key,
    )
    runtime = build_runtime(model, log_dir=app_cfg.log_dir)

    result = run_method(app_cfg.method, task, runtime)
    print(result.answer)
    if result.judge_report:
        print(f"\n[Judge] ok={result.judge_report.ok} score={result.judge_report.score}")


if __name__ == "__main__":
    main()
