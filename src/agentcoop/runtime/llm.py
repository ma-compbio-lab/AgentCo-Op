from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional

from agentcoop.integrations.tool_registry import ToolCandidate
from agentcoop.ir.schema import FailureType, ModelProvider, ModelSpec, NodeSpec
from agentcoop.runtime.reports import ExecutionCost, NodeExecutionResult
from agentcoop.runtime.skills import SkillPromptBundle

if TYPE_CHECKING:
    from agentcoop.runtime.execution_context import NodeExecutionContext

JsonDict = Dict[str, Any]


class LLMClientError(RuntimeError):
    """Raised when the configured live LLM backend cannot be reached or parsed."""


class OpenAICompatibleLLMClient:
    """Minimal OpenAI-compatible chat completions client using the stdlib HTTP stack."""

    def __init__(self, timeout_s: int = 120, *, max_retries: int = 3, retry_backoff_s: float = 2.0):
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.retry_backoff_s = retry_backoff_s

    def complete(
        self,
        model: ModelSpec,
        messages: List[Dict[str, str]],
        *,
        response_format: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        chat_url = self._chat_url(model)
        api_key = self._api_key(model)
        payload = self._build_payload(model, messages, response_format=response_format)

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._requires_api_key(model) and not api_key:
            raise LLMClientError(
                f"Missing API key for provider '{model.provider.value}'. "
                "Set the provider key env var or use a local OpenAI-compatible base_url with allow_unauthenticated=true."
            )
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        parsed = self._perform_request(chat_url, headers, payload, model=model)
        retried_reasoning = False
        if self._should_retry_with_minimal_reasoning(model, parsed, payload):
            retry_payload = self._build_payload(
                model,
                messages,
                response_format=response_format,
                reasoning_effort_override="minimal",
            )
            parsed = self._perform_request(chat_url, headers, retry_payload, model=model)
            retried_reasoning = True

        content = self._extract_message_content(parsed)
        response = {
            "content": content,
            "usage": parsed.get("usage", {}),
            "raw": parsed,
        }
        if retried_reasoning:
            response["raw"] = {
                **parsed,
                "agentcoop_reasoning_retry": {
                    "from": payload.get("reasoning_effort"),
                    "to": "minimal",
                },
            }
        return response

    def _perform_request(
        self,
        chat_url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        *,
        model: ModelSpec,
    ) -> Dict[str, Any]:
        last_error: Optional[Exception] = None
        max_retries = self._max_retries(model)
        for attempt in range(max_retries + 1):
            request = urllib.request.Request(
                chat_url,
                data=json.dumps(payload, ensure_ascii=True).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                    raw_body = response.read().decode("utf-8")
                break
            except urllib.error.HTTPError as exc:
                last_error = exc
                body = exc.read().decode("utf-8", errors="replace")
                if attempt < max_retries and self._is_retryable_http_error(exc.code, body):
                    self._sleep_before_retry(model, attempt, retry_after=self._parse_retry_after(exc))
                    continue
                raise LLMClientError(f"HTTP {exc.code}: {body}") from exc
            except urllib.error.URLError as exc:
                last_error = exc
                if attempt < max_retries:
                    self._sleep_before_retry(model, attempt)
                    continue
                raise LLMClientError(str(exc.reason)) from exc
        else:
            if isinstance(last_error, urllib.error.HTTPError):
                body = last_error.read().decode("utf-8", errors="replace")
                raise LLMClientError(f"HTTP {last_error.code}: {body}") from last_error
            if isinstance(last_error, urllib.error.URLError):
                raise LLMClientError(str(last_error.reason)) from last_error
            raise LLMClientError("LLM backend request failed before a response was received")

        try:
            parsed = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise LLMClientError("LLM backend returned non-JSON response") from exc
        return parsed

    @staticmethod
    def _extract_message_content(parsed: Mapping[str, Any]) -> str:
        try:
            message = parsed["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMClientError("LLM backend response is missing choices[0].message") from exc

        content = message.get("content", "")
        if isinstance(content, list):
            text_parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        text_parts.append(item.get("text", ""))
                    elif "text" in item:
                        text_parts.append(str(item["text"]))
                    else:
                        text_parts.append(json.dumps(item, ensure_ascii=True))
                else:
                    text_parts.append(str(item))
            content = "\n".join(part for part in text_parts if part)
        return str(content or "")

    @staticmethod
    def _build_payload(
        model: ModelSpec,
        messages: List[Dict[str, str]],
        *,
        response_format: Optional[Dict[str, Any]] = None,
        reasoning_effort_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": model.name,
            "messages": messages,
        }
        if OpenAICompatibleLLMClient._supports_temperature(model):
            payload["temperature"] = model.temperature
        if model.max_output_tokens is not None:
            payload[OpenAICompatibleLLMClient._max_tokens_field(model)] = model.max_output_tokens
        reasoning_effort = reasoning_effort_override
        if reasoning_effort is None:
            configured_reasoning = model.meta.get("reasoning_effort")
            if isinstance(configured_reasoning, str) and configured_reasoning:
                reasoning_effort = configured_reasoning
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
        if response_format is not None:
            payload["response_format"] = response_format
        return payload

    @staticmethod
    def _should_retry_with_minimal_reasoning(
        model: ModelSpec,
        parsed: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> bool:
        if not model.name.strip().lower().startswith("gpt-5"):
            return False
        current_effort = str(payload.get("reasoning_effort", "")).strip().lower()
        if current_effort in {"", "minimal"}:
            return False
        try:
            choice = parsed["choices"][0]
            finish_reason = str(choice.get("finish_reason", "")).strip().lower()
        except (KeyError, IndexError, TypeError):
            return False
        if finish_reason != "length":
            return False
        content = OpenAICompatibleLLMClient._extract_message_content(parsed)
        if content.strip():
            return False
        usage = parsed.get("usage", {})
        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        reasoning_tokens = int(
            ((usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)) or 0
        )
        return completion_tokens > 0 and reasoning_tokens >= completion_tokens

    @staticmethod
    def _max_tokens_field(model: ModelSpec) -> str:
        model_name = model.name.strip().lower()
        if model_name.startswith("gpt-5"):
            return "max_completion_tokens"
        return "max_tokens"

    @staticmethod
    def _supports_temperature(model: ModelSpec) -> bool:
        model_name = model.name.strip().lower()
        return not model_name.startswith("gpt-5")

    @staticmethod
    def _chat_url(model: ModelSpec) -> str:
        meta_base = str(model.meta.get("base_url", "")).strip()
        if model.provider == ModelProvider.openai:
            base_url = meta_base or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        elif model.provider in {ModelProvider.local, ModelProvider.other}:
            base_url = meta_base or os.environ.get("OPENAI_BASE_URL", "")
            if not base_url:
                raise LLMClientError("Local/other providers require model.meta.base_url or OPENAI_BASE_URL")
        else:
            base_url = meta_base or os.environ.get("OPENAI_BASE_URL", "")
            if not base_url:
                raise LLMClientError(
                    f"Provider '{model.provider.value}' requires an OpenAI-compatible base_url in model.meta"
                )

        if base_url.endswith("/chat/completions"):
            return base_url
        return f"{base_url.rstrip('/')}/chat/completions"

    @staticmethod
    def _api_key(model: ModelSpec) -> str:
        env_name = model.meta.get("api_key_env")
        if isinstance(env_name, str) and env_name:
            return os.environ.get(env_name, "")
        if model.provider == ModelProvider.openai:
            return os.environ.get("OPENAI_API_KEY", "")
        if model.provider == ModelProvider.deepseek:
            return os.environ.get("DEEPSEEK_API_KEY", "")
        if model.provider == ModelProvider.google:
            return os.environ.get("GOOGLE_API_KEY", "")
        if model.provider == ModelProvider.anthropic:
            return os.environ.get("ANTHROPIC_API_KEY", "")
        return ""

    @staticmethod
    def _requires_api_key(model: ModelSpec) -> bool:
        if bool(model.meta.get("allow_unauthenticated")):
            return False
        return model.provider in {
            ModelProvider.openai,
            ModelProvider.anthropic,
            ModelProvider.google,
            ModelProvider.deepseek,
        }

    def _max_retries(self, model: ModelSpec) -> int:
        value = model.meta.get("api_max_retries")
        if value is None:
            return self.max_retries
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return self.max_retries

    def _retry_backoff(self, model: ModelSpec, attempt: int) -> float:
        value = model.meta.get("retry_backoff_s")
        try:
            base = float(value) if value is not None else float(self.retry_backoff_s)
        except (TypeError, ValueError):
            base = float(self.retry_backoff_s)
        return max(0.0, base * (2**attempt))

    def _sleep_before_retry(self, model: ModelSpec, attempt: int, *, retry_after: Optional[float] = None) -> None:
        delay = retry_after if retry_after is not None else self._retry_backoff(model, attempt)
        if delay > 0:
            time.sleep(delay)

    @staticmethod
    def _is_retryable_http_error(status_code: int, body: str = "") -> bool:
        if status_code in {408, 409, 429, 500, 502, 503, 504}:
            return True
        if status_code == 400:
            normalized = body.lower()
            if "could not parse the json body of your request" in normalized:
                return True
        return False

    @staticmethod
    def _parse_retry_after(exc: urllib.error.HTTPError) -> Optional[float]:
        retry_after = exc.headers.get("Retry-After")
        if retry_after is None:
            return None
        try:
            return max(0.0, float(retry_after))
        except (TypeError, ValueError):
            return None


class LLMRouter:
    """Routes agent/evaluator/router nodes through a live LLM backend."""

    def __init__(
        self,
        client: Optional[OpenAICompatibleLLMClient] = None,
        *,
        allow_offline_fallback: bool = True,
    ):
        self.client = client or OpenAICompatibleLLMClient()
        self.allow_offline_fallback = allow_offline_fallback

    def run_node(self, node: NodeSpec, inputs: JsonDict, context: "NodeExecutionContext") -> NodeExecutionResult:
        if node.model is None:
            return NodeExecutionResult(
                failure_type=FailureType.unknown,
                error=f"Node {node.node_id} is missing model configuration",
            )

        skill_bundle = context.skill_registry.render_prompt_for_node(node)
        if skill_bundle.missing_required:
            trace = {"live_llm": False}
            if skill_bundle.has_trace():
                trace["skills"] = skill_bundle.trace_payload()
            return NodeExecutionResult(
                failure_type=FailureType.unknown,
                error="Required skills could not be resolved: " + "; ".join(skill_bundle.missing_required),
                trace=trace,
            )

        tool_candidates: List[ToolCandidate] = []
        discovery_trace: Optional[JsonDict] = None
        if node.tools or node.tool_discovery is not None:
            tool_candidates, discovery_trace = self._resolve_tool_candidates(node, inputs, context, skill_bundle=skill_bundle)
        if tool_candidates:
            return self._run_tool_loop(
                node,
                inputs,
                context,
                tool_candidates=tool_candidates,
                discovery_trace=discovery_trace or {},
                skill_bundle=skill_bundle,
            )
        return self._run_prompt_only(node, inputs, context, skill_bundle=skill_bundle, discovery_trace=discovery_trace)

    def _run_prompt_only(
        self,
        node: NodeSpec,
        inputs: JsonDict,
        context: "NodeExecutionContext",
        *,
        skill_bundle: SkillPromptBundle,
        discovery_trace: Optional[JsonDict],
    ) -> NodeExecutionResult:
        messages = self._base_messages(node, inputs, skill_bundle=skill_bundle)
        try:
            response = self.client.complete(
                node.model,
                messages,
                response_format={"type": "json_object"},
            )
            parsed = self._parse_payload(response["content"])
            output_payload = self._normalize_output(parsed)
            confidence = self._extract_confidence(parsed, default=0.75)
            trace = {
                "live_llm": True,
                "provider": node.model.provider.value,
                "model": node.model.name,
                "summary": self._extract_summary(parsed),
            }
            if discovery_trace is not None:
                trace["tool_discovery"] = discovery_trace
            if skill_bundle.has_trace():
                trace["skills"] = skill_bundle.trace_payload()
            return NodeExecutionResult(
                outputs=output_payload,
                trace=trace,
                cost=self._cost_from_usage(node.model, response.get("usage", {})),
                confidence=confidence,
            )
        except LLMClientError as exc:
            if not self.allow_offline_fallback:
                trace = {}
                if discovery_trace is not None:
                    trace["tool_discovery"] = discovery_trace
                if skill_bundle.has_trace():
                    trace["skills"] = skill_bundle.trace_payload()
                return NodeExecutionResult(
                    failure_type=FailureType.tool_runtime_error,
                    error=f"LLM backend error: {exc}",
                    trace=trace,
                )
            return self._offline_fallback(
                node,
                inputs,
                reason=str(exc),
                skill_bundle=skill_bundle,
                discovery_trace=discovery_trace,
            )

    def _run_tool_loop(
        self,
        node: NodeSpec,
        inputs: JsonDict,
        context: "NodeExecutionContext",
        *,
        tool_candidates: List[ToolCandidate],
        discovery_trace: JsonDict,
        skill_bundle: SkillPromptBundle,
    ) -> NodeExecutionResult:
        tool_refs = [candidate.ref for candidate in tool_candidates]
        tool_descriptors = [dict(candidate.descriptor) for candidate in tool_candidates]
        history: List[Dict[str, Any]] = []
        aggregate_cost = ExecutionCost()
        tool_calls = 0

        for _ in range(node.max_steps):
            messages = self._tool_loop_messages(node, inputs, tool_descriptors, history, skill_bundle=skill_bundle)
            try:
                response = self.client.complete(
                    node.model,
                    messages,
                    response_format={"type": "json_object"},
                )
                aggregate_cost = aggregate_cost.add(self._cost_from_usage(node.model, response.get("usage", {})))
            except LLMClientError as exc:
                if not self.allow_offline_fallback:
                    trace = {"tool_discovery": discovery_trace}
                    if skill_bundle.has_trace():
                        trace["skills"] = skill_bundle.trace_payload()
                    return NodeExecutionResult(
                        failure_type=FailureType.tool_runtime_error,
                        error=f"LLM backend error: {exc}",
                        trace=trace,
                    )
                return self._offline_fallback(
                    node,
                    inputs,
                    reason=str(exc),
                    skill_bundle=skill_bundle,
                    discovery_trace=discovery_trace,
                )

            decision = self._parse_payload(response["content"])
            action = decision.get("action", "final")
            if action == "call_tool":
                requested_tool = str(decision.get("tool", ""))
                arguments = decision.get("arguments", {})
                try:
                    tool_result = context.mcp_client.call_bound_tool(
                        tool_refs,
                        requested_tool,
                        arguments if isinstance(arguments, dict) else {},
                        blueprint=context.blueprint,
                        sandbox_runner=context.sandbox_runner,
                    )
                except Exception as exc:
                    trace = {"live_llm": True, "tool_loop": history, "tool_discovery": discovery_trace}
                    if skill_bundle.has_trace():
                        trace["skills"] = skill_bundle.trace_payload()
                    return NodeExecutionResult(
                        failure_type=FailureType.tool_runtime_error,
                        error=f"Tool call failed: {exc}",
                        trace=trace,
                        cost=aggregate_cost.add(ExecutionCost(tool_calls=tool_calls)),
                    )
                tool_calls += 1
                history.append(
                    {
                        "action": "call_tool",
                        "tool": requested_tool,
                        "arguments": arguments,
                        "result": tool_result,
                    }
                )
                continue

            output_payload = self._normalize_output(decision)
            confidence = self._extract_confidence(decision, default=0.7)
            trace = {
                "live_llm": True,
                "provider": node.model.provider.value,
                "model": node.model.name,
                "tool_loop": history,
                "tool_discovery": discovery_trace,
                "summary": self._extract_summary(decision),
            }
            if skill_bundle.has_trace():
                trace["skills"] = skill_bundle.trace_payload()
            return NodeExecutionResult(
                outputs=output_payload,
                trace=trace,
                cost=aggregate_cost.add(ExecutionCost(tool_calls=tool_calls)),
                confidence=confidence,
            )

        trace = {"live_llm": True, "tool_loop": history, "tool_discovery": discovery_trace}
        if skill_bundle.has_trace():
            trace["skills"] = skill_bundle.trace_payload()
        return NodeExecutionResult(
            failure_type=FailureType.reasoning_inconsistency,
            error=f"Exceeded max_steps={node.max_steps} without a final answer",
            trace=trace,
            cost=aggregate_cost.add(ExecutionCost(tool_calls=tool_calls)),
        )

    def _resolve_tool_candidates(
        self,
        node: NodeSpec,
        inputs: JsonDict,
        context: "NodeExecutionContext",
        *,
        skill_bundle: SkillPromptBundle,
    ) -> tuple[List[ToolCandidate], JsonDict]:
        discovery = node.tool_discovery
        if discovery is None or discovery.mode.value == "disabled":
            candidates = context.tool_registry.describe_bound_tools(
                node,
                blueprint=context.blueprint,
                sandbox_runner=context.sandbox_runner,
            )
            filtered_candidates, policy_trace = skill_bundle.apply_tool_policy(candidates)
            return filtered_candidates, {
                "enabled": False,
                "candidate_count": len(candidates),
                "skill_tool_policy": policy_trace,
                "selected_candidates": [
                    {"server": candidate.ref.server, "tool": candidate.ref.tool, "source": candidate.source}
                    for candidate in filtered_candidates
                ],
            }

        discovered = context.tool_registry.discover_for_node(
            node,
            blueprint=context.blueprint,
            sandbox_runner=context.sandbox_runner,
        )
        filtered_candidates, policy_trace = skill_bundle.apply_tool_policy(discovered)
        selected, scout_summary = context.tool_scout.select_candidates(
            node,
            inputs,
            filtered_candidates,
            discovery=discovery,
            skill_text=skill_bundle.prompt_text,
        )
        return selected, {
            "enabled": True,
            "mode": discovery.mode.value,
            "candidate_count": len(discovered),
            "post_policy_candidate_count": len(filtered_candidates),
            "skill_tool_policy": policy_trace,
            **scout_summary,
        }

    def _base_messages(self, node: NodeSpec, inputs: JsonDict, *, skill_bundle: SkillPromptBundle) -> List[Dict[str, str]]:
        base_system_prompt = node.system_prompt or (
            f"You are the {node.role} node in a DynaForge workflow. "
            "Respond with a JSON object containing keys: output (object), confidence (0..1), summary (string)."
        )
        system_prompt = self._compose_system_prompt(base_system_prompt, skill_bundle)
        user_prompt = (
            f"Node ID: {node.node_id}\n"
            f"Role: {node.role}\n"
            f"Description: {node.description or 'n/a'}\n"
            f"Inputs JSON:\n{json.dumps(inputs, ensure_ascii=True, indent=2, sort_keys=True)}"
        )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def _tool_loop_messages(
        self,
        node: NodeSpec,
        inputs: JsonDict,
        tool_descriptors: List[Dict[str, Any]],
        history: List[Dict[str, Any]],
        *,
        skill_bundle: SkillPromptBundle,
    ) -> List[Dict[str, str]]:
        base_system_prompt = node.system_prompt or (
            f"You are the {node.role} node in a DynaForge workflow. "
            "You may either call one allowed MCP tool or return a final answer. "
            "Always respond as JSON. Use action='call_tool' with keys tool and arguments, "
            "or action='final' with keys output, confidence, and summary."
        )
        system_prompt = self._compose_system_prompt(base_system_prompt, skill_bundle)
        user_payload = {
            "node_id": node.node_id,
            "role": node.role,
            "description": node.description,
            "inputs": inputs,
            "tools": tool_descriptors,
            "history": history,
        }
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True, indent=2, sort_keys=True)},
        ]

    @staticmethod
    def _compose_system_prompt(base_system_prompt: str, skill_bundle: SkillPromptBundle) -> str:
        if not skill_bundle.prompt_text:
            return base_system_prompt
        return f"{base_system_prompt}\n\n{skill_bundle.prompt_text}"

    @staticmethod
    def _parse_payload(raw_content: str) -> Dict[str, Any]:
        try:
            parsed = json.loads(raw_content)
        except json.JSONDecodeError:
            parsed = {"output": {"text": raw_content}, "confidence": 0.5, "summary": "Non-JSON model response"}
        if not isinstance(parsed, dict):
            return {"output": {"value": parsed}, "confidence": 0.5, "summary": "Non-object JSON model response"}
        return parsed

    @staticmethod
    def _normalize_output(payload: Mapping[str, Any]) -> Dict[str, Any]:
        def normalize_aliases(mapping: Mapping[str, Any]) -> Dict[str, Any]:
            if any(isinstance(key, str) and key.startswith("output.") for key in mapping):
                return {
                    key.split(".", 1)[1] if isinstance(key, str) and key.startswith("output.") else key: value
                    for key, value in mapping.items()
                }
            if any(isinstance(key, str) and key.startswith("output_") for key in mapping):
                return {
                    key.split("output_", 1)[1] if isinstance(key, str) and key.startswith("output_") else key: value
                    for key, value in mapping.items()
                }
            return dict(mapping)

        if "output" in payload and isinstance(payload["output"], dict):
            return normalize_aliases(payload["output"])
        meta_keys = {"confidence", "summary", "action", "tool", "arguments"}
        dotted_output = {
            key.split(".", 1)[1]: value
            for key, value in payload.items()
            if isinstance(key, str) and key.startswith("output.")
        }
        if dotted_output:
            passthrough = {
                key: value
                for key, value in payload.items()
                if key not in meta_keys and not (isinstance(key, str) and key.startswith("output."))
            }
            return {**dotted_output, **passthrough}
        underscored_output = {
            key.split("output_", 1)[1]: value
            for key, value in payload.items()
            if isinstance(key, str) and key.startswith("output_") and len(key) > len("output_")
        }
        if underscored_output:
            passthrough = {
                key: value
                for key, value in payload.items()
                if key not in meta_keys and not (isinstance(key, str) and key.startswith("output_"))
            }
            return {**underscored_output, **passthrough}
        if "result" in payload and isinstance(payload["result"], dict):
            return {"result": dict(payload["result"])}
        direct_output = {key: value for key, value in payload.items() if key not in meta_keys}
        if direct_output:
            return direct_output
        return {"result": dict(payload)}

    @staticmethod
    def _normalize_confidence(raw_value: Any, *, default: float) -> float:
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            return default
        return max(0.0, min(1.0, value))

    @classmethod
    def _extract_confidence(cls, payload: Mapping[str, Any], *, default: float) -> float:
        if "confidence" in payload:
            return cls._normalize_confidence(payload.get("confidence"), default=default)
        output = payload.get("output")
        if isinstance(output, Mapping) and "confidence" in output:
            return cls._normalize_confidence(output.get("confidence"), default=default)
        return default

    @staticmethod
    def _extract_summary(payload: Mapping[str, Any]) -> str:
        summary = payload.get("summary")
        if isinstance(summary, str):
            return summary
        output = payload.get("output")
        if isinstance(output, Mapping):
            nested = output.get("summary")
            if isinstance(nested, str):
                return nested
        return ""

    @staticmethod
    def _cost_from_usage(model: ModelSpec, usage: Mapping[str, Any]) -> ExecutionCost:
        input_tokens = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
        output_tokens = int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
        usd = 0.0
        if model.usd_per_1k_input is not None:
            usd += (input_tokens / 1000.0) * model.usd_per_1k_input
        if model.usd_per_1k_output is not None:
            usd += (output_tokens / 1000.0) * model.usd_per_1k_output
        return ExecutionCost(
            usd=round(usd, 6),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    @staticmethod
    def _offline_fallback(
        node: NodeSpec,
        inputs: JsonDict,
        *,
        reason: str,
        skill_bundle: SkillPromptBundle,
        discovery_trace: Optional[JsonDict],
    ) -> NodeExecutionResult:
        encoded = json.dumps(inputs, ensure_ascii=True, sort_keys=True)
        trace: JsonDict = {
            "offline_fallback": True,
            "provider": node.model.provider.value if node.model else "unknown",
            "model": node.model.name if node.model else "unknown",
            "reason": reason,
        }
        if discovery_trace is not None:
            trace["tool_discovery"] = discovery_trace
        if skill_bundle.has_trace():
            trace["skills"] = skill_bundle.trace_payload()
        return NodeExecutionResult(
            outputs={"result": {"node_id": node.node_id, "role": node.role, "inputs": inputs}},
            trace=trace,
            cost=ExecutionCost(
                input_tokens=max(len(encoded) // 4, 1) if inputs else 1,
                output_tokens=48,
            ),
            confidence=0.55,
        )
