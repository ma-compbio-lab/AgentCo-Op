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


def test_minimal_tool_discovery_config_enables_builtin_web_search() -> None:
    config = load_hydra_config(overrides=["experiment=minimal_tool_discovery"])
    blueprint = build_blueprint_from_config(config)
    planner = blueprint.node_map()["planner"]

    assert planner.tool_discovery is not None
    assert planner.tool_discovery.mode.value == "registry"
    assert planner.tool_discovery.include_web_search is True
    assert planner.tool_discovery.server_allowlist == ["builtin-web-search"]


def test_minimal_skills_config_enables_packaged_skill() -> None:
    config = load_hydra_config(overrides=["experiment=minimal_skills"])
    blueprint = build_blueprint_from_config(config)
    planner = blueprint.node_map()["planner"]

    assert planner.skills
    assert planner.skills[0].name == "structured-json-discipline"


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


def test_medqa_config_uses_compiled_direct_answer_pattern() -> None:
    config = load_hydra_config(overrides=["experiment=medqa", "model=openai_gpt4o_mini"])
    blueprint = build_blueprint_from_config(config)

    medqa_gate = blueprint.gates[0]

    assert config.benchmark.split == "test"
    assert config.benchmark.full_count == "all"
    assert config.benchmark.paper_alignment.base_model == "gpt-5-nano"
    assert list(blueprint.node_map().keys()) == ["solver", "reviewer", "reviser"]
    assert blueprint.meta["workflow_pattern"] == "direct_answer"
    assert blueprint.meta["compile_trace"]["selected_pattern"] == "direct_answer"
    assert medqa_gate.trigger.all_of[0].field == "node.node_id"
    assert medqa_gate.trigger.all_of[0].value == "solver"
    assert medqa_gate.trigger.all_of[1].any_of[0].field == "node.outputs.requires_review"
    assert medqa_gate.trigger.all_of[1].any_of[1].value == 0.55


def test_medqa_planner_gate_config_remains_loadable() -> None:
    config = load_hydra_config(overrides=["experiment=medqa_planner_gate", "model=openai_gpt5_mini"])
    blueprint = build_blueprint_from_config(config)

    medqa_gate = blueprint.gates[0]

    assert blueprint.meta["workflow_pattern"] == "direct_answer"
    assert medqa_gate.trigger.all_of[0].field == "node.node_id"
    assert medqa_gate.trigger.all_of[0].value == "solver"


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


def test_math_config_matches_aflow_alignment() -> None:
    config = load_hydra_config(overrides=["experiment=math", "model=openai_gpt4o_mini"])
    blueprint = build_blueprint_from_config(config)

    assert config.benchmark.kind == "math"
    assert config.benchmark.split_seed == 42
    assert config.benchmark.validation_fraction == 0.2
    assert config.benchmark.paper_alignment.base_model == "gpt-4o-mini"
    assert blueprint.task.task_id == "math"
    assert blueprint.base_nodes[0].model.name == "gpt-4o-mini"


def test_humaneval_config_matches_aflow_alignment() -> None:
    config = load_hydra_config(overrides=["experiment=humaneval", "model=openai_gpt4o_mini"])
    blueprint = build_blueprint_from_config(config)

    assert config.benchmark.kind == "humaneval"
    assert config.benchmark.split_seed == 42
    assert config.benchmark.validation_fraction == 0.2
    assert config.benchmark.paper_alignment.base_model == "gpt-4o-mini"
    assert blueprint.task.task_id == "humaneval"
    assert blueprint.base_nodes[0].model.name == "gpt-4o-mini"


def test_math_v2_config_exposes_programmer_path() -> None:
    config = load_hydra_config(overrides=["experiment=math_v2", "model=openai_gpt4o_mini"])
    blueprint = build_blueprint_from_config(config)

    assert config.benchmark.workflow_variant == "aflow_v2"
    assert config.benchmark.reporting_protocol == "aflow_3run_average"
    assert config.benchmark.paper_alignment.operators == ["Custom", "ScEnsemble", "Programmer"]
    assert "program_exec" in blueprint.node_map()
    assert blueprint.node_map()["program_exec"].meta["direct_sandbox_handler"] is True
    assert blueprint.gates[0].trigger.all_of[0].value == "selector"


