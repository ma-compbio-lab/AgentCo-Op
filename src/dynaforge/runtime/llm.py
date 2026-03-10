from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional

from dynaforge.ir.schema import FailureType, ModelProvider, ModelSpec, NodeSpec
from dynaforge.runtime.reports import ExecutionCost, NodeExecutionResult

if TYPE_CHECKING:
    from dynaforge.runtime.executor import NodeExecutionContext

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
        payload: Dict[str, Any] = {
            "model": model.name,
            "messages": messages,
        }
        if self._supports_temperature(model):
            payload["temperature"] = model.temperature
        if model.max_output_tokens is not None:
            payload[self._max_tokens_field(model)] = model.max_output_tokens
        reasoning_effort = model.meta.get("reasoning_effort")
        if isinstance(reasoning_effort, str) and reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
        if response_format is not None:
            payload["response_format"] = response_format

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

        request = urllib.request.Request(
            chat_url,
            data=json.dumps(payload, ensure_ascii=True).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        last_error: Optional[Exception] = None
        max_retries = self._max_retries(model)
        for attempt in range(max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                    raw_body = response.read().decode("utf-8")
                break
            except urllib.error.HTTPError as exc:
                last_error = exc
                body = exc.read().decode("utf-8", errors="replace")
                if attempt < max_retries and self._is_retryable_http_error(exc.code):
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

        return {
            "content": content,
            "usage": parsed.get("usage", {}),
            "raw": parsed,
        }

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
    def _is_retryable_http_error(status_code: int) -> bool:
        return status_code in {408, 409, 429, 500, 502, 503, 504}

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

        if node.tools:
            return self._run_tool_loop(node, inputs, context)
        return self._run_prompt_only(node, inputs, context)

    def _run_prompt_only(
        self,
        node: NodeSpec,
        inputs: JsonDict,
        context: "NodeExecutionContext",
    ) -> NodeExecutionResult:
        messages = self._base_messages(node, inputs)
        try:
            response = self.client.complete(
                node.model,
                messages,
                response_format={"type": "json_object"},
            )
            parsed = self._parse_payload(response["content"])
            output_payload = self._normalize_output(parsed)
            confidence = self._normalize_confidence(parsed.get("confidence"), default=0.75)
            return NodeExecutionResult(
                outputs=output_payload,
                trace={
                    "live_llm": True,
                    "provider": node.model.provider.value,
                    "model": node.model.name,
                    "summary": parsed.get("summary", ""),
                },
                cost=self._cost_from_usage(node.model, response.get("usage", {})),
                confidence=confidence,
            )
        except LLMClientError as exc:
            if not self.allow_offline_fallback:
                return NodeExecutionResult(
                    failure_type=FailureType.tool_runtime_error,
                    error=f"LLM backend error: {exc}",
                )
            return self._offline_fallback(node, inputs, reason=str(exc))

    def _run_tool_loop(
        self,
        node: NodeSpec,
        inputs: JsonDict,
        context: "NodeExecutionContext",
    ) -> NodeExecutionResult:
        tool_descriptors = context.mcp_client.describe_tools(
            node.tools,
            blueprint=context.blueprint,
            sandbox_runner=context.sandbox_runner,
        )
        history: List[Dict[str, Any]] = []
        aggregate_cost = ExecutionCost()
        tool_calls = 0

        for _ in range(node.max_steps):
            messages = self._tool_loop_messages(node, inputs, tool_descriptors, history)
            try:
                response = self.client.complete(
                    node.model,
                    messages,
                    response_format={"type": "json_object"},
                )
                aggregate_cost = aggregate_cost.add(self._cost_from_usage(node.model, response.get("usage", {})))
            except LLMClientError as exc:
                if not self.allow_offline_fallback:
                    return NodeExecutionResult(
                        failure_type=FailureType.tool_runtime_error,
                        error=f"LLM backend error: {exc}",
                    )
                return self._offline_fallback(node, inputs, reason=str(exc))

            decision = self._parse_payload(response["content"])
            action = decision.get("action", "final")
            if action == "call_tool":
                requested_tool = str(decision.get("tool", ""))
                arguments = decision.get("arguments", {})
                try:
                    tool_result = context.mcp_client.call_bound_tool(
                        node.tools,
                        requested_tool,
                        arguments if isinstance(arguments, dict) else {},
                        blueprint=context.blueprint,
                        sandbox_runner=context.sandbox_runner,
                    )
                except Exception as exc:
                    return NodeExecutionResult(
                        failure_type=FailureType.tool_runtime_error,
                        error=f"Tool call failed: {exc}",
                        trace={"live_llm": True, "tool_loop": history},
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
            confidence = self._normalize_confidence(decision.get("confidence"), default=0.7)
            return NodeExecutionResult(
                outputs=output_payload,
                trace={
                    "live_llm": True,
                    "provider": node.model.provider.value,
                    "model": node.model.name,
                    "tool_loop": history,
                    "summary": decision.get("summary", ""),
                },
                cost=aggregate_cost.add(ExecutionCost(tool_calls=tool_calls)),
                confidence=confidence,
            )

        return NodeExecutionResult(
            failure_type=FailureType.reasoning_inconsistency,
            error=f"Exceeded max_steps={node.max_steps} without a final answer",
            trace={"live_llm": True, "tool_loop": history},
            cost=aggregate_cost.add(ExecutionCost(tool_calls=tool_calls)),
        )

    def _base_messages(self, node: NodeSpec, inputs: JsonDict) -> List[Dict[str, str]]:
        system_prompt = node.system_prompt or (
            f"You are the {node.role} node in a DynaForge workflow. "
            "Respond with a JSON object containing keys: output (object), confidence (0..1), summary (string)."
        )
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
    ) -> List[Dict[str, str]]:
        system_prompt = node.system_prompt or (
            f"You are the {node.role} node in a DynaForge workflow. "
            "You may either call one allowed MCP tool or return a final answer. "
            "Always respond as JSON. Use action='call_tool' with keys tool and arguments, "
            "or action='final' with keys output, confidence, and summary."
        )
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
        if "output" in payload and isinstance(payload["output"], dict):
            return dict(payload["output"])
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
    def _offline_fallback(node: NodeSpec, inputs: JsonDict, *, reason: str) -> NodeExecutionResult:
        encoded = json.dumps(inputs, ensure_ascii=True, sort_keys=True)
        return NodeExecutionResult(
            outputs={"result": {"node_id": node.node_id, "role": node.role, "inputs": inputs}},
            trace={
                "offline_fallback": True,
                "provider": node.model.provider.value if node.model else "unknown",
                "model": node.model.name if node.model else "unknown",
                "reason": reason,
            },
            cost=ExecutionCost(
                input_tokens=max(len(encoded) // 4, 1) if inputs else 1,
                output_tokens=48,
            ),
            confidence=0.55,
        )
