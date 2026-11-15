"""OpenAI-compatible chat completion adapter."""

from __future__ import annotations

from typing import Any

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from illumination.repo.errors import LLMAdapterError
from illumination.types.llm import LLMFinishReason, LLMRequest, LLMResponse, LLMUsage

FINISH_REASONS: dict[str, LLMFinishReason] = {
    "stop": LLMFinishReason.STOP,
    "length": LLMFinishReason.LENGTH,
    "tool_calls": LLMFinishReason.TOOL_CALLS,
    "content_filter": LLMFinishReason.CONTENT_FILTER,
}


class _RetryableLLMError(Exception):
    """Transient transport or server failure."""


class OpenAICompatibleLLM:
    """LLMPort implementation for any OpenAI-compatible chat endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 30.0,
        max_attempts: int = 3,
        retry_wait_seconds: float = 0.5,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key.strip():
            raise LLMAdapterError("缺少 API key")
        if not base_url.strip() or not model.strip():
            raise LLMAdapterError("base_url 与 model 不能为空")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._retry_wait_seconds = retry_wait_seconds
        self._transport = transport

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Call the endpoint with bounded retries on transient failures."""
        retryer = AsyncRetrying(
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential(multiplier=self._retry_wait_seconds, min=0, max=8),
            retry=retry_if_exception_type(_RetryableLLMError),
            reraise=True,
        )
        try:
            async for attempt in retryer:
                with attempt:
                    return await self._post(request)
        except _RetryableLLMError as exc:
            raise LLMAdapterError(f"LLM 请求重试耗尽：{exc}") from exc
        raise LLMAdapterError("LLM 请求未产生响应")

    async def _post(self, request: LLMRequest) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": message.role.value, "content": message.content}
                for message in request.messages
            ],
            "temperature": request.temperature,
        }
        if request.max_output_tokens is not None:
            payload["max_tokens"] = request.max_output_tokens
        if request.response_schema is not None:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
        except httpx.TransportError as exc:
            raise _RetryableLLMError(str(exc)) from exc

        if response.status_code == 429 or response.status_code >= 500:
            raise _RetryableLLMError(f"HTTP {response.status_code}")
        if response.status_code >= 400:
            raise LLMAdapterError(f"LLM 返回 HTTP {response.status_code}")

        try:
            data = response.json()
            choice = data["choices"][0]
            content = choice["message"]["content"]
            usage = data.get("usage") or {}
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMAdapterError("LLM 响应结构无效") from exc

        if not isinstance(content, str) or not content.strip():
            raise LLMAdapterError("LLM 返回空内容")

        return LLMResponse(
            text=content,
            model=str(data.get("model") or self._model),
            finish_reason=FINISH_REASONS.get(
                str(choice.get("finish_reason")), LLMFinishReason.STOP
            ),
            usage=LLMUsage(
                input_tokens=int(usage.get("prompt_tokens", 0)),
                output_tokens=int(usage.get("completion_tokens", 0)),
                cached_tokens=int(
                    (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
                ),
            ),
        )
