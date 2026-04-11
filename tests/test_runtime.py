from __future__ import annotations

from agentcoop.ir import (
    BudgetSpec,
    EdgeSpec,
    GateSpec,
    ModelProvider,
    ModelSpec,
    NodeKind,
    NodeSpec,
    PatchOp,
    PatchOpType,
    ReviewPolicy,
    TaskSpec,
    TriggerExpr,
    WorkflowBlueprint,
)
from agentcoop.runtime.conditions import evaluate_trigger
from agentcoop.runtime.executor import BlueprintExecutor
from agentcoop.runtime.llm import LLMRouter
from agentcoop.runtime.patching import DeterministicPatchPolicy, apply_patch_plan
from agentcoop.runtime.reports import BlameCandidate, ExecutionReport, NodeExecutionResult


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


def test_trigger_contains_returns_false_for_scalar_non_container() -> None:
    trigger = TriggerExpr(any_of=[{"field": "node.outputs.answer", "op": "contains", "value": "."}])
    assert not evaluate_trigger(trigger, {"node": {"outputs": {"answer": 42}}})


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


def test_executor_extracts_nested_output_confidence() -> None:
    class NestedConfidenceLLMClient:
        def complete(self, model, messages, *, response_format=None):
            return {
                "content": (
                    '{"output":{"result":{"ok":true},"confidence":0.92,"summary":"nested"},"summary":"top"}'
                ),
                "usage": {"prompt_tokens": 4, "completion_tokens": 4},
            }

    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="runtime-nested-confidence", title="Runtime", description="Desc"),
        base_nodes=[
            NodeSpec(
                node_id="planner",
                kind=NodeKind.agent,
                role="Planner",
                model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
                io={"output_schema": {"type": "object", "required": ["result"]}},
            )
        ],
        base_edges=[],
    )

    executor = BlueprintExecutor(
        llm_router=LLMRouter(client=NestedConfidenceLLMClient(), allow_offline_fallback=False),
        allow_offline_fallback=False,
    )
    report = executor.execute_blueprint(blueprint)

    assert report.success
    assert report.traces[0].confidence == 0.92
    assert report.traces[0].trace["summary"] == "top"


def test_executor_records_wall_time(monkeypatch) -> None:
    class SimpleLLMClient:
        def complete(self, model, messages, *, response_format=None):
            return {
                "content": '{"output":{"result":{"ok":true}},"confidence":0.8,"summary":"ok"}',
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

    clock = iter([10.0, 10.25])
    monkeypatch.setattr("agentcoop.runtime.executor.time.perf_counter", lambda: next(clock))

    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="runtime-wall-time", title="Runtime", description="Desc"),
        base_nodes=[
            NodeSpec(
                node_id="planner",
                kind=NodeKind.agent,
                role="Planner",
                model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
                io={"output_schema": {"type": "object", "required": ["result"]}},
            )
        ],
        base_edges=[],
    )

    executor = BlueprintExecutor(
        llm_router=LLMRouter(client=SimpleLLMClient(), allow_offline_fallback=False),
        allow_offline_fallback=False,
    )
    report = executor.execute_blueprint(blueprint)

    assert report.success
    assert report.traces[0].cost.wall_time_s == 0.25


def test_executor_records_node_execution_events() -> None:
    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="runtime-events", title="Runtime", description="Desc"),
        base_nodes=[
            NodeSpec(
                node_id="planner",
                kind=NodeKind.agent,
                role="Planner",
                model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
                io={"output_schema": {"type": "object", "required": ["result"]}},
            )
        ],
        base_edges=[],
    )

    report = make_stub_executor().execute_blueprint(blueprint)

    node_events = [event for event in report.events if event.get("type") == "node_executed"]
    assert len(node_events) == 1
    assert node_events[0]["node_id"] == "planner"
    assert node_events[0]["status"] == "success"


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


