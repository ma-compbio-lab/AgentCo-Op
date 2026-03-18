from __future__ import annotations

import io
import json
import urllib.error

from dynaforge.ir.schema import ModelProvider, ModelSpec
from dynaforge.runtime.llm import LLMRouter, OpenAICompatibleLLMClient


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


def test_openai_client_retries_transient_json_parse_400(monkeypatch) -> None:
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
                400,
                "Bad Request",
                hdrs={},
                fp=io.BytesIO(
                    b'{"error":{"message":"We could not parse the JSON body of your request.","type":"invalid_request_error"}}'
                ),
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
    assert sleeps == [0.01]


def test_openai_client_retries_gpt5_reasoning_saturation_with_minimal_effort(monkeypatch) -> None:
    client = OpenAICompatibleLLMClient(timeout_s=5, max_retries=0, retry_backoff_s=0.01)
    model = ModelSpec(
        provider=ModelProvider.other,
        name="gpt-5",
        max_output_tokens=256,
        meta={"base_url": "http://localhost:1234", "allow_unauthenticated": True, "reasoning_effort": "high"},
    )
    payloads: list[dict[str, object]] = []

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        payloads.append(payload)
        if len(payloads) == 1:
            return _FakeHTTPResponse(
                {
                    "choices": [
                        {
                            "message": {"content": ""},
                            "finish_reason": "length",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 256,
                        "completion_tokens_details": {"reasoning_tokens": 256},
                    },
                }
            )
        return _FakeHTTPResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"output":{"result":"ok"},"confidence":0.9,"summary":"done"}'
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    response = client.complete(
        model,
        [
            {"role": "system", "content": "Return JSON."},
            {"role": "user", "content": "Say ok."},
        ],
        response_format={"type": "json_object"},
    )

    assert len(payloads) == 2
    assert payloads[0]["reasoning_effort"] == "high"
    assert payloads[1]["reasoning_effort"] == "minimal"
    assert response["content"]
    assert response["raw"]["dynaforge_reasoning_retry"] == {"from": "high", "to": "minimal"}


def test_normalize_output_accepts_output_underscore_aliases() -> None:
    payload = {
        "output_review_verdict": "revise",
        "output_corrected_answer_label": "C",
        "output_corrected_answer_text": "Nitroglycerin",
        "confidence": 0.44,
        "summary": "reviewed",
    }

    normalized = LLMRouter._normalize_output(payload)

    assert normalized == {
        "review_verdict": "revise",
        "corrected_answer_label": "C",
        "corrected_answer_text": "Nitroglycerin",
    }


def test_normalize_output_accepts_nested_output_underscore_aliases() -> None:
    payload = {
        "output": {
            "output_review_verdict": "revise",
            "output_corrected_answer_label": "D",
            "output_corrected_answer_text": "JAK/STAT",
        },
        "confidence": 0.66,
    }

    normalized = LLMRouter._normalize_output(payload)

    assert normalized == {
        "review_verdict": "revise",
        "corrected_answer_label": "D",
        "corrected_answer_text": "JAK/STAT",
    }
