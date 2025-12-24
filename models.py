from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any

from utils import estimate_tokens


@dataclass
class ModelOutput:
    text: str
    tokens_in: int
    tokens_out: int
    latency_s: float
    model_name: str
    cost_usd: float = 0.0


class BaseModelBackend:
    def __init__(self, name: str = "mock", cache_enabled: bool = True) -> None:
        self.name = name
        self.cache_enabled = cache_enabled
        self._cache: dict[tuple[str, str | None], ModelOutput] = {}

    def generate(self, prompt: str, system: str | None = None, **kwargs: Any) -> ModelOutput:
        cache_key = (prompt, system)
        if self.cache_enabled and cache_key in self._cache:
            return self._cache[cache_key]

        start = perf_counter()
        text = self._generate(prompt, system=system, **kwargs)
        latency = perf_counter() - start

        tokens_in = estimate_tokens(prompt + (system or ""))
        tokens_out = estimate_tokens(text)
        output = ModelOutput(
            text=text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_s=latency,
            model_name=self.name,
        )
        if self.cache_enabled:
            self._cache[cache_key] = output
        return output

    def _generate(self, prompt: str, system: str | None = None, **kwargs: Any) -> str:
        raise NotImplementedError


class MockModelBackend(BaseModelBackend):
    def __init__(self, name: str = "mock", cache_enabled: bool = True) -> None:
        super().__init__(name=name, cache_enabled=cache_enabled)

    def _generate(self, prompt: str, system: str | None = None, **kwargs: Any) -> str:
        snippet = prompt.strip().split("\n")[-1][:200]
        return f"[{self.name}] {snippet}"


class OpenAIModelBackend(BaseModelBackend):
    def __init__(self, name: str, model: str, api_key: str | None = None, cache_enabled: bool = True) -> None:
        super().__init__(name=name, cache_enabled=cache_enabled)
        self.model = model
        self.api_key = api_key

    def _generate(self, prompt: str, system: str | None = None, **kwargs: Any) -> str:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("OpenAI SDK is not installed. Install openai to use this backend.") from exc

        client = OpenAI(api_key=self.api_key)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = client.chat.completions.create(model=self.model, messages=messages, **kwargs)
        return response.choices[0].message.content or ""


def get_model_backend(name: str, model: str | None = None, api_key: str | None = None) -> BaseModelBackend:
    if name == "mock":
        return MockModelBackend(name="mock")
    if name == "openai":
        if not model:
            raise ValueError("OpenAI backend requires --model_name.")
        return OpenAIModelBackend(name="openai", model=model, api_key=api_key)
    raise ValueError(f"Unknown model backend: {name}")
