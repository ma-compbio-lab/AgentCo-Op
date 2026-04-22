"""LLM backend: one wrapper, multiple providers via a pluggable client.

For the framework phase we ship only `MockLLM` (deterministic, offline).
A real `OpenAILLM` / `AnthropicLLM` can be dropped in by implementing the
`LLMClient` protocol.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from agentcoop.core.schema import NodeResult, NodeSpec
from agentcoop.backends.base import Backend, NodeContext


class LLMClient(Protocol):
    model: str

    async def complete(
        self,
        *,
        system: str,
        user: str,
        response_format: dict[str, Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> dict[str, Any]: ...


@dataclass
class MockLLM:
    """Deterministic mock. Returns canned outputs keyed by role or node_id.

    Tests can either register responses via `register(...)` or pass a
    callable `responder(node, payload) -> dict`.
    """

    model: str = "mock-llm"
    canned: dict[str, dict[str, Any]] = field(default_factory=dict)
    responder: Callable[[NodeSpec, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None

    def register(self, key: str, payload: dict[str, Any]) -> None:
        self.canned[key] = payload

    async def complete(
        self,
        *,
        system: str,
        user: str,
        response_format: dict[str, Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        # Unused here; the LLMBackend picks the canned output.
        return {"system": system, "user": user}


class LLMBackend:
    """Backend that turns a prompt template + payload into a NodeResult."""

    name = "llm"

    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client or MockLLM()

    async def execute(
        self,
        node: NodeSpec,
        payload: dict[str, Any],
        context: NodeContext,
    ) -> NodeResult:
        start = time.monotonic()
        system = node.prompt_template or _default_system_for(node.role)

        if isinstance(self.client, MockLLM):
            output = await _mock_output(self.client, node, payload)
            tokens_in, tokens_out = _approx_tokens(system, payload), _approx_tokens_output(output)
        else:
            raw = await self.client.complete(
                system=system,
                user=json.dumps(payload, ensure_ascii=False),
                response_format={"type": "json_object"},
                temperature=float(node.params.get("temperature", 0.0)),
                max_tokens=int(node.params.get("max_tokens", 4096)),
            )
            output = raw.get("output", raw)
            tokens_in = int(raw.get("tokens_in", _approx_tokens(system, payload)))
            tokens_out = int(raw.get("tokens_out", _approx_tokens_output(output)))

        latency = time.monotonic() - start
        confidence = float(output.get("confidence", 0.5)) if isinstance(output, dict) else 0.5
        return NodeResult(
            node_id=node.node_id,
            ok=True,
            output=output if isinstance(output, dict) else {"text": str(output)},
            confidence=confidence,
            evidence=output.get("evidence", []) if isinstance(output, dict) else [],
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_s=latency,
            logs_summary=output.get("rationale_summary", "") if isinstance(output, dict) else "",
        )


async def _mock_output(client: MockLLM, node: NodeSpec, payload: dict[str, Any]) -> dict[str, Any]:
    if client.responder is not None:
        return await client.responder(node, payload)
    for key in (node.node_id, node.role, f"*:{node.role}"):
        if key in client.canned:
            return dict(client.canned[key])
    return {
        "answer": "",
        "confidence": 0.5,
        "rationale_summary": f"mock output for {node.node_id}",
    }


def _approx_tokens(*texts: Any) -> int:
    total = 0
    for t in texts:
        s = t if isinstance(t, str) else json.dumps(t, ensure_ascii=False)
        total += max(1, len(s) // 4)
    return total


def _approx_tokens_output(output: Any) -> int:
    s = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
    return max(1, len(s) // 4)


def _default_system_for(role: str) -> str:
    return (
        f"You are the `{role}` node in AgentCo-Op. "
        "Respond with JSON conforming to the declared output schema. "
        "Do not include hidden chain-of-thought; include a one-paragraph "
        "`rationale_summary` only."
    )


__all__ = ["LLMBackend", "MockLLM", "LLMClient"]
