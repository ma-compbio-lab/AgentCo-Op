from __future__ import annotations

import json

from dynaforge.experiment_cli import main as experiment_main
from dynaforge.experiment_runner import (
    _apply_shard,
    _load_existing_medqa_task_results,
    _medqa_evaluation_failure_type,
    _resolve_medqa_indices,
    build_medqa_deep_analysis,
    run_medqa_experiment,
)
from dynaforge.ir.schema import FailureType
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
