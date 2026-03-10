from __future__ import annotations

from dynaforge.ir import (
    BudgetSpec,
    EdgeSpec,
    GateSpec,
    ModelProvider,
    ModelSpec,
    NodeKind,
    NodeSpec,
    PatchOp,
    PatchOpType,
    TaskSpec,
    TriggerExpr,
    WorkflowBlueprint,
)
from dynaforge.runtime.conditions import evaluate_trigger
from dynaforge.runtime.executor import BlueprintExecutor
from dynaforge.runtime.llm import LLMRouter
from dynaforge.runtime.patching import DeterministicPatchPolicy, apply_patch_plan
from dynaforge.runtime.reports import BlameCandidate, ExecutionReport, NodeExecutionResult


class StubLLMClient:
    def complete(self, model, messages, *, response_format=None):
        node_id = "stub"
        content = messages[-1]["content"]
        for line in content.splitlines():
            if line.startswith("Node ID: "):
                node_id = line.split("Node ID: ", 1)[1].strip()
                break
        return {
            "content": (
                '{"output":{"result":{"node_id":"%s"}},"confidence":0.9,"summary":"ok"}' % node_id
            ),
            "usage": {"prompt_tokens": 4, "completion_tokens": 4},
        }


def make_stub_executor() -> BlueprintExecutor:
    return BlueprintExecutor(
        llm_router=LLMRouter(client=StubLLMClient(), allow_offline_fallback=False),
        allow_offline_fallback=False,
    )


def test_trigger_evaluation() -> None:
    trigger = TriggerExpr(any_of=[{"field": "report.confidence", "op": "<", "value": 0.5}])
    assert evaluate_trigger(trigger, {"report": {"confidence": 0.4}})
    assert not evaluate_trigger(trigger, {"report": {"confidence": 0.9}})


def test_executor_runs_minimal_blueprint() -> None:
    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="runtime-1", title="Runtime", description="Desc"),
        budget=BudgetSpec(max_total_usd=1.0),
        base_nodes=[
            NodeSpec(
                node_id="planner",
                kind=NodeKind.agent,
                role="Planner",
                model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
                cacheable=True,
            ),
            NodeSpec(
                node_id="worker",
                kind=NodeKind.agent,
                role="Worker",
                model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
                io={"output_schema": {"type": "object", "required": ["result"]}},
            ),
        ],
        base_edges=[EdgeSpec(edge_id="e1", src="planner", dst="worker")],
    )

    report = make_stub_executor().execute_blueprint(blueprint)

    assert report.success
    assert report.failure_type.value == "none"
    assert len(report.traces) == 2


def test_deterministic_patch_policy_prefers_sandbox_change_for_env_failures() -> None:
    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="runtime-2", title="Runtime", description="Desc"),
        base_nodes=[
            NodeSpec(
                node_id="tool-node",
                kind=NodeKind.agent,
                role="Tool Node",
                model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
                sandbox={"sandbox_id": "bio-base", "image": "python:3.10-slim"},
            )
        ],
        base_edges=[],
    )
    report = ExecutionReport(
        workflow_id="wf",
        task_id="runtime-2",
        success=False,
        failure_type="env_missing_dep",
        blame_candidates=[
            BlameCandidate(
                target_type="node",
                target_id="tool-node",
                score=0.9,
                reason="missing dep",
            )
        ],
    )

    plan = DeterministicPatchPolicy().propose(blueprint, report)

    assert plan.ops[0].op == "ChangeSandbox"


def test_apply_patch_plan_replaces_model() -> None:
    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="runtime-3", title="Runtime", description="Desc"),
        base_nodes=[
            NodeSpec(
                node_id="planner",
                kind=NodeKind.agent,
                role="Planner",
                model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1"),
            )
        ],
        base_edges=[],
    )

    patched = apply_patch_plan(
        blueprint,
        patch_plan={
            "plan_id": "p1",
            "ops": [
                PatchOp(
                    op=PatchOpType.replace_model,
                    target="planner",
                    params={
                        "provider": "openai",
                        "name": "gpt-4.1-mini",
                        "temperature": 0.1,
                    },
                )
            ],
        },
    )

    assert patched.node_map()["planner"].model.name == "gpt-4.1-mini"


