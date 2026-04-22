from __future__ import annotations

from pathlib import Path

from agentcoop.core.schema import Budget, TaskProfile
from agentcoop.skills import SkillRegistry

PKG_SKILLS = Path(__file__).resolve().parents[2] / "agentcoop" / "skills"


def test_registry_loads_all_skills() -> None:
    reg = SkillRegistry().load_dir(PKG_SKILLS)
    assert len(reg.meta) == 12
    assert len(reg.agents) == 7


def test_meta_retrieval_math() -> None:
    reg = SkillRegistry().load_dir(PKG_SKILLS)
    prof = TaskProfile(
        task_id="t",
        raw_task="What is 13 * 17?",
        domain=["math"],
        answer_type="short_answer",
        verification_available="exact",
        difficulty="simple",
        budget=Budget(),
    )
    top = reg.search_meta(prof, top_k=3)
    names = [m.name for m in top]
    assert "math_specialist_route" in names


def test_meta_retrieval_code() -> None:
    reg = SkillRegistry().load_dir(PKG_SKILLS)
    prof = TaskProfile(
        task_id="t",
        raw_task="Implement reverse(s)",
        domain=["code"],
        answer_type="program",
        verification_available="unit_test",
        difficulty="moderate",
        tool_need=0.6,
        budget=Budget(),
    )
    top = reg.search_meta(prof, top_k=3)
    assert top[0].name == "code_test_repair_loop"


def test_meta_retrieval_repo() -> None:
    reg = SkillRegistry().load_dir(PKG_SKILLS)
    prof = TaskProfile(
        task_id="t",
        raw_task="Run BioDiscoveryAgent on IFNG",
        domain=["bio", "repo"],
        answer_type="report",
        verification_available="rubric",
        difficulty="complex",
        repo_execution_need=0.9,
        budget=Budget(),
    )
    top_agents = reg.search_agents(prof, top_k=3)
    assert "biodiscovery_agent" in [a.name for a in top_agents]
