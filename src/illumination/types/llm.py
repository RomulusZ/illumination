"""LLM request and response contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from illumination.types.base import IlluminationModel


class LLMRole(StrEnum):
    """Roles accepted by the LLM port."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class LLMFinishReason(StrEnum):
    """Normalized finish reasons returned by adapters."""

    STOP = "stop"
    LENGTH = "length"
    TOOL_CALLS = "tool_calls"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"


class LLMMessage(IlluminationModel):
    """One message in a model call."""

    role: LLMRole
    content: str = Field(min_length=1)

    @field_validator("content")
    @classmethod
    def _content_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("消息内容不能为空")
        return value


class LLMRequest(IlluminationModel):
    """A deterministic request to an LLM adapter."""

    messages: tuple[LLMMessage, ...] = Field(min_length=1)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    response_schema: dict[str, Any] | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _metadata_keys_are_not_blank(self) -> LLMRequest:
        if any(not key.strip() for key in self.metadata):
            raise ValueError("metadata 的 key 不能为空")
        return self

    @classmethod
    def from_user(cls, content: str, **kwargs: Any) -> LLMRequest:
        """Build a request with one user message."""
        return cls(messages=(LLMMessage(role=LLMRole.USER, content=content),), **kwargs)


class LLMUsage(IlluminationModel):
    """Token usage reported by the adapter."""

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cached_tokens: int = Field(default=0, ge=0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class LLMResponse(IlluminationModel):
    """A normalized response from an LLM adapter."""

    text: str = Field(min_length=1)
    model: str = Field(min_length=1)
    finish_reason: LLMFinishReason = LLMFinishReason.STOP
    usage: LLMUsage

    @field_validator("text", "model")
    @classmethod
    def _text_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("字段不能为空")
        return value
