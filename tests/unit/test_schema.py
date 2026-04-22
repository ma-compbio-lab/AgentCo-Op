"""Typed-IR round-trip tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentcoop.core.schema import (
    Budget,
    EdgeSpec,
    GatePolicy,
    NodeSpec,
    TaskProfile,
    WorkflowBlueprint,
)


def make_profile() -> TaskProfile:
    return TaskProfile(task_id="t", raw_task="q", budget=Budget())


def test_blueprint_roundtrip() -> None:
    bp = WorkflowBlueprint(
        blueprint_id="bp",
        task_profile=make_profile(),
        nodes=[NodeSpec(node_id="n1", role="solver", backend="llm")],
        edges=[EdgeSpec(source="n1", target="n1")],
        gate_policies=[GatePolicy(name="g1", trigger="low_confidence", action="retry_node")],
    )
    serialized = bp.model_dump_json()
    restored = WorkflowBlueprint.model_validate_json(serialized)
    assert restored == bp


def test_invalid_backend_rejected() -> None:
    with pytest.raises(ValidationError):
        NodeSpec(node_id="n1", role="solver", backend="not_a_backend")


def test_numeric_bounds_enforced() -> None:
    with pytest.raises(ValidationError):
        TaskProfile(task_id="t", raw_task="q", budget=Budget(), tool_need=1.5)
