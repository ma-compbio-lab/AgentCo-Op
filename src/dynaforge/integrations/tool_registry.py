from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from dynaforge.ir.schema import MCPServerRef, NodeSpec, ToolDiscoveryMode, ToolRef, WorkflowBlueprint
from dynaforge.integrations.mcp_client import MCPClientError, MCPClientManager

JsonDict = Dict[str, Any]


@dataclass(frozen=True)
class ToolCandidate:
    ref: ToolRef
    descriptor: JsonDict
    source: str


class ToolRegistry:
    """Enumerates MCP tools across a blueprint and exposes candidates for node-level discovery."""

    def __init__(self, mcp_client: Optional[MCPClientManager] = None):
        self.mcp_client = mcp_client or MCPClientManager()
        self._server_cache: Dict[Tuple[Any, ...], List[JsonDict]] = {}

    def describe_bound_tools(
        self,
        node: NodeSpec,
        *,
        blueprint: WorkflowBlueprint,
        sandbox_runner: Any = None,
    ) -> List[ToolCandidate]:
        if not node.tools:
            return []
        descriptors = self.mcp_client.describe_tools(
            node.tools,
            blueprint=blueprint,
            sandbox_runner=sandbox_runner,
        )
        candidates: List[ToolCandidate] = []
        for tool_ref, descriptor in zip(node.tools, descriptors):
            candidates.append(
                ToolCandidate(
                    ref=tool_ref,
                    descriptor=dict(descriptor),
                    source="bound",
                )
            )
        return self._dedupe(candidates)

    def discover_for_node(
        self,
        node: NodeSpec,
        *,
        blueprint: WorkflowBlueprint,
        sandbox_runner: Any = None,
    ) -> List[ToolCandidate]:
        discovery = node.tool_discovery
        if discovery is None or discovery.mode == ToolDiscoveryMode.disabled:
            return self.describe_bound_tools(node, blueprint=blueprint, sandbox_runner=sandbox_runner)

        registry_candidates = self._registry_candidates(
            blueprint,
            allowlist=discovery.server_allowlist,
            denylist=discovery.server_denylist,
            sandbox_runner=sandbox_runner,
        )
        if discovery.mode == ToolDiscoveryMode.registry:
            return registry_candidates
        return self._dedupe(self.describe_bound_tools(node, blueprint=blueprint, sandbox_runner=sandbox_runner) + registry_candidates)

    def _registry_candidates(
        self,
        blueprint: WorkflowBlueprint,
        *,
        allowlist: Sequence[str],
        denylist: Sequence[str],
        sandbox_runner: Any = None,
    ) -> List[ToolCandidate]:
        allowed = {name for name in allowlist if name}
        denied = {name for name in denylist if name}
        candidates: List[ToolCandidate] = []
        for server_ref in blueprint.mcp_servers:
            if allowed and server_ref.name not in allowed:
                continue
            if server_ref.name in denied:
                continue
            for descriptor in self._list_server_tools(server_ref, sandbox_runner=sandbox_runner):
                tool_name = str(descriptor.get("tool") or descriptor.get("name") or "").strip()
                if ":" in tool_name:
                    tool_name = tool_name.split(":", 1)[1]
                if not tool_name:
                    continue
                candidates.append(
                    ToolCandidate(
                        ref=ToolRef(server=server_ref.name, tool=tool_name),
                        descriptor=dict(descriptor),
                        source="registry",
                    )
                )
        return self._dedupe(candidates)

    def _list_server_tools(self, server_ref: MCPServerRef, *, sandbox_runner: Any = None) -> List[JsonDict]:
        cache_key = self._cache_key(server_ref)
        if cache_key in self._server_cache:
            return [dict(item) for item in self._server_cache[cache_key]]
        try:
            descriptors = self.mcp_client.list_tools(server_ref, sandbox_runner=sandbox_runner)
        except MCPClientError:
            descriptors = []
        self._server_cache[cache_key] = [dict(item) for item in descriptors]
        return [dict(item) for item in descriptors]

    @staticmethod
    def _cache_key(server_ref: MCPServerRef) -> Tuple[Any, ...]:
        sandbox = server_ref.sandbox
        return (
            server_ref.name,
            server_ref.transport.value,
            server_ref.url,
            tuple(server_ref.stdio_cmd or []),
            sandbox.sandbox_id if sandbox is not None else None,
        )

    @staticmethod
    def _dedupe(candidates: Iterable[ToolCandidate]) -> List[ToolCandidate]:
        seen: set[tuple[str, str]] = set()
        deduped: List[ToolCandidate] = []
        for candidate in candidates:
            key = (candidate.ref.server, candidate.ref.tool)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        return deduped
