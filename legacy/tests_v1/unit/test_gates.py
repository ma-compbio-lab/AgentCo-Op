from __future__ import annotations

from agentcoop.core.gates import (
    GateLimits,
    GraphPatchOp,
    apply_patch,
    evaluate_gates,
    plan_patch,
)
from agentcoop.core.schema import (
    Budget,
    GatePolicy,
    NodeResult,
    NodeSpec,
    RunState,
    TaskProfile,
    WorkflowBlueprint,
)


def _make_blueprint(gates: list[GatePolicy], nodes: list[NodeSpec]) -> WorkflowBlueprint:
    p = TaskProfile(task_id="t", raw_task="q", budget=Budget())
    return WorkflowBlueprint(
        blueprint_id="bp",
        task_profile=p,
        nodes=nodes,
        edges=[],
        gate_policies=gates,
    )


def test_schema_invalid_triggers() -> None:
    node = NodeSpec(
        node_id="n1",
        role="solver",
        backend="llm",
        output_schema={"required": ["answer"]},
    )
    bp = _make_blueprint(
        [GatePolicy(name="schema_invalid", trigger="schema_invalid", action="add_formatter")],
        [node],
    )
    state = RunState(run_id="r", blueprint_id="bp")
    fired = evaluate_gates(bp, state, node, NodeResult(node_id="n1", output={}))
    assert len(fired) == 1 and fired[0][0].name == "schema_invalid"


def test_low_confidence_threshold() -> None:
    node = NodeSpec(node_id="n1", role="solver", backend="llm")
    bp = _make_blueprint(
        [GatePolicy(name="low_conf", trigger="low_confidence", threshold=0.5, action="retry_node")],
        [node],
    )
    state = RunState(run_id="r", blueprint_id="bp")
    low = NodeResult(node_id="n1", output={"answer": "x"}, confidence=0.2)
    high = NodeResult(node_id="n1", output={"answer": "x"}, confidence=0.9)
    assert evaluate_gates(bp, state, node, low)
    assert not evaluate_gates(bp, state, node, high)


def test_max_activations_enforced() -> None:
    node = NodeSpec(node_id="n1", role="solver", backend="llm")
    bp = _make_blueprint(
        [GatePolicy(name="g", trigger="low_confidence", threshold=0.9, action="retry_node", max_activations=1)],
        [node],
    )
    state = RunState(run_id="r", blueprint_id="bp", gate_activations={"g": 1})
    fired = evaluate_gates(bp, state, node, NodeResult(node_id="n1", confidence=0.1))
    assert not fired


def test_patch_applied_limits() -> None:
    node = NodeSpec(node_id="n1", role="solver", backend="llm")
    bp = _make_blueprint([], [node])
    state = RunState(run_id="r", blueprint_id="bp")
    limits = GateLimits(max_same_node_repairs=1)
    p = plan_patch(GatePolicy(name="g", trigger="low_confidence", action="retry_node"), node, "low")
    assert apply_patch(bp, state, p, limits)
    # second retry allowed up to max
    assert not apply_patch(bp, state, p, limits)


def test_add_formatter_adds_node() -> None:
    node = NodeSpec(node_id="n1", role="solver", backend="llm")
    bp = _make_blueprint([], [node])
    state = RunState(run_id="r", blueprint_id="bp")
    p = plan_patch(GatePolicy(name="g", trigger="schema_invalid", action="add_formatter"), node, "bad")
    assert p.op is GraphPatchOp.ADD_FORMATTER
    apply_patch(bp, state, p, GateLimits())
    assert any(n.role == "formatter" for n in bp.nodes)