def test_humaneval_v2_config_exposes_public_test_loop() -> None:
    config = load_hydra_config(overrides=["experiment=humaneval_v2", "model=openai_gpt4o_mini"])
    blueprint = build_blueprint_from_config(config)

    assert config.benchmark.workflow_variant == "public_test_loop"
    assert config.benchmark.reporting_protocol == "aflow_3run_average"
    assert config.benchmark.paper_alignment.operators == ["Custom", "CustomCodeGenerate", "ScEnsemble", "Test"]
    assert "public_test_runner" in blueprint.node_map()
    assert "retest_runner" in blueprint.node_map()
    assert blueprint.node_map()["public_test_runner"].meta["direct_sandbox_handler"] is True
    assert blueprint.gates[0].trigger.all_of[0].value == "public_test_runner"


def test_generic_repo_transfer_smoke_config_uses_compiled_generic_runner() -> None:
    config = load_hydra_config(overrides=["experiment=generic_repo_transfer_smoke"])
    blueprint = build_blueprint_from_config(config)

    assert config.benchmark.kind == "case_study"
    assert config.benchmark.runner_mode == "compiled_generic"
    assert config.benchmark.case_id == "generic_repo_transfer_smoke"
    assert config.model.name == "gpt-5"
    assert blueprint.meta["workflow_pattern"] == "repo_transfer_validate"
    assert blueprint.meta["compile_trace"]["selected_pattern"] == "repo_transfer_validate"
    assert blueprint.node_map()["repo_runner"].meta["direct_sandbox_handler"] is True
    assert blueprint.node_map()["repo_runner"].meta["job_module"] == "dynaforge.case_studies.generic_repo_echo_job"


def test_closed_loop_spatial_panel_design_case_compiles_generic_closed_loop_pattern() -> None:
    config = load_hydra_config(overrides=["experiment=closed_loop_spatial_panel_design_case"])
    blueprint = build_blueprint_from_config(config)

    assert config.model.name == "gpt-5"
    assert blueprint.meta["workflow_pattern"] == "closed_loop_design_validate"
    assert blueprint.meta["compile_trace"]["selected_pattern"] == "closed_loop_design_validate"
    assert "repair_controller" in blueprint.node_map()
    assert blueprint.subgraphs[0].entry_nodes == ["repair_controller"]


def test_cell2location_repo_transfer_case_preserves_gpt5_thinking_default() -> None:
    config = load_hydra_config(overrides=["experiment=cell2location_repo_transfer_case"])
    blueprint = build_blueprint_from_config(config)

    assert config.model.name == "gpt-5"
    assert blueprint.meta["workflow_pattern"] == "repo_transfer_validate"
    assert blueprint.node_map()["repo_runner"].sandbox.image == "python:3.13"


def test_spatial_crispr_specialist_collaboration_case_compiles_generic_specialist_pattern() -> None:
    config = load_hydra_config(overrides=["experiment=spatial_crispr_specialist_collaboration_case"])
    blueprint = build_blueprint_from_config(config)

    assert config.model.name == "gpt-5"
    assert blueprint.meta["workflow_pattern"] == "specialist_assembly"
    assert blueprint.node_map()["specialist_a"].meta["job_module"] == "dynaforge.case_studies.spatialagent_context_job"
    assert blueprint.node_map()["specialist_b"].meta["job_module"] == "dynaforge.case_studies.biodiscovery_perturbation_job"
    assert blueprint.node_map()["specialist_b"].sandbox.image.startswith("conda-env:")


def test_scanpy_case_study_config_exposes_local_venv_sandbox() -> None:
    config = load_hydra_config(overrides=["experiment=scanpy_pbmc3k_case", "model=openai_gpt5_mini"])

    assert config.benchmark.kind == "case_study"
    assert config.benchmark.case_id == "scanpy_pbmc3k_umap"
    assert config.benchmark.sandbox.backend == "local_venv"
    assert config.benchmark.enable_refinement_subgraph is True
    assert config.benchmark.refinement_threshold == 8.0
    assert config.executor.allow_offline_fallback is False


def test_squidpy_case_study_config_exposes_local_venv_sandbox() -> None:
    config = load_hydra_config(overrides=["experiment=squidpy_visium_case", "model=openai_gpt5_mini"])

    assert config.benchmark.kind == "case_study"
    assert config.benchmark.case_id == "squidpy_visium_hne_spatial"
    assert config.benchmark.sandbox.backend == "local_venv"
    assert config.benchmark.enable_refinement_subgraph is True
    assert config.benchmark.refinement_threshold == 8.0
    assert config.executor.allow_offline_fallback is False


