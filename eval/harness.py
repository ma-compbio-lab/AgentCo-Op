from __future__ import annotations

import hydra
from hydra.core.config_store import ConfigStore
from omegaconf import DictConfig, OmegaConf

import asyncio

from config import EvalConfig, ModelConfig
from data import load_tasks
from eval.metrics import basic_metrics
from methods.dispatcher import run_method
from models import build_model_routing, configure_openai, ensure_api_key, validate_backend
from runtime import build_runtime
from utils import append_jsonl


def to_eval_config(cfg: DictConfig) -> EvalConfig:
    # Normalize Hydra config into typed dataclasses for safe access.
    cfg_obj = OmegaConf.to_object(cfg)
    if isinstance(cfg_obj, EvalConfig):
        return cfg_obj
    if isinstance(cfg_obj, dict):
        model_dict = cfg_obj.get("model", {})
        return EvalConfig(
            tasks=cfg_obj.get("tasks", "data/tasks.jsonl"),
            method=cfg_obj.get("method", "orchestrated"),
            model=ModelConfig(**model_dict),
            log_dir=cfg_obj.get("log_dir", "logs"),
            max_samples=cfg_obj.get("max_samples"),
        )
    raise TypeError("Config is not compatible with EvalConfig.")


ConfigStore.instance().store(name="eval", node=EvalConfig)


@hydra.main(version_base=None, config_path="../conf", config_name="eval")
def main(cfg: DictConfig) -> None:
    asyncio.run(run_async(cfg))


async def run_async(cfg: DictConfig) -> None:
    eval_cfg = to_eval_config(cfg)
    validate_backend(eval_cfg.model.backend)
    configure_openai(eval_cfg.model.api_key)
    ensure_api_key()
    routing = build_model_routing(eval_cfg.model)
    runtime = build_runtime(routing, log_dir=eval_cfg.log_dir)

    tasks = load_tasks(eval_cfg.tasks)
    if eval_cfg.max_samples:
        tasks = tasks[: eval_cfg.max_samples]

    summary_path = f"{eval_cfg.log_dir}/eval_summary.jsonl"
    for task in tasks:
        result = await run_method(eval_cfg.method, task, runtime)
        metrics = basic_metrics(result)
        row = {"task_id": task.task_id, "method": eval_cfg.method, "metrics": metrics}
        append_jsonl(summary_path, row)


if __name__ == "__main__":
    main()
