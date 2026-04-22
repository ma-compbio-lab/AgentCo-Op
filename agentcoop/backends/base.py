"""Backend protocol and registry.

Every execution backend (llm, mcp, python_sandbox, sandbox_repo,
human_review) exposes the same `execute(node, payload, context) ->
NodeResult` coroutine. The runtime orchestrator never talks to concrete
SDKs; it only sees a `Backend`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agentcoop.core.schema import NodeResult, NodeSpec


@dataclass
class NodeContext:
    """Everything a backend needs at call time beyond the node spec itself.

    Kept small on purpose — anything bigger (blackboard snapshots, shared
    config) is summarised into `memory_summary` by the runtime.
    """

    run_id: str
    task_id: str
    memory_summary: list[dict[str, Any]] = field(default_factory=list)
    shared_inputs: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Backend(Protocol):
    name: str

    async def execute(
        self,
        node: NodeSpec,
        payload: dict[str, Any],
        context: NodeContext,
    ) -> NodeResult: ...


class BackendRegistry:
    def __init__(self) -> None:
        self._backends: dict[str, Backend] = {}

    def register(self, backend: Backend) -> None:
        self._backends[backend.name] = backend

    def get(self, name: str) -> Backend:
        if name not in self._backends:
            raise KeyError(f"backend '{name}' not registered (have {list(self._backends)})")
        return self._backends[name]

    def names(self) -> list[str]:
        return sorted(self._backends)


__all__ = ["Backend", "BackendRegistry", "NodeContext"]