def test_squidpy_interactions_case_config_exposes_local_venv_sandbox() -> None:
    config = load_hydra_config(overrides=["experiment=squidpy_visium_interactions_case", "model=openai_gpt5_mini"])

    assert config.benchmark.kind == "case_study"
    assert config.benchmark.case_id == "squidpy_visium_hne_interactions"
    assert config.benchmark.sandbox.backend == "local_venv"
    assert config.benchmark.enable_refinement_subgraph is True
    assert config.benchmark.planner_mode == "llm_first"
    assert config.benchmark.analysis_config.compute_nhood_enrichment is True
    assert config.executor.allow_offline_fallback is False


def test_squidpy_interactions_fixed_case_config_exposes_simplified_workflow() -> None:
    config = load_hydra_config(overrides=["experiment=squidpy_visium_interactions_fixed_case", "model=openai_gpt5_mini"])

    assert config.benchmark.kind == "case_study"
    assert config.benchmark.case_id == "squidpy_visium_hne_interactions"
    assert config.benchmark.sandbox.backend == "local_venv"
    assert config.benchmark.enable_refinement_subgraph is False
    assert config.benchmark.planner_mode == "fixed"


def test_scanpy_paul15_case_study_config_exposes_local_venv_sandbox() -> None:
    config = load_hydra_config(overrides=["experiment=scanpy_paul15_trajectory_case", "model=openai_gpt5_mini"])

    assert config.benchmark.kind == "case_study"
    assert config.benchmark.case_id == "scanpy_paul15_trajectory"
    assert config.benchmark.sandbox.backend == "local_venv"
    assert config.benchmark.enable_refinement_subgraph is True
    assert config.benchmark.refinement_threshold == 8.0
    assert config.benchmark.planner_mode == "llm_first"
    assert config.benchmark.analysis_config.marker_top_n == 3
    assert config.executor.allow_offline_fallback is False


def test_scanpy_paul15_fixed_case_config_exposes_simplified_workflow() -> None:
    config = load_hydra_config(overrides=["experiment=scanpy_paul15_trajectory_fixed_case", "model=openai_gpt5_mini"])

    assert config.benchmark.kind == "case_study"
    assert config.benchmark.case_id == "scanpy_paul15_trajectory"
    assert config.benchmark.sandbox.backend == "local_venv"
    assert config.benchmark.enable_refinement_subgraph is False
    assert config.benchmark.planner_mode == "fixed"


def test_scanpy_paul15_paper_figure_case_config_exposes_extra_panel() -> None:
    config = load_hydra_config(overrides=["experiment=scanpy_paul15_paper_figure_case", "model=openai_gpt5_mini"])

    assert config.benchmark.kind == "case_study"
    assert config.benchmark.case_id == "scanpy_paul15_paper_figure"
    assert config.benchmark.sandbox.backend == "local_venv"
    assert config.benchmark.enable_refinement_subgraph is True
    assert config.benchmark.planner_mode == "llm_first"
    assert config.benchmark.analysis_config.dynamic_gene_panel_top_n == 4


def test_scanpy_paul15_paper_figure_fixed_case_config_exposes_simplified_workflow() -> None:
    config = load_hydra_config(
        overrides=["experiment=scanpy_paul15_paper_figure_fixed_case", "model=openai_gpt5_mini"]
    )

    assert config.benchmark.kind == "case_study"
    assert config.benchmark.case_id == "scanpy_paul15_paper_figure"
    assert config.benchmark.sandbox.backend == "local_venv"
    assert config.benchmark.enable_refinement_subgraph is False
    assert config.benchmark.planner_mode == "fixed"


def test_scanpy_moignard15_transfer_case_config_exposes_transfer_metadata() -> None:
    config = load_hydra_config(overrides=["experiment=scanpy_moignard15_method_transfer_case", "model=openai_gpt5_mini"])

    assert config.benchmark.kind == "case_study"
    assert config.benchmark.case_id == "scanpy_moignard15_method_transfer"
    assert config.benchmark.sandbox.backend == "local_venv"
    assert config.benchmark.analysis_config.dataset_id == "moignard15"
    assert config.benchmark.analysis_config.root_strategy == "dataset_default"
    assert config.benchmark.enable_refinement_subgraph is True


