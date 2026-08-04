"""LLM components: an optional refinement layer, never a load-bearing one.

An LLM node is allowed inside a compiled workflow, but nothing in the core
mechanism may *require* one. That is why :class:`MockLLM` exists and why it is
the default client: every probe, compile, execute, diagnose, and repair test in
this repository runs offline against it, deterministically, with no API key.

:class:`MockLLM` is keyed by ``(role, subgoal_id)``, matching how the compiler
addresses LLM nodes — a "critic" for subgoal ``s2`` gets the same answer every
time it is asked, so a workflow's behaviour is reproducible and a repair can be
attributed to the patch rather than to sampling noise.

:class:`OpenAIClient` speaks HTTP directly through httpx. No vendor SDK: the
dependency set is frozen, and an SDK would drag in retry, telemetry, and
environment-reading behaviour that this layer must own explicitly in order to
account for cost and to stay reproducible.

Behaviour contracts here are honest rather than flattering. A hosted model is
reported as non-deterministic **even at temperature 0**, because in practice it
is: fleet heterogeneity and batching change logits. Claiming otherwise would
let the determinism probe be satisfied by an assertion instead of by evidence.
"""

from __future__ import annotations

import json
import os
from typing import Any, Mapping, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from agentcoop.ir.capability import BehaviorContract, CostProfile
from agentcoop.ir.faults import FaultClass

from agentcoop.components.base import (
    Clock,
    FacetObserver,
    Invocation,
    InvocationResult,
    draft_artifact,
    failure_result,
    finalize_outputs,
    input_ids,
    perf_clock,
    render_template,
    stable_digest,
    standard_resolver,
)


class LLMRequest(BaseModel):
    """One completion request, fully determined by its fields.

    ``role`` and ``subgoal_id`` are part of the request rather than metadata
    because they are the mock's cache key: the identity of an LLM call in this
    system is "who is speaking, about what", not the prompt string.
    """

    model_config = ConfigDict(extra="forbid")

    prompt: str
    role: str = "assistant"
    subgoal_id: Optional[str] = None
    model: str = ""
    temperature: float = 0.0
    max_tokens: Optional[int] = None
    seed: int = 0
    system: Optional[str] = None

    def key(self) -> tuple[str, Optional[str]]:
        return (self.role, self.subgoal_id)


class LLMResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    usd: float = 0.0
    finish_reason: str = "stop"

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMError(RuntimeError):
    """A client could not produce a completion.

    Carries a fault class so :class:`LLMAdapter` can classify without parsing
    the message: a missing key is an environment problem, a 400 is a
    configuration problem, a 500 is a tool failure. Collapsing all three into
    "the LLM failed" is what produces a repair loop that retries a prompt which
    can never work.
    """

    def __init__(self, message: str, fault_class: FaultClass = FaultClass.TOOL_FAILURE) -> None:
        super().__init__(message)
        self.fault_class = fault_class


@runtime_checkable
class LLMClient(Protocol):
    name: str

    async def complete(self, req: LLMRequest) -> LLMResponse: ...