def test_cache_key_changes_when_node_skills_change() -> None:
    call_count = {"value": 0}

    def handler(node, inputs, context):
        call_count["value"] += 1
        return NodeExecutionResult(outputs={"result": {"call_count": call_count["value"]}})

    executor = BlueprintExecutor()
    base_kwargs = {
        "task": TaskSpec(task_id="runtime-10", title="Runtime", description="Desc"),
        "base_edges": [],
    }
    blueprint_a = WorkflowBlueprint(
        **base_kwargs,
        base_nodes=[
            NodeSpec(
                node_id="planner",
                kind=NodeKind.agent,
                role="Planner",
                model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
                cacheable=True,
                skills=[{"name": "skill-a"}],
            )
        ],
    )
    blueprint_b = WorkflowBlueprint(
        **base_kwargs,
        base_nodes=[
            NodeSpec(
                node_id="planner",
                kind=NodeKind.agent,
                role="Planner",
                model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
                cacheable=True,
                skills=[{"name": "skill-b"}],
            )
        ],
    )

    report_a = executor.execute_blueprint(blueprint_a, handlers={"planner": handler})
    report_b = executor.execute_blueprint(blueprint_b, handlers={"planner": handler})

    assert report_a.success
    assert report_b.success
    assert call_count["value"] == 2


def test_executor_applies_output_backfill_before_contract_validation() -> None:
    def handler(node, inputs, context):
        return NodeExecutionResult(outputs={"final_answer": "12\\pi"})

    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="runtime-backfill", title="Runtime", description="Desc"),
        base_nodes=[
            NodeSpec(
                node_id="programmer",
                kind=NodeKind.agent,
                role="Programmer",
                model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
                io={"output_schema": {"type": "object", "required": ["program", "final_answer"]}},
                meta={
                    "output_backfills": [
                        {
                            "target": "program",
                            "strategy": "python_assignment_from_field",
                            "source": "final_answer",
                            "variable_name": "FINAL_ANSWER",
                            "sentinel": "DYNaforge synthetic program fallback",
                        }
                    ]
                },
            )
        ],
        base_edges=[],
    )

    report = BlueprintExecutor().execute_blueprint(blueprint, handlers={"programmer": handler})

    assert report.success
    outputs = report.node_results["programmer"].outputs
    assert outputs["final_answer"] == "12\\pi"
    assert "FINAL_ANSWER" in outputs["program"]
    assert report.traces[0].trace["output_backfills"][0]["target"] == "program"


def test_executor_output_change_guard_marks_noop_repairs_and_skips_conditioned_edge() -> None:
    def coder_handler(node, inputs, context):
        return NodeExecutionResult(outputs={"completion": "def f(x):\n    return x - 1\n"})

    def public_test_handler(node, inputs, context):
        return NodeExecutionResult(outputs={"passed": False, "result": "expected 4, got 2"})

    def reviewer_handler(node, inputs, context):
        return NodeExecutionResult(outputs={"corrected_completion": "def f(x):\n    return x - 1\n"})

    def reviser_handler(node, inputs, context):
        return NodeExecutionResult(outputs={"completion": "def f(x):\n    return x - 1\n"})

    seen = {"retest_ran": False}

    def retest_handler(node, inputs, context):
        seen["retest_ran"] = True
        return NodeExecutionResult(outputs={"passed": False, "result": "still failing"})

    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="runtime-change-guard", title="Runtime", description="Desc"),
        base_nodes=[
            NodeSpec(
                node_id="coder",
                kind=NodeKind.agent,
                role="Coder",
                model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
            ),
            NodeSpec(
                node_id="public_test_runner",
                kind=NodeKind.tool,
                role="PublicTestRunner",
                meta={"direct_sandbox_handler": True},
            ),
        ],
        base_edges=[EdgeSpec(edge_id="edge_coder_to_test", src="coder", dst="public_test_runner")],
        subgraphs=[
            {
                "subgraph_id": "sg_fix",
                "purpose": "repair",
                "nodes": [
                    NodeSpec(
                        node_id="reviewer",
                        kind=NodeKind.agent,
                        role="Reviewer",
                        model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
                    ),
                    NodeSpec(
                        node_id="reviser",
                        kind=NodeKind.agent,
                        role="Reviser",
                        model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
                        meta={
                            "output_change_guard": {
                                "output_path": "completion",
                                "compare_against": ["inputs.candidate_code"],
                                "normalize": "code",
                                "record_field": "repair_changed",
                                "reason_field": "repair_change_reason",
                            }
                        },
                    ),
                    NodeSpec(
                        node_id="retest_runner",
                        kind=NodeKind.tool,
                        role="RetestRunner",
                        meta={
                            "direct_sandbox_handler": True,
                            "execution_condition": "inputs.repair_changed == True",
                        },
                    ),
                ],
                "edges": [
                    EdgeSpec(edge_id="edge_coder_to_reviewer", src="coder", dst="reviewer", mapping={"candidate_code": "completion"}),
                    EdgeSpec(edge_id="edge_test_to_reviewer", src="public_test_runner", dst="reviewer", mapping={"public_test_result": "result"}),
                    EdgeSpec(edge_id="edge_coder_to_reviser", src="coder", dst="reviser", mapping={"candidate_code": "completion"}),
                    EdgeSpec(edge_id="edge_test_to_reviser", src="public_test_runner", dst="reviser", mapping={"public_test_result": "result"}),
                    EdgeSpec(edge_id="edge_reviewer_to_reviser", src="reviewer", dst="reviser"),
                    EdgeSpec(
                        edge_id="edge_reviser_to_retest",
                        src="reviser",
                        dst="retest_runner",
                        mapping={
                            "candidate_code": "completion",
                            "repair_changed": "repair_changed",
                            "repair_change_reason": "repair_change_reason",
                        },
                    ),
                ],
                "entry_nodes": ["reviewer"],
                "exit_nodes": ["reviser", "retest_runner"],
            }
        ],
        gates=[
            {
                "gate_id": "repair",
                "trigger": {
                    "all_of": [
                        {"field": "node.node_id", "op": "==", "value": "public_test_runner"},
                        {"field": "node.outputs.passed", "op": "==", "value": False},
                    ]
                },
                "enable_subgraphs": ["sg_fix"],
            }
        ],
    )

    handlers = {
        "coder": coder_handler,
        "public_test_runner": public_test_handler,
        "reviewer": reviewer_handler,
        "reviser": reviser_handler,
        "retest_runner": retest_handler,
    }

    report = BlueprintExecutor().execute_blueprint(blueprint, handlers=handlers)

    assert report.success
    assert seen["retest_ran"] is False
    reviser_outputs = report.node_results["reviser"].outputs
    assert reviser_outputs["repair_changed"] is False
    assert "matched guarded reference" in reviser_outputs["repair_change_reason"]


