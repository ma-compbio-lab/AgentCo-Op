from __future__ import annotations

import json
from types import SimpleNamespace

from dynaforge.experiment_cli import main as experiment_main
from dynaforge.experiment_runner import (
    RunRecorder,
    _apply_shard,
    _build_humaneval_task_handlers,
    _build_math_task_handlers,
    _build_visium_multi_agent_collaboration_blueprint,
    _humaneval_public_test_handler,
    _load_existing_medqa_task_results,
    _math_program_exec_handler,
    _medqa_evaluation_failure_type,
    _resolve_medqa_indices,
    _scanpy_planner_handler,
    _summarize_scanpy_paul15_case_study,
    _summarize_scanpy_case_study,
    _summarize_squidpy_case_study,
    aggregate_humaneval_runs,
    aggregate_humaneval_repeats,
    aggregate_math_runs,
    aggregate_math_repeats,
    build_humaneval_task_blueprint,
    build_math_task_blueprint,
    build_medqa_deep_analysis,
    run_medqa_experiment,
)
from dynaforge.ir.schema import FailureType, NodeSpec, SandboxSpec
from dynaforge.runtime.reports import ExecutionCost, ExecutionReport, NodeExecutionResult, NodeTrace


def test_resolve_medqa_indices_supports_focused_task_ids() -> None:
    records = [
        {"id": "100", "question_id": "q100"},
        {"id": "200", "question_id": "q200"},
        {"id": "300", "question_id": "q300"},
    ]
    manifest = {
        "smoke_indices_path": "/tmp/unused-smoke.json",
        "full_indices_path": "/tmp/unused-full.json",
    }

    from dynaforge import experiment_runner

    original_loader = experiment_runner._load_indices
    experiment_runner._load_indices = lambda manifest, *, subset: [0, 1, 2]
    try:
        assert _resolve_medqa_indices(records, manifest, subset="smoke", limit=None, task_ids=["300", "q100"]) == [2, 0]
    finally:
        experiment_runner._load_indices = original_loader


def test_medqa_evaluation_failure_type_marks_incorrect_answers() -> None:
    report = ExecutionReport(
        workflow_id="wf",
        task_id="task",
        success=True,
        failure_type=FailureType.none,
        traces=[],
        hard_checks=[],
        soft_judges=[],
        contract_violations=[],
        activated_gates=[],
        active_subgraphs=[],
        cost={},
        confidence=0.8,
        summary="ok",
        node_results={},
    )

    assert _medqa_evaluation_failure_type(report, correct=True) == "none"
    assert _medqa_evaluation_failure_type(report, correct=False) == "incorrect_answer"


def test_apply_shard_slices_index_list() -> None:
    indices = list(range(10))

    assert _apply_shard(indices, num_shards=1, shard_index=0) == indices
    assert _apply_shard(indices, num_shards=3, shard_index=0) == [0, 3, 6, 9]
    assert _apply_shard(indices, num_shards=3, shard_index=1) == [1, 4, 7]
    assert _apply_shard(indices, num_shards=3, shard_index=2) == [2, 5, 8]


