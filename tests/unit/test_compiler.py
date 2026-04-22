from __future__ import annotations

from pathlib import Path

from agentcoop.core.compiler import compile_workflow
from agentcoop.core.profiler import profile_task
from agentcoop.skills import SkillRegistry

SKILLS = Path(__file__).resolve().parents[2] / "agentcoop" / "skills"


def _compile(task: str):
    reg = SkillRegistry().load_dir(SKILLS)
    profile = profile_task(task).profile
    return compile_workflow(profile, reg)


def test_trivial_math_picks_l0() -> None:
    bp = _compile("What is 13 * 17?")
    assert bp.topology_level == 0
    assert any("simple_direct_answer" in p for p in bp.provenance)


def test_code_picks_l6() -> None:
    bp = _compile("Implement reverse(s). Pass the unit tests.")
    assert bp.topology_level == 6
    assert any("code_test_repair_loop" in p for p in bp.provenance)


def test_hotpot_picks_retrieval() -> None:
    bp = _compile("Who directed the movie that won Best Picture in 1994? HotpotQA multi-hop.")
    assert any(n.backend == "mcp" for n in bp.nodes)


def test_drop_picks_numeric_reading() -> None:
    bp = _compile("How many points did the team score in total in the passage?")
    assert any("numeric_reading_comprehension" in p for p in bp.provenance)


def test_repo_picks_sandbox() -> None:
    bp = _compile("Run the BioDiscoveryAgent repo to design a perturbation screen.")
    assert any(n.backend == "sandbox_repo" for n in bp.nodes)


def test_budget_gate_always_attached() -> None:
    bp = _compile("Solve this simple puzzle.")
    assert any(g.name == "budget_near_limit" for g in bp.gate_policies)
