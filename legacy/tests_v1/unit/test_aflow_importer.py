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
from agentcoop.core.augment_graph import (
    apply_gates,
    attach_skills_and_tools,
    load_gate_yaml,
    select_skills_and_tools_for_profile,
)
from agentcoop.core.profiler import profile_task
from agentcoop.skills import SkillRegistry


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


def _registry_with_configs() -> SkillRegistry:
    reg = SkillRegistry().load_dir(REPO_ROOT / "agentcoop" / "skills")
    reg.load_dir(REPO_ROOT / "configs" / "skills")
    return reg


def test_select_skills_and_tools_for_mbpp_profile() -> None:
    """MBPP profile must dynamically pull the code skills + sandbox tools."""
    reg = _registry_with_configs()
    profile = profile_task("Write a Python function.", dataset="mbpp").profile
    skills, tools = select_skills_and_tools_for_profile(profile, reg)
    assert "code_debugging" in skills
    assert "python_testing" in skills
    assert "sandbox_python" in tools
    assert "generated_tests" in tools


def test_select_skills_and_tools_for_math_profile() -> None:
    """MATH profile must pull the math-family skills + symbolic tools."""
    reg = _registry_with_configs()
    profile = profile_task("Solve the equation x^2 = 4.", dataset="math").profile
    skills, tools = select_skills_and_tools_for_profile(profile, reg)
    assert any(s in skills for s in ("algebraic_simplification", "math_algebra_solver"))
    assert "sympy_checker" in tools


def test_select_skills_skips_unrelated_domains() -> None:
    """A code profile must NOT pick up math-only skills."""
    reg = _registry_with_configs()
    profile = profile_task("Write a Python function.", dataset="mbpp").profile
    skills, _ = select_skills_and_tools_for_profile(profile, reg)
    assert "algebraic_simplification" not in skills
    assert "number_theory" not in skills
