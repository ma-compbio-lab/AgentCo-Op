"""LLM backend: one wrapper, multiple providers via a pluggable client.

Ships:
  - `MockLLM`:   deterministic, offline. Default.
  - `OpenAIClient`: `httpx`-based Chat Completions client (no SDK dep).
                    Set `OPENAI_API_KEY` env-var; passes the prompt-cache
                    hint by reusing identical system prompts across nodes.

The `LLMBackend` consults `agentcoop.backends.prompts` to build a
role × dataset system+user prompt. For unknown roles / datasets it falls
back to a generic prompt.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

import httpx

from agentcoop.core.cost import CostLedger, DEFAULT_PRICE_TABLE_USD_PER_1K
from agentcoop.core.schema import NodeResult, NodeSpec
from agentcoop.backends.base import Backend, NodeContext
from agentcoop.backends.prompts import build_messages, parse_output


# ---------------------------------------------------------------------------
# Client protocol
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# MockLLM
# ---------------------------------------------------------------------------


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
        # Unused here; the LLMBackend picks the canned output via _mock_output.
        return {"text": "", "tokens_in": 0, "tokens_out": 0, "model": self.model}


# ---------------------------------------------------------------------------
# OpenAI client (httpx, no SDK)
# ---------------------------------------------------------------------------


_DEFAULT_OPENAI_URL = "https://api.openai.com/v1/chat/completions"


def _is_reasoning_model(model: str) -> bool:
    """gpt-5 family + o-series use the responses-style param set
    (`max_completion_tokens` + optional `reasoning_effort`). Detection is
    name-based on purpose so the function works without a network round trip.
    """
    m = (model or "").lower()
    return (
        m.startswith("gpt-5")
        or m.startswith("o1")
        or m.startswith("o3")
        or m.startswith("o4")
    )


@dataclass
class OpenAIClient:
    model: str = "gpt-4o-mini"
    api_key: str | None = None
    base_url: str = _DEFAULT_OPENAI_URL
    timeout_s: float = 90.0
    max_retries: int = 4
    _client: httpx.AsyncClient | None = None

    def __post_init__(self) -> None:
        self.api_key = self.api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAIClient: OPENAI_API_KEY is not set")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_s, connect=10.0),
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def complete(
        self,
        *,
        system: str,
        user: str,
        response_format: dict[str, Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        # gpt-5 family + o-series reasoning models use `max_completion_tokens`
        # and accept an optional `reasoning_effort` knob. They don't accept
        # arbitrary `temperature` either (only the default). The legacy
        # `gpt-4o*` / `gpt-4*` / `gpt-3.5*` chat models keep the original
        # `temperature` + `max_tokens` parameters bit-identically so existing
        # benchmark numbers are unaffected.
        is_reasoning = _is_reasoning_model(self.model)
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if is_reasoning:
            body["max_completion_tokens"] = int(max_tokens)
            if reasoning_effort:
                body["reasoning_effort"] = str(reasoning_effort)
        else:
            body["temperature"] = float(temperature)
            body["max_tokens"] = int(max_tokens)
        if response_format:
            body["response_format"] = response_format

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        last_exc: Exception | None = None
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            try:
                assert self._client is not None
                resp = await self._client.post(self.base_url, json=body, headers=headers)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"upstream {resp.status_code}: {resp.text[:200]}",
                        request=resp.request,
                        response=resp,
                    )
                resp.raise_for_status()
                data = resp.json()
                choice = data["choices"][0]["message"]["content"] or ""
                usage = data.get("usage", {}) or {}
                return {
                    "text": choice,
                    "tokens_in": int(usage.get("prompt_tokens", 0)),
                    "tokens_out": int(usage.get("completion_tokens", 0)),
                    "model": data.get("model", self.model),
                }
            except (httpx.HTTPStatusError, httpx.HTTPError, asyncio.TimeoutError) as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    break
                await asyncio.sleep(delay)
                delay = min(delay * 2.0, 16.0)
        # Surface the failure as a typed dict instead of crashing the runtime.
        return {
            "text": "",
            "tokens_in": 0,
            "tokens_out": 0,
            "model": self.model,
            "error": f"{type(last_exc).__name__}: {last_exc}" if last_exc else "unknown",
        }


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


class LLMBackend:
    """Backend that turns a prompt template + payload into a NodeResult."""

    name = "llm"

    def __init__(
        self,
        client: LLMClient | None = None,
        *,
        price_table: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        self.client = client or MockLLM()
        self._ledger = CostLedger(
            price_table=price_table or dict(DEFAULT_PRICE_TABLE_USD_PER_1K)
        )

    async def execute(
        self,
        node: NodeSpec,
        payload: dict[str, Any],
        context: NodeContext,
    ) -> NodeResult:
        start = time.monotonic()

        if isinstance(self.client, MockLLM):
            output = await _mock_output(self.client, node, payload)
            tokens_in = _approx_tokens(node.role, payload)
            tokens_out = _approx_tokens_output(output)
            text_for_summary = output.get("rationale_summary", "") if isinstance(output, dict) else ""
            cost_usd = self._ledger.price(self.client.model, tokens_in, tokens_out)
        else:
            dataset = _dataset_from_payload(payload)
            system, user, want_json = build_messages(
                role=node.role,
                node_id=node.node_id,
                dataset=dataset,
                payload=payload,
            )
            response_format = {"type": "json_object"} if want_json else None
            raw = await self.client.complete(
                system=node.prompt_template or system,
                user=user,
                response_format=response_format,
                temperature=float(node.params.get("temperature", 0.0)),
                max_tokens=int(node.params.get("max_tokens", 4096)),
            )
            text = raw.get("text", "") or ""
            tokens_in = int(raw.get("tokens_in", 0))
            tokens_out = int(raw.get("tokens_out", 0))
            output = parse_output(
                role=node.role,
                node_id=node.node_id,
                dataset=dataset,
                raw_text=text,
            )
            text_for_summary = output.get("rationale_summary", "")
            if "error" in raw:
                output.setdefault("issues", []).append(raw["error"])
            cost_usd = self._ledger.price(raw.get("model", self.client.model), tokens_in, tokens_out)

        latency = time.monotonic() - start
        confidence = float(output.get("confidence", 0.5)) if isinstance(output, dict) else 0.5
        return NodeResult(
            node_id=node.node_id,
            ok=bool(output) and not (isinstance(output, dict) and output.get("issues") == ["json_parse_fail"]),
            output=output if isinstance(output, dict) else {"text": str(output)},
            confidence=confidence,
            evidence=output.get("evidence", []) if isinstance(output, dict) else [],
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            latency_s=latency,
            logs_summary=text_for_summary,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dataset_from_payload(payload: dict[str, Any]) -> str | None:
    if not isinstance(payload, dict):
        return None
    task_input = payload.get("task_input", {}) or {}
    if isinstance(task_input, dict):
        ds = task_input.get("dataset")
        if ds:
            return str(ds)
        inner = task_input.get("input", {}) or {}
        if isinstance(inner, dict) and inner.get("dataset"):
            return str(inner["dataset"])
    return payload.get("dataset")


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


__all__ = ["LLMBackend", "MockLLM", "OpenAIClient", "LLMClient"]
