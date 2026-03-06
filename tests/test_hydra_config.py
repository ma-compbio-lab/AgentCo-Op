from __future__ import annotations

from dynaforge.config import (
    build_blueprint_from_config,
    build_executor_from_config,
    load_hydra_config,
    run_configured_experiment,
)
from hydra import compose, initialize_config_dir
from dynaforge.runtime.executor import BlueprintExecutor
from dynaforge.runtime.llm import LLMRouter


class StubLLMClient:
    def complete(self, model, messages, *, response_format=None):
        return {
            "content": '{"output":{"result":{"configured":true}},"confidence":0.88,"summary":"ok"}',
            "usage": {"prompt_tokens": 10, "completion_tokens": 6},
        }


def test_load_hydra_config_builds_minimal_blueprint() -> None:
    config = load_hydra_config()
    blueprint = build_blueprint_from_config(config)

    assert blueprint.task.task_id == "minimal-demo"
    assert blueprint.node_map()["planner"].model.name == "gpt-4.1-mini"


def test_hydra_overrides_swap_model_group() -> None:
    config = load_hydra_config(overrides=["model=openai_gpt41"])
    blueprint = build_blueprint_from_config(config)

    assert blueprint.node_map()["worker"].model.name == "gpt-4.1"


def test_build_executor_from_config_uses_executor_section() -> None:
    config = load_hydra_config(overrides=["executor.allow_offline_fallback=false"])
    executor = build_executor_from_config(config)

    assert isinstance(executor, BlueprintExecutor)
    assert executor.llm_router.allow_offline_fallback is False


def test_run_configured_experiment_executes_default_config() -> None:
    config = load_hydra_config()
    executor = BlueprintExecutor(
        llm_router=LLMRouter(client=StubLLMClient(), allow_offline_fallback=False),
        allow_offline_fallback=False,
    )
    _, report = run_configured_experiment(config, executor=executor)

    assert report.success
    assert report.task_id == "minimal-demo"


def test_experiment_group_can_override_executor_settings() -> None:
    config = load_hydra_config(overrides=["experiment=minimal_repair"])

    assert config.executor.repair_enabled is True
    assert config.executor.max_repair_iterations == 2


def test_nested_config_shape_is_still_supported() -> None:
    config = {
        "experiment": {
            "blueprint": {
                "task": {
                    "task_id": "nested",
                    "title": "Nested",
                    "description": "Nested experiment shape",
                },
                "base_nodes": [
                    {
                        "node_id": "planner",
                        "kind": "agent",
                        "role": "Planner",
                        "model": {
                            "provider": "openai",
                            "name": "gpt-4.1-mini",
                        },
                    }
                ],
                "base_edges": [],
            },
            "executor": {
                "allow_offline_fallback": False,
            },
        }
    }

    blueprint = build_blueprint_from_config(config)
    executor = build_executor_from_config(config)

    assert blueprint.task.task_id == "nested"
    assert executor.llm_router.allow_offline_fallback is False


def test_load_hydra_config_works_when_hydra_is_already_initialized() -> None:
    from dynaforge.config import DEFAULT_CONFIG_DIR

    with initialize_config_dir(
        config_dir=str(DEFAULT_CONFIG_DIR.resolve()),
        version_base="1.3",
        job_name="outer_test",
    ):
        cfg = load_hydra_config()
        direct = compose(config_name="config")

        assert cfg.blueprint.task.task_id == "minimal-demo"
        assert cfg.blueprint.task.task_id == direct.blueprint.task.task_id
