from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from agentcoop.experiment_cli import main as experiment_main
from agentcoop.experiment_runner import (
    RunRecorder,
    _apply_shard,
    _build_generic_case_study_blueprint,
    _generic_specialist_router_handler,
    _build_humaneval_task_handlers,
    _build_math_task_handlers,
    _extract_test_failure_details,
    _build_visium_multi_agent_collaboration_blueprint,
    _humaneval_public_test_handler,
    _math_program_exec_handler,
    _math_selector_handler,
    _scanpy_planner_handler,
    _summarize_generic_case_study,
    _summarize_scanpy_paul15_case_study,
    _summarize_scanpy_case_study,
    _summarize_squidpy_case_study,
    aggregate_humaneval_runs,
    aggregate_humaneval_repeats,
    aggregate_math_runs,
    aggregate_math_repeats,
    build_humaneval_task_blueprint,
    build_math_task_blueprint,
)
from agentcoop.benchmarks import prepare_case_study_assets
from agentcoop.config import build_blueprint_from_config, build_executor_from_config, load_hydra_config, resolve_hydra_config
from agentcoop.ir.schema import FailureType, NodeSpec, SandboxSpec
from agentcoop.runtime.reports import ExecutionCost, ExecutionReport, NodeExecutionResult


def test_apply_shard_slices_index_list() -> None:
    indices = list(range(10))

    assert _apply_shard(indices, num_shards=1, shard_index=0) == indices
    assert _apply_shard(indices, num_shards=3, shard_index=0) == [0, 3, 6, 9]
    assert _apply_shard(indices, num_shards=3, shard_index=1) == [1, 4, 7]
    assert _apply_shard(indices, num_shards=3, shard_index=2) == [2, 5, 8]


def test_generic_case_study_blueprint_injects_assets(tmp_path) -> None:
    config = load_hydra_config(overrides=["experiment=generic_repo_transfer_smoke"])
    resolved = resolve_hydra_config(config)
    case_assets = prepare_case_study_assets(resolved)
    recorder = RunRecorder("generic-case-study", root_dir=tmp_path / "run")
    blueprint = _build_generic_case_study_blueprint(
        resolved,
        benchmark_settings=resolved["benchmark"],
        case_assets=case_assets,
        recorder=recorder,
    )

    assert blueprint.meta["case_runner_mode"] == "compiled_generic"
    assert blueprint.task.hints["case_id"] == "generic_repo_transfer_smoke"
    assert blueprint.task.hints["candidate_repos"][0]["name"] == "local_project"
    assert blueprint.task.hints["output_dir"].startswith(str(recorder.root))


def test_generic_case_study_executes_direct_sandbox_tool(tmp_path) -> None:
    class FakeSandboxRunner:
        def is_materialized(self, sandbox_id: str) -> bool:
            return True

        def ensure_materialized(self, sandbox):
            return SimpleNamespace(sandbox_id=sandbox.sandbox_id)

        def materialize_server_ref(self, server_ref):
            return server_ref

        def cleanup_all(self) -> None:
            return None

        def run_in_sandbox(self, sandbox, cmd, *, timeout_s=None, env=None):
            input_json = Path(cmd[cmd.index("--input-json") + 1])
            output_json = Path(cmd[cmd.index("--output-json") + 1])
            payload = json.loads(input_json.read_text(encoding="utf-8"))
            inputs = payload["inputs"]
            selected_repo = inputs["task"]["hints"]["candidate_repos"][0]["name"]
            report_path = Path(inputs["task"]["hints"]["output_dir"]) / "fake_report.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps({"selected_repo": selected_repo}, ensure_ascii=True), encoding="utf-8")
            output_json.write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "outputs": {
                            "selected_repo": selected_repo,
                            "summary": "ok",
                            "report_path": str(report_path),
                        },
                        "summary": {"report_path": str(report_path)},
                        "artifacts": [{"path": str(report_path), "mime": "application/json"}],
                        "confidence": 0.9,
                        "trace": {"fake_direct_sandbox": True},
                    },
                    ensure_ascii=True,
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(cmd, 0, "", "")

    config = load_hydra_config(overrides=["experiment=generic_repo_transfer_smoke"])
    resolved = resolve_hydra_config(config)
    case_assets = prepare_case_study_assets(resolved)
    recorder = RunRecorder("generic-case-study", root_dir=tmp_path / "run")
    blueprint = _build_generic_case_study_blueprint(
        resolved,
        benchmark_settings=resolved["benchmark"],
        case_assets=case_assets,
        recorder=recorder,
    )
    executor = build_executor_from_config(
        resolved,
        artifact_root=tmp_path / "artifacts",
        sandbox_runner=FakeSandboxRunner(),
    )
    handlers = {
        "repo_scout": lambda node, inputs, context: NodeExecutionResult(
            outputs={
                "selected_repo": "local_project",
                "repo_candidates": [{"name": "local_project"}],
                "analysis_config": {},
                "summary": "selected local project",
            },
            confidence=0.95,
        ),
        "validator": lambda node, inputs, context: NodeExecutionResult(
            outputs={
                "overall_verdict": "accept",
                "artifact_completeness": 1.0,
                "should_refine": False,
                "concerns": [],
            },
            confidence=0.94,
        ),
    }

    report = executor.execute_blueprint(blueprint, handlers=handlers)
    summary = _summarize_generic_case_study(report, blueprint=blueprint, case_assets=case_assets)

    assert report.success
    assert "repo_runner" in summary["workflow_signature"]
    assert summary["selected_repos"]["repo_scout"] == "local_project"
    assert summary["artifact_paths"]


