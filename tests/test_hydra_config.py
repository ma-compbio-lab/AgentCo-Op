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


def test_medqa_config_uses_advisory_planner_mapping() -> None:
    config = load_hydra_config(overrides=["experiment=medqa", "model=openai_gpt5_mini"])
    blueprint = build_blueprint_from_config(config)

    medqa_edge = blueprint.base_edges[0]
    medqa_gate = blueprint.gates[0]

    assert config.benchmark.split == "test"
    assert config.benchmark.full_count == "all"
    assert medqa_edge.mapping == {
        "planner_question_type": "question_type",
        "planner_key_clues": "key_clues",
        "planner_most_discriminating_clue": "most_discriminating_clue",
        "planner_rationale_outline": "rationale_outline",
    }
    assert medqa_gate.trigger.all_of[0].field == "node.node_id"
    assert medqa_gate.trigger.all_of[0].value == "responder"
    assert medqa_gate.trigger.all_of[1].any_of[0].value == 0.86


def test_medqa_planner_gate_config_preserves_legacy_gate() -> None:
    config = load_hydra_config(overrides=["experiment=medqa_planner_gate", "model=openai_gpt5_mini"])
    blueprint = build_blueprint_from_config(config)

    medqa_gate = blueprint.gates[0]

    assert medqa_gate.trigger.any_of[0].field == "report.confidence"
    assert medqa_gate.trigger.any_of[0].value == 0.86


def test_medqa_gpt5_review_config_uses_stronger_review_model() -> None:
    config = load_hydra_config(overrides=["experiment=medqa_gpt5_review", "model=openai_gpt5_mini"])
    blueprint = build_blueprint_from_config(config)

    review_nodes = blueprint.subgraphs[0].nodes
    reviewer = next(node for node in review_nodes if node.node_id == "reviewer")
    reviser = next(node for node in review_nodes if node.node_id == "reviser")

    assert reviewer.model.name == "gpt-5"
    assert reviser.model.name == "gpt-5"
    assert blueprint.node_map()["responder"].model.name == "gpt-5-mini"


def test_medqa_gpt5_review_gate90_config_raises_review_threshold() -> None:
    config = load_hydra_config(overrides=["experiment=medqa_gpt5_review_gate90", "model=openai_gpt5_mini"])
    blueprint = build_blueprint_from_config(config)
    reviewer = next(node for node in blueprint.subgraphs[0].nodes if node.node_id == "reviewer")

    assert reviewer.model.name == "gpt-5"
    assert blueprint.gates[0].trigger.all_of[1].any_of[0].value == 0.90


def test_scanpy_case_study_config_exposes_local_venv_sandbox() -> None:
    config = load_hydra_config(overrides=["experiment=scanpy_pbmc3k_case", "model=openai_gpt5_mini"])

    assert config.benchmark.kind == "case_study"
    assert config.benchmark.case_id == "scanpy_pbmc3k_umap"
    assert config.benchmark.sandbox.backend == "local_venv"
    assert config.executor.allow_offline_fallback is False


def test_squidpy_case_study_config_exposes_local_venv_sandbox() -> None:
    config = load_hydra_config(overrides=["experiment=squidpy_visium_case", "model=openai_gpt5_mini"])

    assert config.benchmark.kind == "case_study"
    assert config.benchmark.case_id == "squidpy_visium_hne_spatial"
    assert config.benchmark.sandbox.backend == "local_venv"
    assert config.executor.allow_offline_fallback is False


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
