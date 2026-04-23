"""Tests for the AFlow workflow importer + augment-graph."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agentcoop.benchmarks.common import REPO_ROOT
from agentcoop.core.aflow_import import (
    AFlowOperatorCall,
    call_sequence_to_blueprint,
    import_aflow_workflow,
    parse_aflow_workflow,
)
from agentcoop.core.augment_graph import attach_skills_and_tools, load_gate_yaml, apply_gates


def _aflow_mbpp_graph() -> Path:
    return REPO_ROOT / "external" / "AFlow" / "workspace" / "MBPP" / "workflows" / "round_1" / "graph.py"


def test_parse_sequence_from_mbpp() -> None:
    path = _aflow_mbpp_graph()
    if not path.exists():
        pytest.skip("AFlow MBPP graph not cloned")
    calls = parse_aflow_workflow(path)
    assert any(c.operator in ("CustomCodeGenerate", "Custom") for c in calls)


def test_call_sequence_to_blueprint_assigns_roles() -> None:
    calls = [
        AFlowOperatorCall("CustomCodeGenerate", "prog", {"problem": "p"}, 1),
        AFlowOperatorCall("Test", "test", {"code": "c"}, 2),
        AFlowOperatorCall("Format", "fmt", {"draft": "d"}, 3),
    ]
    bp = call_sequence_to_blueprint(calls, dataset="mbpp", source_path="synthetic")
    roles = [n.role for n in bp.nodes]
    assert "programmer" in roles and "tool" in roles and "formatter" in roles
    assert any(e.source == bp.nodes[0].node_id for e in bp.edges)


def test_import_yaml_format(tmp_path: Path) -> None:
    yaml_path = tmp_path / "wf.yaml"
    yaml_path.write_text(
        """
calls:
  - operator: CustomCodeGenerate
    var_name: prog
    args: {problem: p}
  - operator: Test
    var_name: test
    args: {code: c}
        """.strip()
    )
    bp = import_aflow_workflow(yaml_path, dataset="mbpp")
    assert len(bp.nodes) == 2


def test_augment_attaches_skills_and_tools() -> None:
    calls = [AFlowOperatorCall("CustomCodeGenerate", "prog", {}, 1)]
    bp = call_sequence_to_blueprint(calls, dataset="mbpp", source_path="-")
    attach_skills_and_tools(bp, skills=["code_debugging"], tools=["sandbox_python"])
    assert "code_debugging" in bp.nodes[0].skill_refs
    assert "sandbox_python" in bp.nodes[0].tool_policy["allowed_tools"]


def test_load_gate_yaml_and_apply(tmp_path: Path) -> None:
    gate_path = REPO_ROOT / "configs" / "gates" / "code_runtime_gates.yaml"
    gates = load_gate_yaml(gate_path)
    assert any(g.name == "syntax_error" for g in gates)
    calls = [AFlowOperatorCall("CustomCodeGenerate", "prog", {}, 1)]
    bp = call_sequence_to_blueprint(calls, dataset="mbpp", source_path="-")
    apply_gates(bp, gates)
    assert any(g.name == "public_or_generated_test_failure" for g in bp.gate_policies)