def test_generic_specialist_router_handler_selects_candidate_specialists() -> None:
    result = _generic_specialist_router_handler(
        NodeSpec(node_id="specialist_router", kind="router", role="SpecialistRouter", model={"provider": "openai", "name": "gpt-5"}),
        {
            "task": {
                "hints": {
                    "candidate_repos": [
                        {"name": "SpatialAgent"},
                        {"name": "BioDiscoveryAgent"},
                    ]
                }
            }
        },
        None,
    )

    assert result.outputs["selected_specialists"] == ["SpatialAgent", "BioDiscoveryAgent"]
    assert "specialist_a_delivers" in result.outputs["handoff_contract"]
    assert result.trace["deterministic_router"] is True


def test_extract_test_failure_details_parses_call_and_expected_value() -> None:
    result = _extract_test_failure_details(
        "Traceback (most recent call last):\n"
        "  File '/tmp/check.py', line 12, in <module>\n"
        "    assert candidate([1, 2], 9) == 2, \"Error\"\n"
        "AssertionError: Error\n"
    )

    assert result["failing_assertion"] == 'assert candidate([1, 2], 9) == 2, "Error"'
    assert result["failing_call"] == "candidate([1, 2], 9)"
    assert result["assertion_operator"] == "=="
    assert result["expected_repr"] == "2"


def test_build_math_task_blueprint_enables_challenger_solver() -> None:
    config = load_hydra_config(overrides=["experiment=math_v2", "workflow_design.pattern_overrides.reason_execute_select.enable_challenger_solver=true"])
    resolved = resolve_hydra_config(config)
    base_blueprint = build_blueprint_from_config(resolved)
    sample = {
        "id": "aflow_validate:0000",
        "question": "What is 1+1?",
        "prompt": "What is 1+1?",
        "gold_answer": "2",
        "type": "Prealgebra",
        "level": "Level 5",
        "source_config": "aflow_validate",
    }

    blueprint = build_math_task_blueprint(base_blueprint, sample)

    assert "solver_challenger" in blueprint.node_map()


