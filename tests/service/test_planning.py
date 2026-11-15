"""Planning service tests."""

import json

import pytest

from illumination.service.errors import PlanningError
from illumination.service.planning import build_planning_request, plan_run
from illumination.types import RunRequest
from tests.fakes import FakeLLM


def _plan_payload(steps: int = 1) -> str:
    return json.dumps(
        {
            "objective": "demo",
            "steps": [
                {
                    "step_id": f"s{index}",
                    "action": "act",
                    "rationale": "why",
                }
                for index in range(1, steps + 1)
            ],
            "assumptions": [],
        }
    )


def test_planning_request_carries_budget_metadata() -> None:
    request = build_planning_request(RunRequest(objective="demo", max_steps=3))
    assert request.metadata["max_steps"] == "3"
    assert request.metadata["objective"] == "demo"


async def test_plan_run_returns_plan() -> None:
    llm = FakeLLM([_plan_payload()])
    plan = await plan_run(llm, RunRequest(objective="demo"))
    assert plan.objective == "demo"
    assert [step.step_id for step in plan.steps] == ["s1"]


async def test_plan_run_rejects_step_budget_overflow() -> None:
    llm = FakeLLM([_plan_payload(steps=3)])
    with pytest.raises(PlanningError):
        await plan_run(llm, RunRequest(objective="demo", max_steps=2))