def _make_review_blueprint(review_policy: ReviewPolicy) -> WorkflowBlueprint:
    """Helper to build a blueprint with a solver -> gate -> reviewer + reviser subgraph."""
    return WorkflowBlueprint(
        task=TaskSpec(task_id="runtime-review-policy", title="Review", description="Desc"),
        budget=BudgetSpec(max_total_usd=1.0),
        base_nodes=[
            NodeSpec(
                node_id="solver",
                kind=NodeKind.agent,
                role="Solver",
                model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
            ),
        ],
        base_edges=[],
        subgraphs=[
            {
                "subgraph_id": "sg_review",
                "purpose": "review",
                "nodes": [
                    NodeSpec(
                        node_id="reviewer",
                        kind=NodeKind.agent,
                        role="Reviewer",
                        model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
                    ),
                    NodeSpec(
                        node_id="reviser",
                        kind=NodeKind.agent,
                        role="Reviser",
                        model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
                    ),
                ],
                "edges": [
                    EdgeSpec(edge_id="e_solver_to_reviewer", src="solver", dst="reviewer"),
                    EdgeSpec(edge_id="e_reviewer_to_reviser", src="reviewer", dst="reviser"),
                ],
                "entry_nodes": ["reviewer"],
                "exit_nodes": ["reviser"],
                "default_enabled": False,
                "review_policy": review_policy.value,
            },
        ],
        gates=[
            GateSpec(
                gate_id="gate_review",
                trigger=TriggerExpr(
                    all_of=[{"field": "node.node_id", "op": "==", "value": "solver"}]
                ),
                enable_subgraphs=["sg_review"],
            ),
        ],
    )


