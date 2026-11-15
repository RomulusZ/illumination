"""OpenAI-compatible adapter tests over an in-memory transport."""

import httpx
import pytest

from illumination.repo.errors import LLMAdapterError
from illumination.repo.llm_adapter import OpenAICompatibleLLM
from illumination.types import LLMRequest


def _payload(content: str = '{"ok": true}') -> dict[str, object]:
    return {
        "model": "test-model",
        "choices": [
            {
                "message": {"content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def _adapter(
    transport: httpx.AsyncBaseTransport,
    *,
    max_attempts: int = 1,
) -> OpenAICompatibleLLM:
    return OpenAICompatibleLLM(
        base_url="https://example.test/v1",
        api_key="sk-test",
        model="test-model",
        max_attempts=max_attempts,
        retry_wait_seconds=0,
        transport=transport,
    )


async def test_complete_maps_response() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=_payload()))
    response = await _adapter(transport).complete(LLMRequest.from_user("hi"))
    assert response.text == '{"ok": true}'
    assert response.model == "test-model"
    assert response.usage.input_tokens == 10
    assert response.usage.output_tokens == 5


async def test_rate_limit_is_retried() -> None:
    statuses = [429, 200]
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        status = statuses.pop(0)
        if status == 429:
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(200, json=_payload())

    response = await _adapter(httpx.MockTransport(handler), max_attempts=2).complete(
        LLMRequest.from_user("hi")
    )
    assert response.text == '{"ok": true}'
    assert len(calls) == 2


async def test_client_error_is_not_retried() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(400, json={"error": "bad request"})

    with pytest.raises(LLMAdapterError):
        await _adapter(httpx.MockTransport(handler), max_attempts=3).complete(
            LLMRequest.from_user("hi")
        )
    assert len(calls) == 1


async def test_invalid_payload_is_rejected() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": []}))
    with pytest.raises(LLMAdapterError):
        await _adapter(transport).complete(LLMRequest.from_user("hi"))


def test_missing_api_key_fails_at_construction() -> None:
    with pytest.raises(LLMAdapterError):
        OpenAICompatibleLLM(base_url="https://example.test/v1", api_key="", model="test-model")
