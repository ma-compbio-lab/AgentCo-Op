"""Tests for the gate triggers added in session 3."""

from __future__ import annotations

import pytest

from agentcoop.core.gates import evaluate_gates
from agentcoop.core.schema import (
    Budget,
    GatePolicy,
    NodeResult,
    NodeSpec,
    RunState,
    TaskProfile,
    WorkflowBlueprint,
)


def _make(policy: GatePolicy, result: NodeResult, node: NodeSpec | None = None) -> list[tuple[GatePolicy, str]]:
    node = node or NodeSpec(node_id="n1", role="specialist", backend="llm")
    bp = WorkflowBlueprint(
        blueprint_id="bp",
        task_profile=TaskProfile(task_id="t", raw_task="q", budget=Budget()),
        nodes=[node],
        edges=[],
        gate_policies=[policy],
    )
    return evaluate_gates(bp, RunState(run_id="r", blueprint_id="bp"), node, result)


@pytest.mark.parametrize(
    "trigger,result_kwargs",
    [
        ("answer_format_invalid", {"output": {"format_invalid": True}}),
        ("solver_disagreement", {"output": {"solver_answers": ["42", "43"]}}),
        ("method_disagreement", {"metrics": {"method_disagreement": "DESeq2 vs edgeR"}}),
        ("symbolic_check_fail", {"metrics": {"symbolic_check": False}}),
        ("numeric_inconsistency", {"metrics": {"numeric_inconsistency": True}}),
        ("multi_span_conflict", {"metrics": {"multi_span_conflict": True}}),
        ("arithmetic_fail", {"metrics": {"arithmetic_verifier": False}}),
        ("answer_extraction_fail", {"output": {"final_answer": ""}}),
        ("gene_mapping_low", {"metrics": {"mapping_rate": 0.4}}),
        ("enrichment_empty", {"metrics": {"enrichment_rows": 0}}),
        ("too_few_markers", {"metrics": {"num_markers": 2}}),
        ("geneagent_unsupported_claim", {"metrics": {"unsupported_claims": 2}}),
        ("model_env_fail", {"errors": ["ImportError: missing"]}),
        ("prediction_schema_invalid", {"output": {"perturbation": "p"}}),
        ("simple_baseline_beats_all", {"metrics": {"simple_baseline_wins": True}}),
    ],
)
def test_each_new_trigger_fires(trigger: str, result_kwargs: dict) -> None:
    policy = GatePolicy(name=trigger, trigger=trigger, action="retry_node", max_activations=1)
    node = NodeSpec(
        node_id="n1",
        role="specialist",
        backend="llm",
        output_schema={"required": ["answer"]} if trigger == "answer_format_invalid" else {},
    )
    result = NodeResult(node_id="n1", **result_kwargs)
    fired = _make(policy, result, node)
    assert fired and fired[0][0].name == trigger, (trigger, fired)


def test_boxed_answer_missing_quiet_when_present() -> None:
    policy = GatePolicy(
        name="boxed_answer_missing", trigger="boxed_answer_missing",
        action="add_formatter", max_activations=1,
    )
    good = NodeResult(node_id="n1", output={"final_answer": r"\boxed{3}"})
    assert not _make(policy, good)
