from __future__ import annotations

import hydra
from hydra.core.config_store import ConfigStore
from omegaconf import DictConfig, OmegaConf

import asyncio

from config import EvalConfig, LogConfig, ModelConfig
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
        log_dict = cfg_obj.get("log", {})
        return EvalConfig(
            tasks=cfg_obj.get("tasks", "data/tasks.jsonl"),
            method=cfg_obj.get("method", "orchestrated"),
            model=ModelConfig(**model_dict),
            log_dir=cfg_obj.get("log_dir", "logs"),
            log=LogConfig(**log_dict) if isinstance(log_dict, dict) else LogConfig(),
            max_samples=cfg_obj.get("max_samples"),
        )
    raise TypeError("Config is not compatible with EvalConfig.")


ConfigStore.instance().store(name="eval", node=EvalConfig)


@hydra.main(version_base=None, config_path="../conf", config_name="eval")
def main(cfg: DictConfig) -> None:
    asyncio.run(run_async(cfg))


async def run_async(cfg: DictConfig) -> None:
    eval_cfg = to_eval_config(cfg)
    from utils import log_event, set_log_config

    set_log_config(
        enabled=eval_cfg.log.enabled,
        level=eval_cfg.log.level,
        use_color=eval_cfg.log.use_color,
        use_icons=eval_cfg.log.use_icons,
        preview_chars=eval_cfg.log.preview_chars,
        show_prompts=eval_cfg.log.show_prompts,
        show_outputs=eval_cfg.log.show_outputs,
    )
    log_event("RUN", "start", "starting eval", data={"method": eval_cfg.method})
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
    log_event("RUN", "done", "completed eval", data={"count": len(tasks)})


if __name__ == "__main__":
    main()
