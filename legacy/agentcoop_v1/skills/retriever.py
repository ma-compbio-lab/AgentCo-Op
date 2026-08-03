"""Thin wrapper exposing retrieval helpers used by the compiler."""

from __future__ import annotations

from agentcoop.core.schema import AgentSkill, MetaSkill, TaskProfile
from agentcoop.skills.registry import SkillRegistry


def retrieve_meta_skills(
    registry: SkillRegistry, profile: TaskProfile, top_k: int = 8
) -> list[MetaSkill]:
    return registry.search_meta(profile, top_k=top_k)


def retrieve_agent_skills(
    registry: SkillRegistry, profile: TaskProfile, top_k: int = 20
) -> list[AgentSkill]:
    return registry.search_agents(profile, top_k=top_k)


__all__ = ["retrieve_meta_skills", "retrieve_agent_skills"]