def estimate_tokens(text: str) -> int:
    """Deterministic token estimate.

    A crude character heuristic on purpose: an exact tokenizer would be a new
    dependency, and the number is only ever used for budget accounting and for
    relative cost comparison between candidate workflows, never as evidence.
    """
    return max(1, (len(text) + 3) // 4)


class MockLLM:
    """A deterministic stand-in keyed by ``(role, subgoal_id)``.

    Unscripted keys still answer, with text derived by hashing the key and the
    seed. That matters for the benchmark: a workflow with an unscripted LLM
    node must still run end to end and still reproduce, otherwise "works
    offline" would mean "works only where someone wrote a fixture".

    The seed participates in the synthesized text so that seed-sensitivity
    experiments are possible; the determinism probe holds the seed fixed, so
    this does not weaken the determinism guarantee.
    """

    def __init__(
        self,
        responses: Optional[Mapping[tuple[str, Optional[str]], str]] = None,
        *,
        failures: Optional[Mapping[tuple[str, Optional[str]], str]] = None,
        name: str = "mock-llm",
        model: str = "mock",
        usd_per_1k_tokens: float = 0.0,
    ) -> None:
        self.name = name
        self.model = model
        self._responses = dict(responses or {})
        self._failures = dict(failures or {})
        self._usd_per_1k = usd_per_1k_tokens
        #: Every request seen, in order. Tests assert on what was actually
        #: asked, which is how "the judge saw its own generator's output" is
        #: detectable rather than assumed.
        self.calls: list[LLMRequest] = []

    async def complete(self, req: LLMRequest) -> LLMResponse:
        self.calls.append(req)
        key = req.key()
        if key in self._failures:
            raise LLMError(self._failures[key], FaultClass.TOOL_FAILURE)
        text = self._responses.get(key)
        if text is None:
            text = (
                f"[mock:{req.role}:{req.subgoal_id or '-'}] "
                f"{stable_digest(req.role, req.subgoal_id or '', str(req.seed))}"
            )
        prompt_tokens = estimate_tokens(req.prompt) + estimate_tokens(req.system or "")
        completion_tokens = estimate_tokens(text)
        return LLMResponse(
            text=text,
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            usd=self._usd_per_1k * (prompt_tokens + completion_tokens) / 1000.0,
            finish_reason="stop",
        )

    def behavior_contract(self) -> BehaviorContract:
        return BehaviorContract(
            deterministic=True, idempotent=True, retry_safe=True, shadow_safe=True
        )


class OpenAIClient:
    """OpenAI-compatible chat completions over httpx.

    Works against any endpoint exposing ``/chat/completions``. Constructing it
    without a key is legal — importing and wiring the system must never require
    credentials — but calling it without one raises an ``ENVIRONMENT``-classed
    :class:`LLMError` rather than silently returning empty text.
    """

    def __init__(
        self,
        *,
        model: str = "gpt-4o-mini",
        api_key: Optional[str] = None,
        base_url: str = "https://api.openai.com/v1",
        api_key_env: str = "OPENAI_API_KEY",
        timeout_s: float = 120.0,
        name: str = "openai",
        usd_per_1k_prompt: float = 0.0,
        usd_per_1k_completion: float = 0.0,
    ) -> None:
        self.name = name
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._api_key_env = api_key_env
        self._timeout_s = timeout_s
        self._usd_per_1k_prompt = usd_per_1k_prompt
        self._usd_per_1k_completion = usd_per_1k_completion

    def _key(self) -> str:
        key = self._api_key or os.environ.get(self._api_key_env, "")
        if not key:
            raise LLMError(
                f"no API key: pass api_key= or set ${self._api_key_env}",
                FaultClass.ENVIRONMENT,
            )
        return key

    def request_body(self, req: LLMRequest) -> dict[str, Any]:
        """The JSON body, exposed so it can be asserted on without a network."""
        messages: list[dict[str, str]] = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        messages.append({"role": "user", "content": req.prompt})
        body: dict[str, Any] = {
            "model": req.model or self.model,
            "messages": messages,
            "temperature": req.temperature,
            "seed": req.seed,
        }
        if req.max_tokens is not None:
            body["max_tokens"] = req.max_tokens
        return body

    async def complete(self, req: LLMRequest) -> LLMResponse:
        import httpx  # imported here so the module stays importable offline

        key = self._key()
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                    json=self.request_body(req),
                )
        except Exception as exc:  # noqa: BLE001 - httpx raises a family of errors
            raise LLMError(f"transport error: {type(exc).__name__}: {exc}", FaultClass.TOOL_FAILURE)

        if response.status_code >= 500:
            raise LLMError(f"upstream error {response.status_code}", FaultClass.TOOL_FAILURE)
        if response.status_code == 429:
            raise LLMError("rate limited", FaultClass.TOOL_FAILURE)
        if response.status_code >= 400:
            raise LLMError(
                f"request rejected with {response.status_code}: {response.text[:400]}",
                FaultClass.CONFIGURATION,
            )

        try:
            data = response.json()
            choice = data["choices"][0]
            text = choice["message"]["content"] or ""
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"malformed response: {exc}", FaultClass.TOOL_FAILURE)

        usage = data.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens", estimate_tokens(req.prompt)))
        completion_tokens = int(usage.get("completion_tokens", estimate_tokens(text)))
        return LLMResponse(
            text=text,
            model=str(data.get("model", req.model or self.model)),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            usd=(
                self._usd_per_1k_prompt * prompt_tokens
                + self._usd_per_1k_completion * completion_tokens
            )
            / 1000.0,
            finish_reason=str(choice.get("finish_reason", "stop")),
        )

    def behavior_contract(self) -> BehaviorContract:
        """Non-deterministic, and said so out loud.

        Hosted inference is not reproducible even at temperature 0. Recording
        ``deterministic=False`` here means the determinism probe's verdict for
        an LLM-backed component reflects measurement, and the compiler will not
        treat two samples from this client as a replication.
        """
        return BehaviorContract(
            deterministic=False,
            idempotent=False,
            retry_safe=True,
            side_effects=["calls a paid third-party API"],
            shadow_safe=True,
        )


