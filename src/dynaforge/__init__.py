"""DynaForge core package."""

try:  # pragma: no cover - exercised indirectly in sandboxed runtime imports
    from dynaforge.config import (
        build_blueprint_from_config,
        build_executor_from_config,
        get_benchmark_settings,
        load_hydra_config,
        run_configured_experiment,
    )
except ImportError:  # pragma: no cover - optional in lightweight sandbox tool environments
    build_blueprint_from_config = None
    build_executor_from_config = None
    get_benchmark_settings = None
    load_hydra_config = None
    run_configured_experiment = None

try:  # pragma: no cover - exercised indirectly
    from dynaforge.runtime.executor import BlueprintExecutor
except ImportError:  # pragma: no cover - optional in lightweight sandbox tool environments
    BlueprintExecutor = None

__all__ = [
    "BlueprintExecutor",
    "build_blueprint_from_config",
    "build_executor_from_config",
    "get_benchmark_settings",
    "load_hydra_config",
    "run_configured_experiment",
]