def test_scanpy_moignard15_transfer_fixed_case_config_exposes_simplified_workflow() -> None:
    config = load_hydra_config(
        overrides=["experiment=scanpy_moignard15_method_transfer_fixed_case", "model=openai_gpt5_mini"]
    )

    assert config.benchmark.kind == "case_study"
    assert config.benchmark.case_id == "scanpy_moignard15_method_transfer"
    assert config.benchmark.sandbox.backend == "local_venv"
    assert config.benchmark.enable_refinement_subgraph is False
    assert config.benchmark.planner_mode == "fixed"


def test_scanpy_paul15_specialized_collaboration_case_config_exposes_specialist_sandboxes() -> None:
    config = load_hydra_config(
        overrides=["experiment=scanpy_paul15_specialized_collaboration_case", "model=openai_gpt5_mini"]
    )

    assert config.benchmark.kind == "case_study"
    assert config.benchmark.case_id == "scanpy_paul15_specialized_collaboration"
    assert config.benchmark.paper_spec_sandbox.backend == "local_venv"
    assert config.benchmark.planner_sandbox.backend == "local_venv"
    assert config.benchmark.trajectory_sandbox.backend == "local_venv"
    assert config.benchmark.critic_sandbox.backend == "local_venv"
    assert config.benchmark.paper_spec_sandbox.env.SPECIALIZED_AGENT_ROLE == "paper_spec"
    assert config.benchmark.planner_sandbox.env.SPECIALIZED_AGENT_ROLE == "execution_planner"
    assert config.benchmark.critic_sandbox.env.SPECIALIZED_AGENT_ROLE == "figure_critic"


def test_scanpy_moignard15_specialized_collaboration_case_config_exposes_transfer_specialists() -> None:
    config = load_hydra_config(
        overrides=["experiment=scanpy_moignard15_specialized_collaboration_case", "model=openai_gpt5_mini"]
    )

    assert config.benchmark.kind == "case_study"
    assert config.benchmark.case_id == "scanpy_moignard15_specialized_collaboration"
    assert config.benchmark.analysis_config.dataset_id == "moignard15"
    assert config.benchmark.analysis_config.root_strategy == "dataset_default"
    assert config.benchmark.trajectory_sandbox.backend == "local_venv"
    assert config.benchmark.paper_spec_sandbox.env.SPECIALIZED_AGENT_ROLE == "paper_spec"


def test_scanpy_krumsiek11_transfer_case_config_exposes_transfer_metadata() -> None:
    config = load_hydra_config(overrides=["experiment=scanpy_krumsiek11_method_transfer_case", "model=openai_gpt5_mini"])

    assert config.benchmark.kind == "case_study"
    assert config.benchmark.case_id == "scanpy_krumsiek11_method_transfer"
    assert config.benchmark.sandbox.backend == "local_venv"
    assert config.benchmark.analysis_config.dataset_id == "krumsiek11"
    assert config.benchmark.analysis_config.root_strategy == "dataset_default"
    assert config.benchmark.enable_refinement_subgraph is True


def test_scanpy_krumsiek11_transfer_fixed_case_config_exposes_simplified_workflow() -> None:
    config = load_hydra_config(
        overrides=["experiment=scanpy_krumsiek11_method_transfer_fixed_case", "model=openai_gpt5_mini"]
    )

    assert config.benchmark.kind == "case_study"
    assert config.benchmark.case_id == "scanpy_krumsiek11_method_transfer"
    assert config.benchmark.sandbox.backend == "local_venv"
    assert config.benchmark.enable_refinement_subgraph is False
    assert config.benchmark.planner_mode == "fixed"


def test_scanpy_krumsiek11_specialized_collaboration_case_config_exposes_transfer_specialists() -> None:
    config = load_hydra_config(
        overrides=["experiment=scanpy_krumsiek11_specialized_collaboration_case", "model=openai_gpt5_mini"]
    )

    assert config.benchmark.kind == "case_study"
    assert config.benchmark.case_id == "scanpy_krumsiek11_specialized_collaboration"
    assert config.benchmark.analysis_config.dataset_id == "krumsiek11"
    assert config.benchmark.trajectory_sandbox.backend == "local_venv"
    assert config.benchmark.paper_spec_sandbox.env.SPECIALIZED_AGENT_ROLE == "paper_spec"