def test_experiment_cli_parses_math_arguments(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr("agentcoop.experiment_cli.load_hydra_config", lambda overrides: {"ok": True})

    def fake_run(config, **kwargs):
        captured.update(kwargs)
        return {"task_count": 3}

    monkeypatch.setattr("agentcoop.experiment_cli.run_math_experiment", fake_run)

    exit_code = experiment_main(["math", "--subset", "test", "--task-ids", "a,b", "--num-shards", "4", "--shard-index", "2"])

    assert exit_code == 0
    assert captured["subset"] == "test"
    assert captured["task_ids"] == ["a", "b"]
    assert captured["num_shards"] == 4
    assert captured["shard_index"] == 2


def test_experiment_cli_parses_humaneval_arguments(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr("agentcoop.experiment_cli.load_hydra_config", lambda overrides: {"ok": True})

    def fake_run(config, **kwargs):
        captured.update(kwargs)
        return {"task_count": 2}

    monkeypatch.setattr("agentcoop.experiment_cli.run_humaneval_experiment", fake_run)

    exit_code = experiment_main(["humaneval", "--subset", "validation", "--resume-run-dir", "/tmp/he-run"])

    assert exit_code == 0
    assert captured["subset"] == "validation"
    assert captured["resume_run_dir"] == "/tmp/he-run"


def test_experiment_cli_runs_math_aggregate(monkeypatch) -> None:
    monkeypatch.setattr("agentcoop.experiment_cli.aggregate_math_runs", lambda run_dirs, base_dir: {"task_count": 2})
    exit_code = experiment_main(["math-aggregate", "/tmp/run-a", "/tmp/run-b"])
    assert exit_code == 0


def test_experiment_cli_runs_humaneval_aggregate(monkeypatch) -> None:
    monkeypatch.setattr("agentcoop.experiment_cli.aggregate_humaneval_runs", lambda run_dirs, base_dir: {"task_count": 2})
    exit_code = experiment_main(["humaneval-aggregate", "/tmp/run-a", "/tmp/run-b"])
    assert exit_code == 0


def test_aggregate_math_runs_merges_task_results(tmp_path) -> None:
    run_a = tmp_path / "run-a"
    run_b = tmp_path / "run-b"
    for run_dir, task_id in ((run_a, "m1"), (run_b, "m2")):
        (run_dir / "eval").mkdir(parents=True)
        (run_dir / "summaries").mkdir(parents=True)
        (run_dir / "eval" / "task_results.json").write_text(
            json.dumps(
                [
                    {
                        "task_id": task_id,
                        "sample_index": 0 if task_id == "m1" else 1,
                        "correct": True,
                        "workflow_signature": "solver",
                        "failure_type": "none",
                        "activated_gates": [],
                        "cost": {"usd": 0.1},
                        "activated_node_count": 1,
                        "activated_subgraph_count": 0,
                    }
                ]
            ),
            encoding="utf-8",
        )
        (run_dir / "summaries" / "summary.json").write_text(
            json.dumps({"manifest": {"dataset_id": "math", "metric": "solve_rate"}}),
            encoding="utf-8",
        )

    summary = aggregate_math_runs([run_a, run_b], base_dir=tmp_path / "agg")

    assert summary["task_count"] == 2
    assert summary["solve_rate"] == 1.0


def test_aggregate_math_runs_regrades_symbolic_equivalence(tmp_path) -> None:
    run_dir = tmp_path / "run-a"
    (run_dir / "eval").mkdir(parents=True)
    (run_dir / "summaries").mkdir(parents=True)
    (run_dir / "eval" / "task_results.json").write_text(
        json.dumps(
            [
                {
                    "task_id": "m1",
                    "sample_index": 0,
                    "correct": False,
                    "workflow_signature": "programmer->solver->program_exec->selector",
                    "failure_type": "incorrect_answer",
                    "activated_gates": [],
                    "cost": {"usd": 0.1},
                    "activated_node_count": 4,
                    "activated_subgraph_count": 0,
                    "prediction_answer": "12*x - 34",
                    "gold_answer": "-34 + 12x",
                }
            ]
        ),
        encoding="utf-8",
    )
    (run_dir / "summaries" / "summary.json").write_text(
        json.dumps({"manifest": {"dataset_id": "math", "metric": "solve_rate"}}),
        encoding="utf-8",
    )

    summary = aggregate_math_runs([run_dir], base_dir=tmp_path / "agg")

    assert summary["task_count"] == 1
    assert summary["solve_rate"] == 1.0


def test_aggregate_humaneval_runs_merges_task_results(tmp_path) -> None:
    run_a = tmp_path / "run-a"
    run_b = tmp_path / "run-b"
    for run_dir, task_id in ((run_a, "h1"), (run_b, "h2")):
        (run_dir / "eval").mkdir(parents=True)
        (run_dir / "summaries").mkdir(parents=True)
        (run_dir / "eval" / "task_results.json").write_text(
            json.dumps(
                [
                    {
                        "task_id": task_id,
                        "sample_index": 0 if task_id == "h1" else 1,
                        "correct": True,
                        "workflow_signature": "coder",
                        "failure_type": "none",
                        "activated_gates": [],
                        "cost": {"usd": 0.1},
                        "activated_node_count": 1,
                        "activated_subgraph_count": 0,
                    }
                ]
            ),
            encoding="utf-8",
        )
        (run_dir / "summaries" / "summary.json").write_text(
            json.dumps({"manifest": {"dataset_id": "humaneval", "metric": "pass@1"}}),
            encoding="utf-8",
        )

    summary = aggregate_humaneval_runs([run_a, run_b], base_dir=tmp_path / "agg")

    assert summary["task_count"] == 2
    assert summary["pass_at_1"] == 1.0


def test_aggregate_math_repeats_averages_repeat_summaries(tmp_path) -> None:
    repeat_dirs = []
    for index, solve_rate in enumerate((0.9, 0.8, 1.0), start=1):
        repeat_dir = tmp_path / f"repeat-{index}"
        source_dir = repeat_dir / "source-shard"
        (repeat_dir / "summaries").mkdir(parents=True)
        (source_dir / "summaries").mkdir(parents=True)
        (repeat_dir / "summaries" / "summary.json").write_text(
            json.dumps(
                {
                    "benchmark": "math",
                    "task_count": 486,
                    "solve_rate": solve_rate,
                    "average_usd": 0.001 * index,
                    "average_input_tokens": 10 * index,
                    "average_output_tokens": 5 * index,
                    "average_latency_s": 2.0 * index,
                    "source_run_dirs": [str(source_dir)],
                }
            ),
            encoding="utf-8",
        )
        (source_dir / "summaries" / "summary.json").write_text(
            json.dumps(
                {
                    "benchmark": "math",
                    "subset": "test",
                    "manifest": {"dataset_id": "math", "record_count": 605, "test_count": 486, "metric": "solve_rate"},
                }
            ),
            encoding="utf-8",
        )
        (source_dir / "summaries" / "run_metadata.json").write_text(
            json.dumps({"model_name": "gpt-4o-mini"}),
            encoding="utf-8",
        )
        repeat_dirs.append(repeat_dir)

    summary = aggregate_math_repeats(repeat_dirs, base_dir=tmp_path / "agg-repeat")

    assert summary["repeat_count"] == 3
    assert summary["subset"] == "test"
    assert summary["model_name"] == "gpt-4o-mini"
    assert summary["solve_rate"] == 0.9
    assert summary["solve_rate_std"] > 0


def test_aggregate_humaneval_repeats_averages_repeat_summaries(tmp_path) -> None:
    repeat_dirs = []
    for index, pass_at_1 in enumerate((0.85, 0.9, 0.95), start=1):
        repeat_dir = tmp_path / f"repeat-h-{index}"
        source_dir = repeat_dir / "source-shard"
        (repeat_dir / "summaries").mkdir(parents=True)
        (source_dir / "summaries").mkdir(parents=True)
        (repeat_dir / "summaries" / "summary.json").write_text(
            json.dumps(
                {
                    "benchmark": "humaneval",
                    "task_count": 131,
                    "pass_at_1": pass_at_1,
                    "average_usd": 0.002 * index,
                    "average_input_tokens": 20 * index,
                    "average_output_tokens": 7 * index,
                    "average_latency_s": 3.0 * index,
                    "source_run_dirs": [str(source_dir)],
                }
            ),
            encoding="utf-8",
        )
        (source_dir / "summaries" / "summary.json").write_text(
            json.dumps(
                {
                    "benchmark": "humaneval",
                    "subset": "test",
                    "manifest": {"dataset_id": "humaneval", "record_count": 164, "test_count": 131, "metric": "pass@1"},
                }
            ),
            encoding="utf-8",
        )
        (source_dir / "summaries" / "run_metadata.json").write_text(
            json.dumps({"model_name": "gpt-4o-mini"}),
            encoding="utf-8",
        )
        repeat_dirs.append(repeat_dir)

    summary = aggregate_humaneval_repeats(repeat_dirs, base_dir=tmp_path / "agg-repeat")

    assert summary["repeat_count"] == 3
    assert summary["subset"] == "test"
    assert summary["model_name"] == "gpt-4o-mini"
    assert round(summary["pass_at_1"], 4) == 0.9


def test_build_math_task_blueprint_does_not_leak_gold_answer() -> None:
    from agentcoop.config import load_hydra_config, build_blueprint_from_config

    config = load_hydra_config(overrides=["experiment=math_v2", "model=openai_gpt4o_mini"])
    blueprint = build_blueprint_from_config(config)
    task_blueprint = build_math_task_blueprint(
        blueprint,
        {
            "id": "counting_and_probability:0000",
            "prompt": "Solve x + 1 = 2.",
            "question": "Solve x + 1 = 2.",
            "type": "Counting & Probability",
            "level": "Level 5",
            "gold_answer": "1",
        },
    )

    assert "gold_answer" not in task_blueprint.task.hints


def test_math_program_exec_handler_runs_program() -> None:
    node = NodeSpec.model_validate(
        {
            "node_id": "program_exec",
            "kind": "tool",
            "role": "ProgramExecutor",
            "tools": [{"server": "direct", "tool": "math_program_exec"}],
            "meta": {"direct_sandbox_handler": True},
        }
    )
    result = _math_program_exec_handler(
        node,
        {"program": "FINAL_ANSWER = 42", "candidate_answer": "42"},
        SimpleNamespace(sandbox_runner=SimpleNamespace(ensure_materialized=lambda sandbox: SimpleNamespace(python_bin=""))),
    )

    assert result.failure_type == FailureType.none
    assert result.outputs["passed"] is True
    assert result.outputs["executed_answer"] == "42"


def test_humaneval_public_test_handler_runs_assertions() -> None:
    node = NodeSpec.model_validate(
        {
            "node_id": "public_test_runner",
            "kind": "tool",
            "role": "PublicTestRunner",
            "tools": [{"server": "direct", "tool": "humaneval_public_test_runner"}],
            "meta": {"direct_sandbox_handler": True},
        }
    )
    sample = {
        "entry_point": "add",
        "public_tests": ["assert candidate(2, 5) == 7"],
    }
    result = _humaneval_public_test_handler(
        node,
        {"candidate_code": "def add(a, b):\n    return a + b\n"},
        SimpleNamespace(sandbox_runner=SimpleNamespace(ensure_materialized=lambda sandbox: SimpleNamespace(python_bin=""))),
        sample=sample,
    )

    assert result.failure_type == FailureType.none
    assert result.outputs["passed"] is True
    assert result.outputs["public_test_count"] == 1


def test_build_math_and_humaneval_handlers_detect_v2_nodes() -> None:
    from agentcoop.config import load_hydra_config, build_blueprint_from_config

    math_config = load_hydra_config(overrides=["experiment=math_v2", "model=openai_gpt4o_mini"])
    math_blueprint = build_blueprint_from_config(math_config)
    math_handlers = _build_math_task_handlers(math_config, math_blueprint, {"id": "m"})

    humaneval_config = load_hydra_config(overrides=["experiment=humaneval_v2", "model=openai_gpt4o_mini"])
    humaneval_blueprint = build_blueprint_from_config(humaneval_config)
    humaneval_handlers = _build_humaneval_task_handlers(
        humaneval_config,
        humaneval_blueprint,
        {"id": "h", "entry_point": "foo", "public_tests": []},
    )

    assert "program_exec" in math_handlers
    assert "public_test_runner" in humaneval_handlers
    assert "retest_runner" in humaneval_handlers
    assert "rewrite_test_runner" in humaneval_handlers


def test_scanpy_case_planner_prefers_llm_result_when_valid() -> None:
    node = NodeSpec.model_validate(
        {
            "node_id": "planner",
            "kind": "agent",
            "role": "BioPlanner",
            "model": {"provider": "openai", "name": "gpt-4.1-mini"},
        }
    )
    inputs = {
        "task": {
            "hints": {
                "candidate_repos": [{"name": "scanpy"}, {"name": "scanpy-tutorials"}],
                "analysis_config": {"n_neighbors": 10, "min_genes": 200},
            }
        }
    }

    class FakeRouter:
        def run_node(self, node, inputs, context):
            return NodeExecutionResult(
                outputs={
                    "selected_repo": "scanpy-tutorials",
                    "analysis_config": {"n_neighbors": 22},
                    "summary": "Use the tutorial repo.",
                },
                trace={"live_llm": True},
                confidence=0.81,
            )

    result = _scanpy_planner_handler(node, inputs, SimpleNamespace(llm_router=FakeRouter()))

    assert result.failure_type == FailureType.none
    assert result.outputs["selected_repo"] == "scanpy-tutorials"
    assert result.outputs["analysis_config"]["n_neighbors"] == 22
    assert result.outputs["analysis_config"]["min_genes"] == 200
    assert result.trace["case_planner_strategy"] == "llm"


def test_case_planner_drops_runtime_only_analysis_keys() -> None:
    node = NodeSpec.model_validate(
        {
            "node_id": "planner",
            "kind": "agent",
            "role": "BioPlanner",
            "model": {"provider": "openai", "name": "gpt-4.1-mini"},
        }
    )
    inputs = {
        "task": {
            "hints": {
                "candidate_repos": [{"name": "scanpy"}, {"name": "scanpy-tutorials"}],
                "analysis_config": {"n_neighbors": 10, "n_pcs": 30},
            }
        }
    }

    class FakeRouter:
        def run_node(self, node, inputs, context):
            return NodeExecutionResult(
                outputs={
                    "selected_repo": "scanpy",
                    "analysis_config": {
                        "n_neighbors": 18,
                        "figure_path": "/tmp/generated.png",
                        "output_dir": "/tmp/out",
                    },
                    "summary": "Use scanpy.",
                },
                trace={"live_llm": True},
                confidence=0.79,
            )

    result = _scanpy_planner_handler(node, inputs, SimpleNamespace(llm_router=FakeRouter()))

    assert result.outputs["analysis_config"] == {"n_neighbors": 18, "n_pcs": 30}


def test_scanpy_paul15_planner_can_run_in_fixed_mode() -> None:
    from agentcoop.experiment_runner import _scanpy_paul15_planner_handler

    node = NodeSpec.model_validate(
        {
            "node_id": "planner",
            "kind": "agent",
            "role": "BioPlanner",
            "model": {"provider": "openai", "name": "gpt-4.1-mini"},
        }
    )
    inputs = {
        "task": {
            "hints": {
                "planner_mode": "fixed",
                "candidate_repos": [{"name": "scanpy"}, {"name": "scanpy-tutorials"}],
                "analysis_config": {"n_neighbors": 12, "n_pcs": 30},
            }
        }
    }

    class UnusedRouter:
        def run_node(self, node, inputs, context):
            raise AssertionError("fixed planner mode should bypass llm_router")

    result = _scanpy_paul15_planner_handler(node, inputs, SimpleNamespace(llm_router=UnusedRouter()))

    assert result.failure_type == FailureType.none
    assert result.outputs["selected_repo"] == "scanpy"
    assert result.trace["deterministic_case_planner"] is True


def test_squidpy_planner_can_run_in_fixed_mode() -> None:
    from agentcoop.experiment_runner import _squidpy_planner_handler

    node = NodeSpec.model_validate(
        {
            "node_id": "planner",
            "kind": "agent",
            "role": "SpatialPlanner",
            "model": {"provider": "openai", "name": "gpt-4.1-mini"},
        }
    )
    inputs = {
        "task": {
            "hints": {
                "planner_mode": "fixed",
                "case_id": "squidpy_visium_hne_interactions",
                "candidate_repos": [{"name": "squidpy"}, {"name": "scanpy"}],
                "analysis_config": {"compute_nhood_enrichment": True, "interaction_top_n": 5},
            }
        }
    }

    class UnusedRouter:
        def run_node(self, node, inputs, context):
            raise AssertionError("fixed planner mode should bypass llm_router")

    result = _squidpy_planner_handler(node, inputs, SimpleNamespace(llm_router=UnusedRouter()))

    assert result.failure_type == FailureType.none
    assert result.outputs["selected_repo"] == "squidpy"
    assert result.trace["deterministic_case_planner"] is True


def test_visium_multi_agent_planner_can_run_in_fixed_mode() -> None:
    from agentcoop.experiment_runner import _visium_multi_agent_planner_handler

    node = NodeSpec.model_validate(
        {
            "node_id": "planner",
            "kind": "agent",
            "role": "MultiAgentPlanner",
            "model": {"provider": "openai", "name": "gpt-4.1-mini"},
        }
    )
    inputs = {
        "task": {
            "hints": {
                "planner_mode": "fixed",
                "scanpy_candidate_repos": [{"name": "scanpy"}, {"name": "scanpy-tutorials"}],
                "squidpy_candidate_repos": [{"name": "squidpy"}],
                "cluster_analysis_config": {"cluster_key": "scanpy_leiden"},
                "interaction_analysis_config": {"cluster_key": "scanpy_leiden"},
            }
        }
    }

    class UnusedRouter:
        def run_node(self, node, inputs, context):
            raise AssertionError("fixed planner mode should bypass llm_router")

    result = _visium_multi_agent_planner_handler(node, inputs, SimpleNamespace(llm_router=UnusedRouter()))

    assert result.failure_type == FailureType.none
    assert result.outputs["scanpy_repo"] == "scanpy"
    assert result.outputs["squidpy_repo"] == "squidpy"
    assert result.trace["deterministic_case_planner"] is True


def test_visium_collaboration_blueprint_reuses_prepared_raw_data_path(tmp_path) -> None:
    methods_path = tmp_path / "methods.txt"
    target_path = tmp_path / "target.txt"
    repos_path = tmp_path / "candidate_repos.json"
    raw_data_path = tmp_path / "prepared_raw.h5ad"
    reference_summary_path = tmp_path / "reference_summary.json"

    methods_path.write_text("methods", encoding="utf-8")
    target_path.write_text("target", encoding="utf-8")
    repos_path.write_text(
        json.dumps(
            [
                {"name": "scanpy", "url": "https://example.com/scanpy.git"},
                {"name": "scanpy-tutorials", "url": "https://example.com/scanpy-tutorials.git"},
                {"name": "squidpy", "url": "https://example.com/squidpy.git"},
            ]
        ),
        encoding="utf-8",
    )
    raw_data_path.write_text("placeholder", encoding="utf-8")
    reference_summary_path.write_text("{}", encoding="utf-8")

    config = {
        "model": {"provider": "openai", "name": "gpt-4.1-mini"},
        "review_model": {"provider": "openai", "name": "gpt-4.1-mini"},
        "blueprint": {"budget": {}},
    }
    benchmark_settings = {
        "title": "Visium collaboration test",
        "planner_mode": "fixed",
        "cluster_analysis_config": {"cluster_key": "scanpy_leiden"},
        "interaction_analysis_config": {"cluster_key": "scanpy_leiden"},
    }
    case_assets = {
        "case_id": "visium_multi_agent_collaboration",
        "methods_excerpt_path": str(methods_path),
        "target_figure_description_path": str(target_path),
        "candidate_repos_path": str(repos_path),
    }
    reference_assets = {
        "reference_summary_path": str(reference_summary_path),
        "raw_data_path": str(raw_data_path),
    }
    recorder = RunRecorder("visium-collab-test", root_dir=tmp_path / "run")
    scanpy_sandbox = SandboxSpec.model_validate(
        {"sandbox_id": "scanpy-test", "backend": "local_venv", "image": "python:3.11"}
    )
    squidpy_sandbox = SandboxSpec.model_validate(
        {"sandbox_id": "squidpy-test", "backend": "local_venv", "image": "python:3.11"}
    )

    blueprint = _build_visium_multi_agent_collaboration_blueprint(
        config,
        benchmark_settings=benchmark_settings,
        case_assets=case_assets,
        reference_assets=reference_assets,
        recorder=recorder,
        scanpy_sandbox=scanpy_sandbox,
        squidpy_sandbox=squidpy_sandbox,
    )

    assert blueprint.task.hints["raw_data_path"] == str(raw_data_path.resolve())
    assert str(recorder.artifacts_dir.resolve()) not in blueprint.task.hints["raw_data_path"]


def test_seqfish_collaboration_blueprint_exposes_dataset_aware_titles(tmp_path) -> None:
    methods_path = tmp_path / "methods.txt"
    target_path = tmp_path / "target.txt"
    repos_path = tmp_path / "repos.json"
    reference_summary_path = tmp_path / "reference_summary.json"
    raw_data_path = tmp_path / "seqfish_raw.h5ad"
    methods_path.write_text("methods", encoding="utf-8")
    target_path.write_text("target", encoding="utf-8")
    repos_path.write_text(
        json.dumps(
            [
                {"name": "scanpy", "url": "https://github.com/scverse/scanpy.git"},
                {"name": "squidpy", "url": "https://github.com/scverse/squidpy.git"},
            ]
        ),
        encoding="utf-8",
    )
    raw_data_path.write_text("placeholder", encoding="utf-8")
    reference_summary_path.write_text("{}", encoding="utf-8")

    config = {
        "model": {"provider": "openai", "name": "gpt-4.1-mini"},
        "review_model": {"provider": "openai", "name": "gpt-4.1-mini"},
        "blueprint": {"budget": {}},
    }
    benchmark_settings = {
        "title": "seqFISH collaboration test",
        "planner_mode": "fixed",
        "cluster_analysis_config": {"dataset_id": "seqfish", "cluster_key": "scanpy_leiden"},
        "interaction_analysis_config": {"dataset_id": "seqfish", "cluster_key": "scanpy_leiden"},
    }
    case_assets = {
        "case_id": "seqfish_multi_agent_collaboration",
        "dataset_id": "seqfish",
        "dataset_label": "seqFISH",
        "methods_excerpt_path": str(methods_path),
        "target_figure_description_path": str(target_path),
        "candidate_repos_path": str(repos_path),
    }
    reference_assets = {
        "reference_summary_path": str(reference_summary_path),
        "raw_data_path": str(raw_data_path),
    }
    recorder = RunRecorder("seqfish-collab-test", root_dir=tmp_path / "run")
    scanpy_sandbox = SandboxSpec.model_validate(
        {"sandbox_id": "scanpy-test", "backend": "local_venv", "image": "python:3.11"}
    )
    squidpy_sandbox = SandboxSpec.model_validate(
        {"sandbox_id": "squidpy-test", "backend": "local_venv", "image": "python:3.11"}
    )

    blueprint = _build_visium_multi_agent_collaboration_blueprint(
        config,
        benchmark_settings=benchmark_settings,
        case_assets=case_assets,
        reference_assets=reference_assets,
        recorder=recorder,
        scanpy_sandbox=scanpy_sandbox,
        squidpy_sandbox=squidpy_sandbox,
    )

    assert blueprint.task.task_id == "seqfish_multi_agent_collaboration_case"
    assert blueprint.task.hints["dataset_id"] == "seqfish"
    assert blueprint.task.hints["interaction_title"] == "seqFISH Neighborhood Enrichment"
    assert blueprint.task.hints["raw_data_path"] == str(raw_data_path.resolve())


def test_scanpy_case_planner_falls_back_on_llm_failure() -> None:
    node = NodeSpec.model_validate(
        {
            "node_id": "planner",
            "kind": "agent",
            "role": "BioPlanner",
            "model": {"provider": "openai", "name": "gpt-4.1-mini"},
        }
    )
    inputs = {
        "task": {
            "hints": {
                "candidate_repos": [{"name": "scanpy"}, {"name": "scanpy-tutorials"}],
                "analysis_config": {"n_neighbors": 10},
            }
        }
    }

    class FakeRouter:
        def run_node(self, node, inputs, context):
            return NodeExecutionResult(
                failure_type=FailureType.tool_runtime_error,
                error="backend unavailable",
            )

    result = _scanpy_planner_handler(node, inputs, SimpleNamespace(llm_router=FakeRouter()))

    assert result.failure_type == FailureType.none
    assert result.outputs["selected_repo"] == "scanpy"
    assert result.outputs["analysis_config"]["n_neighbors"] == 10
    assert result.trace["case_planner_strategy"] == "deterministic_fallback"


def test_scanpy_case_summary_prefers_refined_outputs() -> None:
    report = ExecutionReport(
        workflow_id="wf",
        task_id="scanpy-case",
        success=True,
        failure_type=FailureType.none,
        traces=[],
        hard_checks=[],
        soft_judges=[],
        contract_violations=[],
        activated_gates=["case_refinement_gate"],
        active_subgraphs=["case_refinement"],
        cost={},
        confidence=0.9,
        summary="ok",
        node_results={
            "planner": NodeExecutionResult(outputs={"selected_repo": "scanpy", "analysis_config": {}, "summary": "plan"}),
            "scanpy_tool": NodeExecutionResult(
                outputs={"result": {"selected_repo": "scanpy", "status": "ok", "summary": {"figure_path": "base.png"}}}
            ),
            "bio_reviewer": NodeExecutionResult(outputs={"overall_verdict": "weak"}),
            "case_refiner": NodeExecutionResult(
                outputs={"selected_repo": "scanpy-tutorials", "analysis_config": {"n_neighbors": 18}, "summary": "refine"}
            ),
            "scanpy_tool_refined": NodeExecutionResult(
                outputs={
                    "result": {
                        "selected_repo": "scanpy-tutorials",
                        "status": "ok",
                        "summary": {
                            "figure_path": "refined.png",
                            "summary_path": "refined.json",
                            "raw_data_path": "raw.h5ad",
                            "cluster_count": 8,
                        },
                    }
                }
            ),
            "bio_reviewer_refined": NodeExecutionResult(outputs={"overall_verdict": "strong"}),
        },
    )

    summary = _summarize_scanpy_case_study(
        report,
        case_assets={"case_id": "scanpy_pbmc3k_umap"},
        reference_assets={"reference_figure_path": "ref.png", "reference_summary_path": "ref.json"},
    )

    assert summary["used_refinement"] is True
    assert summary["generated_figure_path"] == "refined.png"
    assert summary["tool_output"]["selected_repo"] == "scanpy-tutorials"
    assert summary["review_output"]["overall_verdict"] == "strong"


def test_scanpy_paul15_case_summary_prefers_refined_outputs() -> None:
    report = ExecutionReport(
        workflow_id="wf",
        task_id="scanpy-paul15-case",
        success=True,
        failure_type=FailureType.none,
        traces=[],
        hard_checks=[],
        soft_judges=[],
        contract_violations=[],
        activated_gates=["case_refinement_gate"],
        active_subgraphs=["case_refinement"],
        cost={},
        confidence=0.92,
        summary="ok",
        node_results={
            "planner": NodeExecutionResult(outputs={"selected_repo": "scanpy", "analysis_config": {}, "summary": "plan"}),
            "scanpy_trajectory_tool": NodeExecutionResult(
                outputs={"result": {"selected_repo": "scanpy", "status": "ok", "summary": {"figure_path": "base.png"}}}
            ),
            "bio_reviewer": NodeExecutionResult(outputs={"overall_verdict": "weak"}),
            "case_refiner": NodeExecutionResult(
                outputs={"selected_repo": "scanpy-tutorials", "analysis_config": {"n_neighbors": 18}, "summary": "refine"}
            ),
            "scanpy_trajectory_tool_refined": NodeExecutionResult(
                outputs={
                    "result": {
                        "selected_repo": "scanpy-tutorials",
                        "status": "ok",
                        "summary": {
                            "figure_path": "trajectory.png",
                            "paga_figure_path": "paga.png",
                            "summary_path": "refined.json",
                            "raw_data_path": "raw.h5ad",
                            "cluster_count": 12,
                            "pseudotime_range": [0.0, 1.0],
                            "paga_edge_count": 16,
                            "root_cluster": "3",
                            "qc_summary": {"total_counts_mean": 100.0},
                            "dynamic_gene_summary": {"positive": [{"gene": "Gata1", "correlation": 0.7}]},
                        },
                    }
                }
            ),
            "bio_reviewer_refined": NodeExecutionResult(outputs={"overall_verdict": "strong"}),
        },
    )

    summary = _summarize_scanpy_paul15_case_study(
        report,
        case_assets={"case_id": "scanpy_paul15_trajectory"},
        reference_assets={
            "reference_figure_path": "ref.png",
            "reference_paga_figure_path": "ref_paga.png",
            "reference_summary_path": "ref.json",
        },
    )

    assert summary["used_refinement"] is True
    assert summary["generated_figure_path"] == "trajectory.png"
    assert summary["generated_paga_figure_path"] == "paga.png"
    assert summary["tool_output"]["selected_repo"] == "scanpy-tutorials"
    assert summary["review_output"]["overall_verdict"] == "strong"


def test_squidpy_case_summary_includes_interaction_artifacts() -> None:
    report = ExecutionReport(
        workflow_id="wf",
        task_id="squidpy-case",
        success=True,
        failure_type=FailureType.none,
        traces=[],
        hard_checks=[],
        soft_judges=[],
        contract_violations=[],
        activated_gates=[],
        active_subgraphs=[],
        cost=ExecutionCost(usd=0.2),
        confidence=0.8,
        summary="ok",
        node_results={
            "planner": NodeExecutionResult(outputs={"selected_repo": "squidpy", "summary": "plan"}),
            "squidpy_tool": NodeExecutionResult(
                outputs={
                    "result": {
                        "selected_repo": "squidpy",
                        "status": "ok",
                        "summary": {
                            "figure_path": "spatial.png",
                            "interaction_figure_path": "nhood.png",
                            "summary_path": "summary.json",
                            "raw_data_path": "raw.h5ad",
                            "cluster_count": 12,
                            "has_nhood_enrichment": True,
                            "nhood_enrichment_summary": {
                                "shape": [12, 12],
                                "max_abs_zscore": 8.5,
                                "top_same_cluster_pairs": [{"source": "3", "target": "3", "zscore": 8.5}],
                                "top_cross_cluster_pairs": [{"source": "3", "target": "5", "zscore": 5.0}],
                            },
                        },
                    }
                }
            ),
            "bio_reviewer": NodeExecutionResult(outputs={"overall_verdict": "strong"}),
        },
    )

    summary = _summarize_squidpy_case_study(
        report,
        case_assets={"case_id": "squidpy_visium_hne_interactions"},
        reference_assets={
            "reference_figure_path": "ref_spatial.png",
            "reference_interaction_figure_path": "ref_nhood.png",
            "reference_summary_path": "ref_summary.json",
        },
    )

    assert summary["generated_figure_path"] == "spatial.png"
    assert summary["generated_interaction_figure_path"] == "nhood.png"
    assert summary["reference_interaction_figure_path"] == "ref_nhood.png"
    assert summary["tool_output"]["summary"]["has_nhood_enrichment"] is True


def test_math_selector_prefers_executed_answer_on_clean_disagreement() -> None:
    result = _math_selector_handler(
        None,
        {
            "solver_answer": "24",
            "solver_outline": "outline",
            "program_execution_passed": True,
            "program_executed_answer": "64",
            "program_execution_error": "",
            "program_is_synthetic": False,
        },
        None,
    )

    assert result.outputs["final_answer"] == "64"
    assert result.outputs["needs_review"] is True
    assert "preferring the executed answer" in result.outputs["arbitration_notes"]