def test_review_policy_verify_then_correct_accept() -> None:
    """When reviewer returns verdict=accept, the reviser should be skipped."""

    class ReviewerAcceptClient:
        def complete(self, model, messages, *, response_format=None):
            content = messages[-1]["content"]
            if "Node ID: reviewer" in content:
                return {
                    "content": '{"output":{"verdict":"accept","reason":"looks good"},"confidence":0.95,"summary":"accepted"}',
                    "usage": {"prompt_tokens": 4, "completion_tokens": 4},
                }
            # Default for other nodes (solver, etc.)
            node_id = "stub"
            for line in content.splitlines():
                if line.startswith("Node ID: "):
                    node_id = line.split("Node ID: ", 1)[1].strip()
                    break
            return {
                "content": '{"output":{"result":{"node_id":"%s"}},"confidence":0.9,"summary":"ok"}' % node_id,
                "usage": {"prompt_tokens": 4, "completion_tokens": 4},
            }

    blueprint = _make_review_blueprint(ReviewPolicy.verify_then_correct)
    executor = BlueprintExecutor(
        llm_router=LLMRouter(client=ReviewerAcceptClient(), allow_offline_fallback=False),
        allow_offline_fallback=False,
    )
    report = executor.execute_blueprint(blueprint)

    assert report.success
    reviser_trace = [t for t in report.traces if t.node_id == "reviser"]
    assert len(reviser_trace) == 1
    assert reviser_trace[0].status == "skipped"
    assert reviser_trace[0].trace.get("skipped_by_review_policy") == "reviewer_accepted"


def test_review_policy_verify_then_correct_reject() -> None:
    """When reviewer returns verdict=reject, the reviser should run normally."""

    class ReviewerRejectClient:
        def complete(self, model, messages, *, response_format=None):
            content = messages[-1]["content"]
            if "Node ID: reviewer" in content:
                return {
                    "content": '{"output":{"verdict":"reject","reason":"needs work"},"confidence":0.5,"summary":"rejected"}',
                    "usage": {"prompt_tokens": 4, "completion_tokens": 4},
                }
            if "Node ID: reviser" in content:
                return {
                    "content": '{"output":{"result":{"node_id":"reviser","fixed":true}},"confidence":0.85,"summary":"revised"}',
                    "usage": {"prompt_tokens": 4, "completion_tokens": 4},
                }
            # Default for other nodes (solver, etc.)
            node_id = "stub"
            for line in content.splitlines():
                if line.startswith("Node ID: "):
                    node_id = line.split("Node ID: ", 1)[1].strip()
                    break
            return {
                "content": '{"output":{"result":{"node_id":"%s"}},"confidence":0.9,"summary":"ok"}' % node_id,
                "usage": {"prompt_tokens": 4, "completion_tokens": 4},
            }

    blueprint = _make_review_blueprint(ReviewPolicy.verify_then_correct)
    executor = BlueprintExecutor(
        llm_router=LLMRouter(client=ReviewerRejectClient(), allow_offline_fallback=False),
        allow_offline_fallback=False,
    )
    report = executor.execute_blueprint(blueprint)

    assert report.success
    reviser_trace = [t for t in report.traces if t.node_id == "reviser"]
    assert len(reviser_trace) == 1
    assert reviser_trace[0].status == "success"


def test_assemble_inputs_includes_prior_attempts() -> None:
    """Node with include_prior_attempts meta should receive prior_attempts in inputs."""
    seen_inputs: dict = {}

    def handler(node, inputs, context):
        seen_inputs[node.node_id] = inputs
        return NodeExecutionResult(
            outputs={"result": {"node_id": node.node_id}},
            confidence=0.9,
        )

    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="runtime-prior-attempts", title="Runtime", description="Desc"),
        base_nodes=[
            NodeSpec(
                node_id="solver",
                kind=NodeKind.agent,
                role="Solver",
                model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
            ),
            NodeSpec(
                node_id="aggregator",
                kind=NodeKind.agent,
                role="Aggregator",
                model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
                meta={"include_prior_attempts": True},
            ),
        ],
        base_edges=[EdgeSpec(edge_id="e1", src="solver", dst="aggregator")],
    )

    report = BlueprintExecutor().execute_blueprint(blueprint, handlers={
        "solver": handler,
        "aggregator": handler,
    })

    assert report.success
    aggregator_inputs = seen_inputs["aggregator"]
    assert "prior_attempts" in aggregator_inputs
    prior = aggregator_inputs["prior_attempts"]
    assert len(prior) == 1
    assert prior[0]["node_id"] == "solver"
    assert prior[0]["outputs"] == {"result": {"node_id": "solver"}}
    assert prior[0]["confidence"] == 0.9
    assert prior[0]["failure_type"] == "none"