def test_visium_multi_agent_collaboration_case_config_exposes_dual_sandboxes() -> None:
    config = load_hydra_config(overrides=["experiment=visium_multi_agent_collaboration_case", "model=openai_gpt5_mini"])

    assert config.benchmark.kind == "case_study"
    assert config.benchmark.case_id == "visium_multi_agent_collaboration"
    assert config.benchmark.planner_mode == "fixed"
    assert config.benchmark.scanpy_sandbox.backend == "local_venv"
    assert config.benchmark.squidpy_sandbox.backend == "local_venv"
    assert config.benchmark.squidpy_sandbox.sandbox_id == "visium-squidpy-interactions-v2"
    assert ["python", "-m", "pip", "install", "pillow", "mcp", "leidenalg", "igraph"] in list(
        config.benchmark.squidpy_sandbox.bootstrap_cmds
    )
    assert config.benchmark.cluster_analysis_config.cluster_key == "scanpy_leiden"
    assert config.benchmark.interaction_analysis_config.cluster_key == "scanpy_leiden"


def test_visium_multi_agent_monolith_case_config_exposes_single_sandbox_baseline() -> None:
    config = load_hydra_config(overrides=["experiment=visium_multi_agent_monolith_case", "model=openai_gpt5_mini"])

    assert config.benchmark.kind == "case_study"
    assert config.benchmark.case_id == "visium_multi_agent_monolith"
    assert config.benchmark.planner_mode == "fixed"
    assert config.benchmark.squidpy_sandbox.backend == "local_venv"


def test_seqfish_multi_agent_collaboration_case_config_exposes_dual_sandboxes() -> None:
    config = load_hydra_config(overrides=["experiment=seqfish_multi_agent_collaboration_case", "model=openai_gpt5_mini"])

    assert config.benchmark.kind == "case_study"
    assert config.benchmark.case_id == "seqfish_multi_agent_collaboration"
    assert config.benchmark.planner_mode == "fixed"
    assert config.benchmark.cluster_analysis_config.dataset_id == "seqfish"
    assert config.benchmark.interaction_analysis_config.dataset_id == "seqfish"
    assert config.benchmark.scanpy_sandbox.backend == "local_venv"
    assert config.benchmark.squidpy_sandbox.backend == "local_venv"


def test_seqfish_multi_agent_monolith_case_config_exposes_single_sandbox_baseline() -> None:
    config = load_hydra_config(overrides=["experiment=seqfish_multi_agent_monolith_case", "model=openai_gpt5_mini"])

    assert config.benchmark.kind == "case_study"
    assert config.benchmark.case_id == "seqfish_multi_agent_monolith"
    assert config.benchmark.planner_mode == "fixed"
    assert config.benchmark.cluster_analysis_config.dataset_id == "seqfish"
    assert config.benchmark.interaction_analysis_config.dataset_id == "seqfish"
    assert config.benchmark.squidpy_sandbox.backend == "local_venv"


def test_squidpy_interactions_case_config_exposes_local_venv_sandbox() -> None:
    config = load_hydra_config(overrides=["experiment=squidpy_visium_interactions_case", "model=openai_gpt5_mini"])

    assert config.benchmark.kind == "case_study"
    assert config.benchmark.case_id == "squidpy_visium_hne_interactions"
    assert config.benchmark.sandbox.backend == "local_venv"
    assert config.benchmark.enable_refinement_subgraph is True
    assert config.benchmark.planner_mode == "llm_first"
    assert config.executor.allow_offline_fallback is False


def test_squidpy_interactions_fixed_case_config_exposes_simplified_workflow() -> None:
    config = load_hydra_config(
        overrides=["experiment=squidpy_visium_interactions_fixed_case", "model=openai_gpt5_mini"]
    )

    assert config.benchmark.kind == "case_study"
    assert config.benchmark.case_id == "squidpy_visium_hne_interactions"
    assert config.benchmark.sandbox.backend == "local_venv"
    assert config.benchmark.enable_refinement_subgraph is False
    assert config.benchmark.planner_mode == "fixed"


def test_squidpy_seqfish_transfer_case_config_exposes_dataset_aware_interaction_workflow() -> None:
    config = load_hydra_config(overrides=["experiment=squidpy_seqfish_method_transfer_case", "model=openai_gpt5_mini"])

    assert config.benchmark.kind == "case_study"
    assert config.benchmark.case_id == "squidpy_seqfish_method_transfer"
    assert config.benchmark.sandbox.backend == "local_venv"
    assert config.benchmark.analysis_config.dataset_id == "seqfish"
    assert config.benchmark.analysis_config.cluster_key == "celltype_mapped_refined"
    assert config.benchmark.planner_mode == "fixed"


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
