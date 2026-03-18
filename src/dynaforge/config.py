from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from hydra import compose, initialize_config_dir
from hydra.core.global_hydra import GlobalHydra
from omegaconf import DictConfig, OmegaConf

from dynaforge.ir import WorkflowBlueprint
from dynaforge.ir.schema import ModelSpec
from dynaforge.runtime.executor import BlueprintExecutor
from dynaforge.runtime.reports import ExecutionReport
from dynaforge.runtime.skills import SkillRegistry
from dynaforge.workflows.compiler import compile_workflow_from_config, extract_compiler_config
from dynaforge.workflows.expansion import RuntimeGraphExpansionPolicy

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parent / "conf"


def load_hydra_config(
    overrides: Optional[Sequence[str]] = None,
    *,
    config_name: str = "config",
    config_dir: str | Path | None = None,
) -> DictConfig:
    overrides = list(overrides or [])
    config_root = Path(config_dir) if config_dir is not None else DEFAULT_CONFIG_DIR
    hydra_state = GlobalHydra.instance()
    if hydra_state.is_initialized():
        return compose(config_name=config_name, overrides=overrides)
    with initialize_config_dir(
        config_dir=str(config_root.resolve()),
        version_base="1.3",
        job_name="dynaforge_config",
    ):
        return compose(config_name=config_name, overrides=overrides)


def resolve_hydra_config(config: DictConfig | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(config, DictConfig):
        resolved = OmegaConf.to_container(config, resolve=True)
        if not isinstance(resolved, dict):
            raise TypeError("Resolved Hydra config must be a mapping")
        return resolved
    return dict(config)


def get_benchmark_settings(config: DictConfig | Mapping[str, Any]) -> dict[str, Any]:
    resolved = resolve_hydra_config(config)
    benchmark_cfg = resolved.get("benchmark")
    if isinstance(benchmark_cfg, Mapping):
        return dict(benchmark_cfg)
    experiment_cfg = resolved.get("experiment")
    if isinstance(experiment_cfg, Mapping):
        nested_benchmark = experiment_cfg.get("benchmark")
        if isinstance(nested_benchmark, Mapping):
            return dict(nested_benchmark)
    return {}


def build_blueprint_from_config(config: DictConfig | Mapping[str, Any]) -> WorkflowBlueprint:
    resolved = resolve_hydra_config(config)
    compiler_cfg = extract_compiler_config(resolved)
    if compiler_cfg is not None and compiler_cfg.enabled:
        blueprint, _ = compile_workflow_from_config(resolved)
        return blueprint
    blueprint_payload = resolved.get("blueprint")
    if blueprint_payload is None:
        experiment_cfg = resolved.get("experiment")
        if isinstance(experiment_cfg, Mapping):
            blueprint_payload = experiment_cfg.get("blueprint")
    if blueprint_payload is None:
        blueprint_payload = resolved
    return WorkflowBlueprint.model_validate(blueprint_payload)


def build_executor_from_config(
    config: DictConfig | Mapping[str, Any],
    **overrides: Any,
) -> BlueprintExecutor:
    resolved = resolve_hydra_config(config)
    executor_cfg = dict(resolved.get("executor", {}))
    experiment_cfg = resolved.get("experiment")
    if isinstance(experiment_cfg, Mapping):
        nested_executor = experiment_cfg.get("executor")
        if isinstance(nested_executor, Mapping):
            executor_cfg = {**executor_cfg, **dict(nested_executor)}
    kwargs: dict[str, Any] = {
        "artifact_root": executor_cfg.get("artifact_root", ".dynaforge_artifacts"),
        "allow_offline_fallback": executor_cfg.get("allow_offline_fallback", True),
    }
    compiler_cfg = extract_compiler_config(resolved)
    if compiler_cfg is not None and compiler_cfg.enabled and compiler_cfg.runtime_expansion.enabled:
        model_spec = ModelSpec.model_validate(resolved.get("model", {}))
        review_model_spec = ModelSpec.model_validate(resolved.get("review_model", resolved.get("model", {})))
        kwargs["runtime_expansion_policy"] = RuntimeGraphExpansionPolicy(
            compiler_cfg.runtime_expansion,
            model=model_spec,
            review_model=review_model_spec,
        )
    skill_search_paths = executor_cfg.get("skill_search_paths")
    skill_project_root = executor_cfg.get("skill_project_root")
    if skill_search_paths is not None or skill_project_root is not None:
        kwargs["skill_registry"] = SkillRegistry(
            search_paths=skill_search_paths,
            project_root=skill_project_root,
        )
    kwargs.update(overrides)
    return BlueprintExecutor(**kwargs)


def run_configured_experiment(
    config: DictConfig | Mapping[str, Any],
    *,
    executor: Optional[BlueprintExecutor] = None,
) -> tuple[WorkflowBlueprint, ExecutionReport]:
    resolved = resolve_hydra_config(config)
    blueprint = build_blueprint_from_config(resolved)
    executor_cfg = dict(resolved.get("executor", {}))
    experiment_cfg = resolved.get("experiment")
    if isinstance(experiment_cfg, Mapping):
        nested_executor = experiment_cfg.get("executor")
        if isinstance(nested_executor, Mapping):
            executor_cfg = {**executor_cfg, **dict(nested_executor)}
    runner = executor or build_executor_from_config(resolved)
    if executor_cfg.get("repair_enabled", False):
        max_iterations = int(executor_cfg.get("max_repair_iterations", 3))
        updated_blueprint, report = runner.execute_with_repair(
            blueprint,
            max_iterations=max_iterations,
        )
        return updated_blueprint, report
    return blueprint, runner.execute_blueprint(blueprint)