class LLMAdapter:
    """Render a prompt, call a client, emit a typed artifact."""

    def __init__(
        self,
        name: str,
        *,
        client: Optional[LLMClient] = None,
        output_type: str = "LLMOutput",
        output_format: str = "text",
        prompt_template: str = "{in:Prompt}",
        system_template: Optional[str] = None,
        role: str = "assistant",
        model: str = "",
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        output_facets: Optional[Mapping[str, Mapping[str, str]]] = None,
        facet_observers: Optional[Mapping[str, FacetObserver]] = None,
        clock: Clock = perf_clock,
    ) -> None:
        """``output_format='json'`` parses the completion; a parse failure is an
        ``ARTIFACT_CONTRACT`` fault, never a silently-kept string."""
        self.name = name
        self.client: LLMClient = client if client is not None else MockLLM()
        self.output_type = output_type
        self.output_format = output_format
        self.prompt_template = prompt_template
        self.system_template = system_template
        self.role = role
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._output_facets = {k: dict(v) for k, v in (output_facets or {}).items()}
        self._facet_observers = dict(facet_observers or {})
        self._clock = clock

    def behavior_contract(self) -> BehaviorContract:
        """Inherited from the client, which is the thing that actually varies."""
        contract = getattr(self.client, "behavior_contract", None)
        if callable(contract):
            result = contract()
            if isinstance(result, BehaviorContract):
                return result
        return BehaviorContract(deterministic=False, idempotent=False, shadow_safe=True)

    def build_request(self, inv: Invocation) -> LLMRequest:
        """Render the request without sending it. Testable, and inspectable."""
        resolver = standard_resolver(inv)
        template = str(inv.config.get("prompt_template", self.prompt_template))
        system_template = inv.config.get("system_template", self.system_template)
        return LLMRequest(
            prompt=render_template(template, resolver),
            system=render_template(str(system_template), resolver) if system_template else None,
            role=str(inv.config.get("role", self.role)),
            subgoal_id=inv.subgoal_id,
            model=str(inv.config.get("model", self.model)),
            temperature=float(inv.config.get("temperature", self.temperature)),
            max_tokens=inv.config.get("max_tokens", self.max_tokens),
            seed=inv.seed,
        )

    async def invoke(self, inv: Invocation) -> InvocationResult:
        started = self._clock()
        try:
            request = self.build_request(inv)
        except ValueError as exc:
            return failure_result(FaultClass.CONFIGURATION, str(exc))

        try:
            response = await self.client.complete(request)
        except LLMError as exc:
            return failure_result(
                exc.fault_class,
                str(exc),
                cost=CostProfile(latency_s=self._clock() - started),
            )
        except Exception as exc:  # noqa: BLE001
            return failure_result(
                FaultClass.TOOL_FAILURE,
                f"{type(exc).__name__}: {exc}",
                cost=CostProfile(latency_s=self._clock() - started),
            )

        cost = CostProfile(
            latency_s=self._clock() - started, tokens=response.tokens, usd=response.usd
        )
        output_format = str(inv.config.get("output_format", self.output_format))
        if output_format == "json":
            try:
                payload: Any = json.loads(response.text)
            except json.JSONDecodeError as exc:
                return InvocationResult(
                    ok=False,
                    cost=cost,
                    logs=response.text,
                    errors=[
                        f"[{FaultClass.ARTIFACT_CONTRACT.value}] component '{self.name}' was "
                        f"asked for JSON and returned prose: {exc}"
                    ],
                    exit_code=1,
                )
        else:
            payload = response.text

        artifact = draft_artifact(type_name=self.output_type, payload=payload)
        finalized = finalize_outputs(
            {self.output_type: artifact},
            observers=self._facet_observers,
            declared_facets=self._output_facets,
            producer=inv.producer_id,
            derived_from=input_ids(inv),
        )
        errors: list[str] = []
        if response.finish_reason not in ("stop", "end_turn", ""):
            # Truncation is a real failure with a plausible-looking output —
            # exactly the shape that gets mistaken for success downstream.
            errors.append(
                f"[{FaultClass.CONFIGURATION.value}] completion ended early "
                f"(finish_reason={response.finish_reason}); output may be truncated"
            )
        return InvocationResult(
            ok=not errors,
            outputs=finalized.outputs,
            cost=cost,
            logs="\n".join(finalized.notes),
            errors=errors,
            exit_code=0 if not errors else 1,
            observed_facets=finalized.observed_facets,
        )


__all__ = [
    "LLMRequest",
    "LLMResponse",
    "LLMError",
    "LLMClient",
    "MockLLM",
    "OpenAIClient",
    "LLMAdapter",
    "estimate_tokens",
]
