"""Structured LLM output parsing and invocation helpers."""

from __future__ import annotations

import json

from pydantic import BaseModel, ValidationError

from illumination.service.errors import StructuredOutputError
from illumination.types.llm import LLMRequest
from illumination.types.ports import LLMPort


async def complete_structured[ResponseModel: BaseModel](
    port: LLMPort,
    request: LLMRequest,
    response_model: type[ResponseModel],
) -> ResponseModel:
    """Call the port with a JSON schema and validate the parsed response."""
    enriched = request.model_copy(update={"response_schema": response_model.model_json_schema()})
    response = await port.complete(enriched)
    return parse_structured_output(response.text, response_model)


def parse_structured_output[ResponseModel: BaseModel](
    text: str,
    response_model: type[ResponseModel],
) -> ResponseModel:
    """Parse JSON text and validate it against a Pydantic contract."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(
            "模型输出不是合法 JSON",
            reason="invalid_json",
        ) from exc

    if not isinstance(payload, dict):
        raise StructuredOutputError(
            "模型输出必须是 JSON 对象",
            reason="schema_mismatch",
        )

    try:
        return response_model.model_validate(payload)
    except ValidationError as exc:
        raise StructuredOutputError(
            "模型输出不符合响应契约",
            reason="schema_mismatch",
        ) from exc
