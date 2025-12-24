from __future__ import annotations

from core.contracts import AgentSpec


class Registry:
    def __init__(self) -> None:
        self._agents: dict[str, AgentSpec] = {}

    def register_agent(self, spec: AgentSpec) -> None:
        self._agents[spec.agent_id] = spec

    def get(self, agent_id: str) -> AgentSpec | None:
        return self._agents.get(agent_id)

    def all(self) -> list[AgentSpec]:
        return list(self._agents.values())

    def filter(self, required_caps: list[str], input_types: list[str]) -> list[AgentSpec]:
        matches: list[AgentSpec] = []
        for spec in self._agents.values():
            if not all(cap in spec.capabilities for cap in required_caps):
                continue
            if not any(i in spec.input_types for i in input_types):
                continue
            matches.append(spec)
        return matches
