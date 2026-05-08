"""AgentCard — typed YAML-loadable spec for one external sandboxed agent.

Mirrors the YAML shape in `docs/experiments/case_study_1.md` §4.3 so users can author cards
by hand and AgentCo-Op can also generate them from a `RepoProfile` /
`SandboxSpec`. Used by the `external_repo_collaboration` meta-skill.

This module is **additive**: it does not replace `AgentSkill` (which the
existing skill registry uses). An AgentCard is the higher-level wrapper
around an external repository — typed I/O contract, container reference,
role description.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

from agentcoop.core.repo_profile import RepoProfile
from agentcoop.core.sandbox_build import SandboxSpec


AgentKindT = Literal[
    "sandboxed_external_agent",
    "agentcoop_internal",
    "llm_backed_agent_node",
    "evaluator_gate",
]


class AgentCard(BaseModel):
    """A YAML-loadable agent specification.

    Strictly typed against the docs/experiments/case_study_1.md §4.3 schema so author and
    machine see the same shape.
    """

    model_config = ConfigDict(extra="allow")

    name: str
    kind: AgentKindT = "sandboxed_external_agent"
    repository: str | None = None
    container: str | None = None
    role: str = ""
    capabilities: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    sandbox_spec: SandboxSpec | None = None

    notes: list[str] = Field(default_factory=list)

    # ---- IO ---------------------------------------------------------------

    @classmethod
    def from_yaml(cls, path: str | Path) -> "AgentCard":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls.model_validate(data)

    def to_yaml(self, path: str | Path) -> None:
        Path(path).write_text(
            yaml.safe_dump(self.model_dump(exclude_none=True), sort_keys=False),
            encoding="utf-8",
        )

    # ---- Convenience constructors ----------------------------------------

    @classmethod
    def from_profile(
        cls,
        profile: RepoProfile,
        *,
        role: str = "",
        capabilities: Iterable[str] | None = None,
        sandbox_spec: SandboxSpec | None = None,
        container: str | None = None,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> "AgentCard":
        """Build a default AgentCard from a `RepoProfile`."""
        caps = list(capabilities) if capabilities else list(profile.candidate_capabilities)
        return cls(
            name=profile.repo_name,
            repository=profile.repo_url,
            container=container or f"agentcoop-{profile.repo_name.lower()}:case-study",
            role=role,
            capabilities=caps,
            input_schema=input_schema or _default_input_schema(),
            output_schema=output_schema or _default_output_schema(),
            sandbox_spec=sandbox_spec,
        )


# ---------------------------------------------------------------------------
# AgentRegistry — small helper for groups of cards
# ---------------------------------------------------------------------------


class AgentRegistry(BaseModel):
    """A typed bundle of AgentCards consumed by the planner / runtime."""

    model_config = ConfigDict(extra="allow")

    cards: dict[str, AgentCard] = Field(default_factory=dict)

    def add(self, card: AgentCard) -> None:
        self.cards[card.name] = card

    def get(self, name: str) -> AgentCard:
        return self.cards[name]

    def names(self) -> list[str]:
        return list(self.cards.keys())

    def to_json_dict(self) -> dict[str, Any]:
        return {"cards": {n: c.model_dump(exclude_none=True) for n, c in self.cards.items()}}

    @classmethod
    def from_dir(cls, path: str | Path) -> "AgentRegistry":
        reg = cls()
        for p in sorted(Path(path).glob("*.yaml")):
            reg.add(AgentCard.from_yaml(p))
        return reg


# ---------------------------------------------------------------------------
# Default schemas
# ---------------------------------------------------------------------------


def _default_input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["task_description", "output_dir"],
        "properties": {
            "task_description": {"type": "string"},
            "output_dir": {"type": "string"},
            "input": {"type": "object"},
        },
    }


def _default_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["status", "artifacts", "summary"],
        "properties": {
            "status": {"enum": ["success", "failed", "partial"]},
            "artifacts": {"type": "object"},
            "summary": {"type": "string"},
            "warnings": {"type": "array"},
        },
    }


__all__ = ["AgentCard", "AgentRegistry"]