def test_apply_patch_plan_adds_gate() -> None:
    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="runtime-4", title="Runtime", description="Desc"),
        base_nodes=[
            NodeSpec(
                node_id="planner",
                kind=NodeKind.agent,
                role="Planner",
                model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
            )
        ],
        base_edges=[],
        subgraphs=[
            {
                "subgraph_id": "sg_review",
                "purpose": "review",
                "nodes": [
                    NodeSpec(
                        node_id="review",
                        kind=NodeKind.evaluator,
                        role="Review",
                        model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
                    )
                ],
                "edges": [],
                "entry_nodes": ["review"],
                "exit_nodes": ["review"],
                "default_enabled": False,
            }
        ],
    )

    patched = apply_patch_plan(
        blueprint,
        {
            "plan_id": "p2",
            "ops": [
                {
                    "op": "AddGate",
                    "params": {
                        "gate": GateSpec(
                            gate_id="gate1",
                            trigger=TriggerExpr(any_of=[{"field": "report.confidence", "op": "<", "value": 0.5}]),
                            enable_subgraphs=["sg_review"],
                        ).model_dump(by_alias=True)
                    },
                }
            ],
        },
    )

    assert patched.gates[0].enable_subgraphs == ["sg_review"]


def test_forced_subgraph_node_is_reexecuted() -> None:
    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="runtime-5", title="Runtime", description="Desc"),
        base_nodes=[
            NodeSpec(
                node_id="planner",
                kind=NodeKind.agent,
                role="Planner",
                model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
            )
        ],
        base_edges=[],
        subgraphs=[
            {
                "subgraph_id": "sg_review",
                "purpose": "review",
                "nodes": [
                    NodeSpec(
                        node_id="review",
                        kind=NodeKind.evaluator,
                        role="Review",
                        model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
                    )
                ],
                "edges": [],
                "entry_nodes": ["review"],
                "exit_nodes": ["review"],
                "default_enabled": False,
            }
        ],
    )

    report = make_stub_executor().execute_blueprint(
        blueprint,
        resume_state={"review": NodeExecutionResult(outputs={"result": {"cached": True}})},
        force_nodes={"review"},
    )

    assert "review" in report.meta["executed_nodes"]
    assert report.node_results["review"].outputs["result"]["node_id"] == "review"


def test_input_contract_blocks_handler_execution() -> None:
    called = {"value": False}

    def handler(node, inputs, context):
        called["value"] = True
        return NodeExecutionResult(outputs={"result": "unexpected"})

    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="runtime-6", title="Runtime", description="Desc"),
        base_nodes=[
            NodeSpec(
                node_id="worker",
                kind=NodeKind.agent,
                role="Worker",
                model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
                io={"input_schema": {"type": "object", "required": ["payload"]}},
            )
        ],
        base_edges=[],
    )

    report = BlueprintExecutor().execute_blueprint(blueprint, handlers={"worker": handler})

    assert not called["value"]
    assert not report.success
    assert report.failure_type.value == "output_contract_violation"


def test_conditional_false_edge_does_not_block_or_inject_inputs() -> None:
    seen_inputs = {}

    def handler(node, inputs, context):
        seen_inputs[node.node_id] = inputs
        return NodeExecutionResult(outputs={"result": {"node_id": node.node_id}})

    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="runtime-7", title="Runtime", description="Desc"),
        base_nodes=[
            NodeSpec(
                node_id="a",
                kind=NodeKind.agent,
                role="A",
                model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
            ),
            NodeSpec(
                node_id="b",
                kind=NodeKind.agent,
                role="B",
                model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
            ),
        ],
        base_edges=[EdgeSpec(edge_id="e1", src="a", dst="b", condition="False")],
    )

    report = BlueprintExecutor().execute_blueprint(blueprint, handlers={"A": handler, "B": handler})

    assert report.success
    assert seen_inputs["b"] == {
        "task": blueprint.task.model_dump(),
        "workflow_meta": blueprint.meta,
    }


def test_broken_hard_check_returns_failed_report() -> None:
    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="runtime-8", title="Runtime", description="Desc"),
        base_nodes=[
            NodeSpec(
                node_id="planner",
                kind=NodeKind.agent,
                role="Planner",
                model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
            )
        ],
        base_edges=[],
        eval={
            "hard_checks": [
                {
                    "check_id": "missing-binary",
                    "description": "broken command",
                    "cmd": ["__definitely_missing_binary__"],
                }
            ]
        },
    )

    report = BlueprintExecutor().execute_blueprint(blueprint)

    assert not report.success
    assert report.hard_checks[0].passed is False


def test_tighten_contract_preserves_existing_required_keys() -> None:
    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="runtime-9", title="Runtime", description="Desc"),
        base_nodes=[
            NodeSpec(
                node_id="worker",
                kind=NodeKind.agent,
                role="Worker",
                model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
                io={"output_schema": {"type": "object", "required": ["a", "b"]}},
            )
        ],
        base_edges=[],
    )

    patched = apply_patch_plan(
        blueprint,
        {
            "plan_id": "p3",
            "ops": [
                {
                    "op": "TightenContract",
                    "target": "worker",
                    "params": {"io": {"output_schema": {"required": ["result"]}}},
                }
            ],
        },
    )

    required = patched.node_map()["worker"].io.output_schema["required"]
    assert set(required) == {"a", "b", "result"}
