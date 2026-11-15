"""Runtime graph tests."""

import json

from illumination.runtime.pipeline import build_run_id, run_once
from illumination.types import RunRequest, RunStatus, StepStatus
from tests.fakes import FakeClock, FakeLLM, InMemoryArtifactStore


def _plan_payload() -> str:
    return json.dumps(
        {
            "objective": "demo",
            "steps": [
                {"step_id": "s1", "action": "act-1", "rationale": "why-1"},
                {"step_id": "s2", "action": "act-2", "rationale": "why-2"},
            ],
            "assumptions": [],
        }
    )


async def test_run_once_returns_partial_plan() -> None:
    store = InMemoryArtifactStore()
    result = await run_once(
        request=RunRequest(objective="demo"),
        llm=FakeLLM([_plan_payload()]),
        clock=FakeClock(),
        store=store,
    )

    assert result.status is RunStatus.PARTIAL
    assert result.plan is not None
    assert [outcome.status for outcome in result.outcomes] == [
        StepStatus.SKIPPED,
        StepStatus.SKIPPED,
    ]
    assert len(result.artifacts) == 1
    assert json.loads(store.read(result.artifacts[0].artifact_id))["objective"] == "demo"


async def test_run_once_failure_is_typed() -> None:
    result = await run_once(
        request=RunRequest(objective="demo"),
        llm=FakeLLM(["not json"]),
        clock=FakeClock(),
        store=InMemoryArtifactStore(),
    )

    assert result.status is RunStatus.FAILED
    assert result.error_code == "structured_output_invalid"
    assert result.artifacts == ()
    assert result.plan is None


def test_run_id_is_stable_for_same_input() -> None:
    clock = FakeClock()
    first = build_run_id("ill", "demo", clock.now())
    second = build_run_id("ill", "demo", clock.now())
    assert first == second
    assert first.startswith("ill_")