def test_experiment_cli_parses_task_ids(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr("dynaforge.experiment_cli.load_hydra_config", lambda overrides: {"ok": True})

    def fake_run(config, **kwargs):
        captured.update(kwargs)
        return {"task_count": 2}

    monkeypatch.setattr("dynaforge.experiment_cli.run_medqa_experiment", fake_run)

    exit_code = experiment_main(["medqa", "--task-ids", "1486,8313", "--limit", "2"])

    assert exit_code == 0
    assert captured["task_ids"] == ["1486", "8313"]
    assert captured["limit"] == 2


def test_experiment_cli_defaults_medqa_to_gpt4o_mini(monkeypatch) -> None:
    captured_overrides: list[str] = []

    def fake_load(overrides):
        captured_overrides.extend(overrides)
        return {"ok": True}

    monkeypatch.setattr("dynaforge.experiment_cli.load_hydra_config", fake_load)
    monkeypatch.setattr("dynaforge.experiment_cli.run_medqa_experiment", lambda config, **kwargs: {"task_count": 1})

    exit_code = experiment_main(["medqa"])

    assert exit_code == 0
    assert "model=openai_gpt4o_mini" in captured_overrides


def test_experiment_cli_parses_shard_arguments(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr("dynaforge.experiment_cli.load_hydra_config", lambda overrides: {"ok": True})

    def fake_run(config, **kwargs):
        captured.update(kwargs)
        return {"task_count": 10}

    monkeypatch.setattr("dynaforge.experiment_cli.run_medqa_experiment", fake_run)

    exit_code = experiment_main(["medqa", "--num-shards", "8", "--shard-index", "3"])

    assert exit_code == 0
    assert captured["num_shards"] == 8
    assert captured["shard_index"] == 3


def test_experiment_cli_parses_resume_run_dir(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr("dynaforge.experiment_cli.load_hydra_config", lambda overrides: {"ok": True})

    def fake_run(config, **kwargs):
        captured.update(kwargs)
        return {"task_count": 1}

    monkeypatch.setattr("dynaforge.experiment_cli.run_medqa_experiment", fake_run)

    exit_code = experiment_main(["medqa", "--resume-run-dir", "/tmp/medqa-run"])

    assert exit_code == 0
    assert captured["resume_run_dir"] == "/tmp/medqa-run"


def test_experiment_cli_allows_custom_medqa_experiment_group(monkeypatch) -> None:
    captured_overrides: list[str] = []

    def fake_load(overrides):
        captured_overrides.extend(overrides)
        return {"ok": True}

    monkeypatch.setattr("dynaforge.experiment_cli.load_hydra_config", fake_load)
    monkeypatch.setattr("dynaforge.experiment_cli.run_medqa_experiment", lambda config, **kwargs: {"task_count": 1})

    exit_code = experiment_main(["medqa", "--experiment", "medqa_planner_gate"])

    assert exit_code == 0
    assert "experiment=medqa_planner_gate" in captured_overrides


def test_experiment_cli_parses_math_arguments(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr("dynaforge.experiment_cli.load_hydra_config", lambda overrides: {"ok": True})

    def fake_run(config, **kwargs):
        captured.update(kwargs)
        return {"task_count": 3}

    monkeypatch.setattr("dynaforge.experiment_cli.run_math_experiment", fake_run)

    exit_code = experiment_main(["math", "--subset", "test", "--task-ids", "a,b", "--num-shards", "4", "--shard-index", "2"])

    assert exit_code == 0
    assert captured["subset"] == "test"
    assert captured["task_ids"] == ["a", "b"]
    assert captured["num_shards"] == 4
    assert captured["shard_index"] == 2


def test_experiment_cli_parses_humaneval_arguments(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr("dynaforge.experiment_cli.load_hydra_config", lambda overrides: {"ok": True})

    def fake_run(config, **kwargs):
        captured.update(kwargs)
        return {"task_count": 2}

    monkeypatch.setattr("dynaforge.experiment_cli.run_humaneval_experiment", fake_run)

    exit_code = experiment_main(["humaneval", "--subset", "validation", "--resume-run-dir", "/tmp/he-run"])

    assert exit_code == 0
    assert captured["subset"] == "validation"
    assert captured["resume_run_dir"] == "/tmp/he-run"


def test_experiment_cli_runs_math_aggregate(monkeypatch) -> None:
    monkeypatch.setattr("dynaforge.experiment_cli.aggregate_math_runs", lambda run_dirs, base_dir: {"task_count": 2})
    exit_code = experiment_main(["math-aggregate", "/tmp/run-a", "/tmp/run-b"])
    assert exit_code == 0


def test_experiment_cli_runs_humaneval_aggregate(monkeypatch) -> None:
    monkeypatch.setattr("dynaforge.experiment_cli.aggregate_humaneval_runs", lambda run_dirs, base_dir: {"task_count": 2})
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
    from dynaforge.config import load_hydra_config, build_blueprint_from_config

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
    from dynaforge.config import load_hydra_config, build_blueprint_from_config

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
    from dynaforge.experiment_runner import _scanpy_paul15_planner_handler

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
    from dynaforge.experiment_runner import _squidpy_planner_handler

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
    from dynaforge.experiment_runner import _visium_multi_agent_planner_handler

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


def test_load_existing_medqa_task_results_reconstructs_from_traces(tmp_path) -> None:
    run_root = tmp_path / "run"
    (run_root / "traces").mkdir(parents=True)
    sample = {
        "id": "100",
        "question_id": "q100",
        "question": "Question",
        "options": [{"label": "A", "text": "Answer A"}, {"label": "B", "text": "Answer B"}],
        "gold_answer_label": "A",
        "gold_answer_text": "Answer A",
        "metadata": {"type": "single_choice"},
    }
    report = ExecutionReport(
        workflow_id="wf",
        task_id="medqa:100",
        success=True,
        failure_type=FailureType.none,
        traces=[],
        hard_checks=[],
        soft_judges=[],
        contract_violations=[],
        activated_gates=["gate"],
        active_subgraphs=["review"],
        cost={},
        confidence=0.9,
        summary="ok",
        node_results={
            "responder": NodeExecutionResult(outputs={"final_answer_label": "A", "summary": "ok"}, confidence=0.9)
        },
    )
    (run_root / "traces" / "100.report.json").write_text(report.model_dump_json(), encoding="utf-8")

    results = _load_existing_medqa_task_results(
        run_root,
        sample_by_task_id={
            "100": {
                "order": 1,
                "sample_index": 12,
                "sample": sample,
            }
        },
        preferred_nodes=["responder"],
    )

    assert len(results) == 1
    assert results[0]["task_id"] == "100"
    assert results[0]["sample_index"] == 12
    assert results[0]["correct"] is True


def test_run_medqa_experiment_resume_skips_completed_tasks(monkeypatch, tmp_path) -> None:
    records_path = tmp_path / "records.jsonl"
    records = [
        {
            "id": "100",
            "question_id": "q100",
            "question": "Question 100",
            "prompt": "Prompt 100",
            "options": [{"label": "A", "text": "Answer A"}, {"label": "B", "text": "Answer B"}],
            "gold_answer_label": "A",
            "gold_answer_text": "Answer A",
            "metadata": {"type": "single_choice"},
        },
        {
            "id": "200",
            "question_id": "q200",
            "question": "Question 200",
            "prompt": "Prompt 200",
            "options": [{"label": "A", "text": "Answer A"}, {"label": "B", "text": "Answer B"}],
            "gold_answer_label": "A",
            "gold_answer_text": "Answer A",
            "metadata": {"type": "single_choice"},
        },
    ]
    records_path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    indices_path = tmp_path / "indices.json"
    indices_path.write_text("[0, 1]\n", encoding="utf-8")
    manifest = {
        "records_path": str(records_path),
        "smoke_indices_path": str(indices_path),
        "full_indices_path": str(indices_path),
    }
    run_root = tmp_path / "resume-run"
    (run_root / "eval").mkdir(parents=True)
    existing_result = {
        "order": 1,
        "sample_index": 0,
        "task_id": "100",
        "question_id": "q100",
        "question": "Question 100",
        "options": [{"label": "A", "text": "Answer A"}, {"label": "B", "text": "Answer B"}],
        "metadata": {"type": "single_choice"},
        "prediction_node": "responder",
        "prediction_label": "A",
        "prediction_text": "Answer A",
        "gold_label": "A",
        "gold_text": "Answer A",
        "correct": True,
        "workflow_signature": "responder",
        "activated_gates": [],
        "active_subgraphs": [],
        "activated_node_count": 1,
        "activated_subgraph_count": 0,
        "runtime_failure_type": "none",
        "failure_type": "none",
        "confidence": 0.9,
        "summary": "cached",
        "report_path": "",
        "cost": {},
    }
    (run_root / "eval" / "task_results.json").write_text(
        json.dumps([existing_result], ensure_ascii=True, indent=2),
        encoding="utf-8",
    )

    monkeypatch.setattr("dynaforge.experiment_runner.load_local_secrets", lambda: None)
    monkeypatch.setattr("dynaforge.experiment_runner.resolve_hydra_config", lambda config: config)
    monkeypatch.setattr("dynaforge.experiment_runner.prepare_medqa_assets", lambda resolved: {"prepared": True})
    monkeypatch.setattr("dynaforge.experiment_runner.prepare_medqa_dataset", lambda resolved: manifest)
    monkeypatch.setattr("dynaforge.experiment_runner.build_blueprint_from_config", lambda resolved: object())
    monkeypatch.setattr("dynaforge.experiment_runner.build_executor_from_config", lambda resolved, artifact_root=None: object())
    monkeypatch.setattr("dynaforge.experiment_runner.build_medqa_task_blueprint", lambda base_blueprint, sample: sample)

    executed: list[str] = []

    def fake_run_blueprint(config, task_blueprint, executor):
        sample = task_blueprint
        executed.append(sample["id"])
        report = ExecutionReport(
            workflow_id="wf",
            task_id=f"medqa:{sample['id']}",
            success=True,
            failure_type=FailureType.none,
            traces=[],
            hard_checks=[],
            soft_judges=[],
            contract_violations=[],
            activated_gates=[],
            active_subgraphs=[],
            cost={},
            confidence=0.8,
            summary="ok",
            node_results={
                "responder": NodeExecutionResult(
                    outputs={"final_answer_label": "A", "summary": "ok"},
                    confidence=0.8,
                )
            },
        )
        return task_blueprint, report

    monkeypatch.setattr("dynaforge.experiment_runner._run_blueprint", fake_run_blueprint)

    summary = run_medqa_experiment(
        {"benchmark": {"kind": "medqa"}},
        subset="smoke",
        base_dir=tmp_path / "runs",
        resume_run_dir=run_root,
    )

    merged_results = json.loads((run_root / "eval" / "task_results.json").read_text(encoding="utf-8"))

    assert executed == ["200"]
    assert summary["task_count"] == 2
    assert [result["task_id"] for result in merged_results] == ["100", "200"]


def test_build_medqa_deep_analysis_uses_question_type_and_review_ids(tmp_path) -> None:
    reviewed_report = ExecutionReport(
        workflow_id="wf-review",
        task_id="medqa:100",
        success=True,
        failure_type=FailureType.none,
        traces=[
            NodeTrace(
                node_id="planner",
                role="Planner",
                status="success",
                outputs={"question_type": "mechanism"},
                confidence=0.7,
            ),
            NodeTrace(
                node_id="responder",
                role="Responder",
                status="success",
                outputs={"final_answer_label": "B"},
                confidence=0.7,
            ),
            NodeTrace(
                node_id="reviewer",
                role="Reviewer",
                status="success",
                outputs={"review_verdict": "revise"},
                confidence=0.8,
            ),
            NodeTrace(
                node_id="reviser",
                role="Reviser",
                status="success",
                outputs={"final_answer_label": "B"},
                confidence=0.8,
            ),
        ],
        hard_checks=[],
        soft_judges=[],
        contract_violations=[],
        activated_gates=["medqa_low_conf_review"],
        active_subgraphs=["sg_review"],
        cost=ExecutionCost(usd=0.004),
        confidence=0.75,
        summary="reviewed",
        node_results={
            "reviser": NodeExecutionResult(outputs={"final_answer_label": "B"}, confidence=0.8),
        },
    )
    plain_report = ExecutionReport(
        workflow_id="wf-plain",
        task_id="medqa:200",
        success=True,
        failure_type=FailureType.none,
        traces=[
            NodeTrace(
                node_id="planner",
                role="Planner",
                status="success",
                outputs={"question_type": "diagnosis"},
                confidence=0.8,
            ),
            NodeTrace(
                node_id="responder",
                role="Responder",
                status="success",
                outputs={"final_answer_label": "A"},
                confidence=0.9,
            ),
        ],
        hard_checks=[],
        soft_judges=[],
        contract_violations=[],
        activated_gates=[],
        active_subgraphs=[],
        cost=ExecutionCost(usd=0.001),
        confidence=0.85,
        summary="plain",
        node_results={
            "responder": NodeExecutionResult(outputs={"final_answer_label": "A"}, confidence=0.9),
        },
    )
    reviewed_path = tmp_path / "100.report.json"
    plain_path = tmp_path / "200.report.json"
    reviewed_path.write_text(reviewed_report.model_dump_json(), encoding="utf-8")
    plain_path.write_text(plain_report.model_dump_json(), encoding="utf-8")

    deep_analysis = build_medqa_deep_analysis(
        [
            {
                "task_id": "100",
                "question_type": "",
                "correct": False,
                "workflow_signature": "planner->responder->reviewer->reviser",
                "prediction_label": "B",
                "gold_label": "C",
                "confidence": 0.75,
                "failure_type": "incorrect_answer",
                "runtime_failure_type": "none",
                "report_path": str(reviewed_path),
                "cost": {"usd": 0.004},
                "activated_node_count": 4,
                "activated_subgraph_count": 1,
            },
            {
                "task_id": "200",
                "question_type": "diagnosis",
                "correct": True,
                "workflow_signature": "planner->responder",
                "prediction_label": "A",
                "gold_label": "A",
                "confidence": 0.85,
                "failure_type": "none",
                "runtime_failure_type": "none",
                "report_path": str(plain_path),
                "cost": {"usd": 0.001},
                "activated_node_count": 2,
                "activated_subgraph_count": 0,
            },
        ]
    )

    assert deep_analysis["review_task_count"] == 1
    assert deep_analysis["reviewed_task_ids"] == ["100"]
    assert deep_analysis["question_type_breakdown"]["mechanism"]["count"] == 1
    assert deep_analysis["question_type_breakdown"]["mechanism"]["review_rate"] == 1.0
    assert deep_analysis["review_failure_breakdown"]["mechanism"] == 1
    assert deep_analysis["workflow_structure"]["role_frequency"]["Reviewer"] == 1
