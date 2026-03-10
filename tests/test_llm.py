from __future__ import annotations

import io
import json
import urllib.error

from dynaforge.ir.schema import ModelProvider, ModelSpec
from dynaforge.runtime.llm import OpenAICompatibleLLMClient


class _FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_openai_client_retries_retryable_http_errors(monkeypatch) -> None:
    client = OpenAICompatibleLLMClient(timeout_s=5, max_retries=2, retry_backoff_s=0.01)
    model = ModelSpec(
        provider=ModelProvider.other,
        name="test-model",
        meta={"base_url": "http://localhost:1234", "allow_unauthenticated": True},
    )
    calls = {"count": 0}
    sleeps: list[float] = []

    def fake_urlopen(request, timeout):
        calls["count"] += 1
        if calls["count"] == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "Too Many Requests",
                hdrs={"Retry-After": "0"},
                fp=io.BytesIO(b'{"error":"rate_limited"}'),
            )
        return _FakeHTTPResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"output":{"result":"ok"},"confidence":0.9,"summary":"done"}'
                        }
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda delay: sleeps.append(delay))

    response = client.complete(
        model,
        [
            {"role": "system", "content": "Return JSON."},
            {"role": "user", "content": "Say ok."},
        ],
        response_format={"type": "json_object"},
    )

    assert calls["count"] == 2
    assert response["content"]
    assert sleeps == []
