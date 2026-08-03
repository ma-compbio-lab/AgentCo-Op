"""Human-review backend.

In framework / dev mode we auto-approve with a note. Production deployments
should plug in a real approval channel (Slack, pager, UI).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from agentcoop.core.schema import NodeResult, NodeSpec
from agentcoop.backends.base import NodeContext


@dataclass
class HumanReviewBackend:
    name: str = "human_review"
    auto_approve: bool = True
    reviewer: Callable[[NodeSpec, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None

    async def execute(
        self,
        node: NodeSpec,
        payload: dict[str, Any],
        context: NodeContext,
    ) -> NodeResult:
        start = time.monotonic()
        if self.reviewer is not None:
            verdict = await self.reviewer(node, payload)
        elif self.auto_approve:
            verdict = {
                "approved": True,
                "comment": "auto-approved in dev mode",
                "request": payload,
            }
        else:
            verdict = {"approved": False, "comment": "auto-reject: no reviewer wired"}
        return NodeResult(
            node_id=node.node_id,
            ok=bool(verdict.get("approved", False)),
            output=verdict,
            confidence=1.0 if verdict.get("approved") else 0.0,
            latency_s=time.monotonic() - start,
        )


__all__ = ["HumanReviewBackend"]
