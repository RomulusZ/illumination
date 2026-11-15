"""Contract validation tests."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from illumination.types import (
    ArtifactRef,
    LLMRequest,
    PlanStep,
    RunPlan,
    RunRequest,
    RunResult,
    RunStatus,
)


def _plan(steps: int = 1) -> RunPlan:
    return RunPlan(
        objective="demo",
        steps=tuple(
            PlanStep(step_id=f"s{index}", action="act", rationale="why")
            for index in range(1, steps + 1)
        ),
    )


def test_run_request_rejects_non_positive_max_steps() -> None:
    with pytest.raises(ValidationError):
        RunRequest(objective="demo", max_steps=0)


def test_run_plan_rejects_duplicate_step_ids() -> None:
    with pytest.raises(ValidationError):
        RunPlan(
            objective="demo",
            steps=(
                PlanStep(step_id="s1", action="a", rationale="r"),
                PlanStep(step_id="s1", action="b", rationale="r"),
            ),
        )


def test_run_result_rejects_reversed_timestamps() -> None:
    later = datetime(2026, 1, 1, 1, tzinfo=UTC)
    earlier = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValidationError):
        RunResult(
            run_id="ill_1",
            status=RunStatus.PARTIAL,
            plan=_plan(),
            started_at=later,
            finished_at=earlier,
        )


def test_artifact_ref_requires_sha256() -> None:
    with pytest.raises(ValidationError):
        ArtifactRef(artifact_id="art_1", path="x", sha256="not-a-digest", summary="x")


def test_llm_request_rejects_blank_metadata_key() -> None:
    with pytest.raises(ValidationError):
        LLMRequest.from_user("hi", metadata={" ": "value"})
