"""Run request, plan and result contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, model_validator

from illumination.types.base import IlluminationModel
from illumination.types.common import ArtifactRef, RunStatus, StepStatus


class RunRequest(IlluminationModel):
    """One user goal handed to the harness."""

    objective: str = Field(min_length=1)
    context: tuple[str, ...] = ()
    max_steps: int = Field(default=8, ge=1, le=32)


class PlanStep(IlluminationModel):
    """One bounded unit of planned work."""

    step_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class RunPlan(IlluminationModel):
    """Structured plan produced by the planning service."""

    objective: str = Field(min_length=1)
    steps: tuple[PlanStep, ...] = Field(min_length=1)
    assumptions: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _step_ids_are_unique(self) -> RunPlan:
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("step_id 必须唯一")
        return self


class StepOutcome(IlluminationModel):
    """Observed result for one plan step."""

    step_id: str = Field(min_length=1)
    status: StepStatus
    summary: str = Field(min_length=1)


class RunResult(IlluminationModel):
    """Terminal state of one run and the pointers it produced."""

    run_id: str = Field(min_length=1)
    status: RunStatus
    plan: RunPlan | None = None
    outcomes: tuple[StepOutcome, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    started_at: datetime
    finished_at: datetime
    error_code: str | None = None

    @model_validator(mode="after")
    def _timestamps_are_ordered(self) -> RunResult:
        if self.finished_at < self.started_at:
            raise ValueError("finished_at 不能早于 started_at")
        return self
