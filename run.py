from __future__ import annotations

import asyncio

import hydra
from hydra.core.config_store import ConfigStore
from omegaconf import DictConfig, OmegaConf

from config import (
    AppConfig,
    ChatConfig,
    LogConfig,
    MCPConfig,
    MemoryConfig,
    ModelConfig,
    RepairConfig,
    TaskConfig,
    ToolConfig,
)
from core.contracts import TaskSpec
from methods.dispatcher import run_method
from models import build_model_routing, configure_openai, ensure_api_key, validate_backend
from runtime import build_runtime


def to_app_config(cfg: DictConfig) -> AppConfig:
    # Normalize Hydra config into typed dataclasses for safe access.
    cfg_obj = OmegaConf.to_object(cfg)
    if isinstance(cfg_obj, AppConfig):
        return cfg_obj
    if isinstance(cfg_obj, dict):
        task_dict = cfg_obj.get("task", {})
        model_dict = cfg_obj.get("model", {})
        log_dict = cfg_obj.get("log", {})
        repair_dict = cfg_obj.get("repair", {})
        memory_dict = cfg_obj.get("memory", {})
        tool_dict = cfg_obj.get("tool", {})
        mcp_dict = cfg_obj.get("mcp", {})
        chat_dict = cfg_obj.get("chat", {})
        return AppConfig(
            task=TaskConfig(**task_dict),
            method=cfg_obj.get("method", "orchestrated"),
            model=ModelConfig(**model_dict),
            log_dir=cfg_obj.get("log_dir", "logs"),
            log=LogConfig(**log_dict) if isinstance(log_dict, dict) else LogConfig(),
            repair=RepairConfig(**repair_dict) if isinstance(repair_dict, dict) else RepairConfig(),
            memory=MemoryConfig(**memory_dict) if isinstance(memory_dict, dict) else MemoryConfig(),
            tool=ToolConfig(**tool_dict) if isinstance(tool_dict, dict) else ToolConfig(),
            mcp=MCPConfig(**mcp_dict) if isinstance(mcp_dict, dict) else MCPConfig(),
            chat=ChatConfig(**chat_dict) if isinstance(chat_dict, dict) else ChatConfig(),
        )
    raise TypeError("Config is not compatible with AppConfig.")


ConfigStore.instance().store(name="config", node=AppConfig)


@hydra.main(version_base=None, config_path="conf", config_name="config")
def main(cfg: DictConfig) -> None:
    asyncio.run(run_async(cfg))


async def run_async(cfg: DictConfig) -> None:
    app_cfg = to_app_config(cfg)
    from utils import log_event, set_log_config

    set_log_config(
        enabled=app_cfg.log.enabled,
        level=app_cfg.log.level,
        use_color=app_cfg.log.use_color,
        use_icons=app_cfg.log.use_icons,
        preview_chars=app_cfg.log.preview_chars,
        show_prompts=app_cfg.log.show_prompts,
        show_outputs=app_cfg.log.show_outputs,
    )
    log_event("RUN", "start", "starting run", data={"method": app_cfg.method})

    task_cfg = app_cfg.task
    task = TaskSpec(
        goal=task_cfg.goal,
        constraints=task_cfg.constraints,
        success_criteria=task_cfg.success_criteria,
        budget_tokens=task_cfg.budget_tokens,
        input_modalities=task_cfg.input_modalities,
        output_modalities=task_cfg.output_modalities,
        allow_web_search_for_spec=task_cfg.allow_web_search_for_spec,
        spec_max_search_queries=task_cfg.spec_max_search_queries,
        allow_tool_search=task_cfg.allow_tool_search,
        tool_max_candidates=task_cfg.tool_max_candidates,
        tool_max_search_queries=task_cfg.tool_max_search_queries,
    )

    validate_backend(app_cfg.model.backend)
    configure_openai(app_cfg.model.api_key)
    ensure_api_key()
    routing = build_model_routing(app_cfg.model)
    runtime = build_runtime(
        routing,
        log_dir=app_cfg.log_dir,
        repair=app_cfg.repair,
        memory_cfg=app_cfg.memory,
        tool_cfg=app_cfg.tool,
        mcp_cfg=app_cfg.mcp,
    )

    result = await run_method(app_cfg.method, task, runtime)
    print(result.answer)
    if result.judge_report:
        print(f"\n[Judge] ok={result.judge_report.ok} score={result.judge_report.score}")
    log_event("RUN", "done", "completed run")


if __name__ == "__main__":
    main()
