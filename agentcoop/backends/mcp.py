"""MCP / tool backend — framework-only stub.

Validates that the declared tool is registered and returns a canned result.
A production implementation would forward the call through the MCP client.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from agentcoop.core.schema import NodeResult, NodeSpec
from agentcoop.backends.base import NodeContext


ToolFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass
class MCPBackend:
    name: str = "mcp"
    tools: dict[str, ToolFn] = field(default_factory=dict)

    def register_tool(self, tool_name: str, fn: ToolFn) -> None:
        self.tools[tool_name] = fn

    async def execute(
        self,
        node: NodeSpec,
        payload: dict[str, Any],
        context: NodeContext,
    ) -> NodeResult:
        start = time.monotonic()
        tool_name = node.params.get("tool") or (
            node.skill_refs[0] if node.skill_refs else node.node_id
        )
        if tool_name not in self.tools:
            return NodeResult(
                node_id=node.node_id,
                ok=False,
                output={},
                errors=[f"tool '{tool_name}' not registered"],
                latency_s=time.monotonic() - start,
            )
        try:
            result = await self.tools[tool_name](payload)
            return NodeResult(
                node_id=node.node_id,
                ok=bool(result.get("ok", True)),
                output=result,
                confidence=result.get("confidence"),
                artifacts=list(result.get("artifacts", [])),
                latency_s=time.monotonic() - start,
            )
        except Exception as exc:  # surface tool errors to the gate layer
            return NodeResult(
                node_id=node.node_id,
                ok=False,
                output={},
                errors=[f"{type(exc).__name__}: {exc}"],
                latency_s=time.monotonic() - start,
            )


__all__ = ["MCPBackend"]
