from __future__ import annotations

import os

from agentcoop.ir.schema import ModelProvider, ModelSpec, NodeKind, NodeSpec, TaskSpec, WorkflowBlueprint
from agentcoop.runtime.executor import BlueprintExecutor
from agentcoop.runtime.llm import LLMRouter, OpenAICompatibleLLMClient
from agentcoop.runtime.reports import NodeExecutionResult
from agentcoop.secrets import load_local_secrets


def test_load_local_secrets_reads_secret_files(tmp_path, monkeypatch) -> None:
    secret_dir = tmp_path / ".secrets"
    secret_dir.mkdir()
    (secret_dir / "openai_api_key").write_text("test-openai\n", encoding="utf-8")
    (secret_dir / "wandb_api_key").write_text("test-wandb\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("WANDB_API_KEY", raising=False)

    loaded = load_local_secrets(secret_dir=secret_dir)

    assert os.environ["OPENAI_API_KEY"] == "test-openai"
    assert os.environ["WANDB_API_KEY"] == "test-wandb"
    assert loaded["OPENAI_API_KEY"].endswith("openai_api_key")
    assert loaded["WANDB_API_KEY"].endswith("wandb_api_key")


def test_gpt5_uses_max_completion_tokens_field() -> None:
    gpt5 = ModelSpec(provider=ModelProvider.openai, name="gpt-5-mini")
    gpt41 = ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini")

    assert OpenAICompatibleLLMClient._max_tokens_field(gpt5) == "max_completion_tokens"
    assert OpenAICompatibleLLMClient._max_tokens_field(gpt41) == "max_tokens"
    assert OpenAICompatibleLLMClient._supports_temperature(gpt5) is False
    assert OpenAICompatibleLLMClient._supports_temperature(gpt41) is True


def test_normalize_output_accepts_dotted_output_keys() -> None:
    normalized = LLMRouter._normalize_output(
        {
            "output.final_answer_label": "B",
            "output.final_answer_text": "Beta",
            "rationale": "Because",
            "confidence": 0.8,
            "summary": "ok",
        }
    )

    assert normalized == {
        "final_answer_label": "B",
        "final_answer_text": "Beta",
        "rationale": "Because",
    }


def test_root_node_receives_task_payload() -> None:
    captured: dict[str, object] = {}
    blueprint = WorkflowBlueprint.model_validate(
        {
            "task": {
                "task_id": "task-aware",
                "title": "Task aware",
                "description": "Answer the question",
                "hints": {"question": "What is 2+2?"},
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
            "meta": {"benchmark": "unit"},
        }
    )

    def handler(node: NodeSpec, inputs: dict[str, object], context: object):
        captured.update(inputs)
        return NodeExecutionResult(outputs={"result": "ok"}, confidence=1.0)

    report = BlueprintExecutor().execute_blueprint(blueprint, handlers={"planner": handler})

    assert report.success is True
    assert isinstance(captured.get("task"), dict)
    assert captured["task"]["description"] == "Answer the question"
    assert captured["task"]["hints"]["question"] == "What is 2+2?"
    assert captured["workflow_meta"]["benchmark"] == "unit"


def test_non_root_node_receives_task_payload() -> None:
    captured: dict[str, object] = {}
    blueprint = WorkflowBlueprint.model_validate(
        {
            "task": {
                "task_id": "task-aware-child",
                "title": "Task aware child",
                "description": "Classify the answer",
                "hints": {"question": "What is 2+2?"},
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
                },
                {
                    "node_id": "responder",
                    "kind": "agent",
                    "role": "Responder",
                    "model": {
                        "provider": "openai",
                        "name": "gpt-4.1-mini",
                    },
                },
            ],
            "base_edges": [
                {
                    "edge_id": "plan_to_respond",
                    "src": "planner",
                    "dst": "responder",
                }
            ],
            "meta": {"benchmark": "unit"},
        }
    )

    def planner_handler(node: NodeSpec, inputs: dict[str, object], context: object):
        return NodeExecutionResult(outputs={"outline": "start"}, confidence=1.0)

    def responder_handler(node: NodeSpec, inputs: dict[str, object], context: object):
        captured.update(inputs)
        return NodeExecutionResult(outputs={"result": "ok"}, confidence=1.0)

    report = BlueprintExecutor().execute_blueprint(
        blueprint,
        handlers={"planner": planner_handler, "responder": responder_handler},
    )

    assert report.success is True
    assert captured["planner"] == {"outline": "start"}
    assert captured["task"]["description"] == "Classify the answer"
    assert captured["task"]["hints"]["question"] == "What is 2+2?"
    assert captured["workflow_meta"]["benchmark"] == "unit"
