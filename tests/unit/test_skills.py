from __future__ import annotations

from pathlib import Path

from agentcoop.core.schema import Budget, TaskProfile
from agentcoop.skills import SkillRegistry

PKG_SKILLS = Path(__file__).resolve().parents[2] / "agentcoop" / "skills"


def test_registry_loads_all_skills() -> None:
    reg = SkillRegistry().load_dir(PKG_SKILLS)
    # Session 3 removed the v1 domain_agent_collaboration / biodiscovery /
    # spatial skill cards (left 11 meta + 5 agents). Session 7 adds the
    # `external_repo_collaboration` L7 meta-skill (12 meta + 5 agents).
    assert len(reg.meta) == 12
    assert len(reg.agents) == 5


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
    """Case-study repo skills live in `agentcoop/wrappers/*` after session 3,
    not as agent-skill YAMLs, so the registry-level retrieval only has
    general-purpose skills. This test keeps the registry sanity."""
    reg = SkillRegistry().load_dir(PKG_SKILLS)
    prof = TaskProfile(
        task_id="t",
        raw_task="Run a Dockerized repo in a sandbox",
        domain=["repo"],
        answer_type="report",
        verification_available="rubric",
        difficulty="complex",
        repo_execution_need=0.9,
        budget=Budget(),
    )
    top = reg.search_meta(prof, top_k=3)
    # sandbox_repo_execution remains the most-repo-relevant meta-skill.
    assert "sandbox_repo_execution" in [m.name for m in top]
