"""Structured output helper tests."""

import pytest

from illumination.service.errors import StructuredOutputError
from illumination.service.llm_structured import complete_structured, parse_structured_output
from illumination.types import LLMRequest, RunPlan
from tests.fakes import FakeLLM


def test_invalid_json_is_reported() -> None:
    with pytest.raises(StructuredOutputError) as exc_info:
        parse_structured_output("not json", RunPlan)
    assert exc_info.value.reason == "invalid_json"


def test_non_object_payload_is_reported() -> None:
    with pytest.raises(StructuredOutputError) as exc_info:
        parse_structured_output("[]", RunPlan)
    assert exc_info.value.reason == "schema_mismatch"


def test_schema_mismatch_is_reported() -> None:
    with pytest.raises(StructuredOutputError) as exc_info:
        parse_structured_output('{"objective": "x"}', RunPlan)
    assert exc_info.value.reason == "schema_mismatch"


async def test_complete_structured_returns_validated_model() -> None:
    llm = FakeLLM(
        ['{"objective": "demo", "steps": [{"step_id": "s1", "action": "a", "rationale": "r"}]}']
    )
    plan = await complete_structured(llm, LLMRequest.from_user("plan"), RunPlan)
    assert plan.steps[0].step_id == "s1"
    assert llm.requests[0].response_schema is not None
