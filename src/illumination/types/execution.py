"""Cycle, action, evidence and review contracts."""

from __future__ import annotations

from datetime import date, datetime
from typing import Self

from pydantic import Field, model_validator

from illumination.types.base import IlluminationModel
from illumination.types.enums import (
    ActionStatus,
    ActionType,
    CycleResult,
    CycleState,
    EvidenceChannel,
    EvidenceMode,
    EvidenceStatus,
    MetricKey,
    ReviewScope,
    StageDecision,
)


class CycleCreate(IlluminationModel):
    """一个节奏单元的创建输入；起止为左闭右开。"""

    goal_id: str = Field(min_length=1)
    goal_version_id: str = Field(min_length=1)
    seq: int = Field(ge=1)
    state: CycleState = CycleState.ACTIVE
    start_at: datetime
    end_at: datetime
    closing_until: datetime | None = None
    budget_minutes: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _validate_window(self) -> Self:
        if self.end_at <= self.start_at:
            raise ValueError("end_at 必须晚于 start_at")
        if self.state is CycleState.CLOSING and self.closing_until is None:
            raise ValueError("closing 状态必须有 closing_until")
        return self


class Cycle(CycleCreate):
    cycle_id: str = Field(min_length=1)


class ActionCreate(IlluminationModel):
    """单元内的最小动作；必须绑定一个观测项。"""

    cycle_id: str = Field(min_length=1)
    goal_id: str = Field(min_length=1)
    seq: int = Field(ge=1)
    title: str = Field(min_length=1)
    metric_key: MetricKey
    action_type: ActionType = ActionType.ONCE
    duration_min: int = Field(gt=0)
    is_deferred: bool = False


class Action(ActionCreate):
    action_id: str = Field(min_length=1)
    status: ActionStatus = ActionStatus.PENDING
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def _validate_completion(self) -> Self:
        is_closed = self.status in {ActionStatus.DONE, ActionStatus.FAILED}
        if is_closed != (self.completed_at is not None):
            raise ValueError("done / failed 状态必须与 completed_at 一一对应")
        return self


class EvidenceCreate(IlluminationModel):
    """一次证据提交；来源通道与采集时间必填。"""

    goal_id: str = Field(min_length=1)
    metric_key: MetricKey
    captured_at: datetime
    source_channel: EvidenceChannel
    status: EvidenceStatus
    mode: EvidenceMode = EvidenceMode.NORMAL
    cycle_id: str | None = None
    action_id: str | None = None
    confirmed_at: datetime | None = None
    expires_at: datetime | None = None
    source_device: str | None = None
    entered_value: float | None = None
    entered_unit: str | None = None
    value_calc: float | None = None
    unit_code: str | None = None
    display_unit_code: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _validate_status(self) -> Self:
        if (self.status is EvidenceStatus.CONFIRMED) != (self.confirmed_at is not None):
            raise ValueError("confirmed 状态必须与 confirmed_at 一一对应")
        if (self.status is EvidenceStatus.CANDIDATE) != (self.expires_at is not None):
            raise ValueError("candidate 状态必须与 expires_at 一一对应")
        if (
            self.status is EvidenceStatus.CANDIDATE
            and self.source_channel is not EvidenceChannel.DEVICE
        ):
            raise ValueError("候选证据只能来自设备")
        if self.source_channel is EvidenceChannel.DEVICE and not self.source_device:
            raise ValueError("设备来源必须说明 source_device")
        return self

    @model_validator(mode="after")
    def _validate_numeric_shape(self) -> Self:
        if self.entered_value is None and self.value_calc is None:
            return self
        if (
            self.entered_value is None
            or self.value_calc is None
            or self.entered_unit is None
            or self.unit_code is None
        ):
            raise ValueError("数值证据必须同时给出 entered_value、value_calc 与单位")
        return self


class Evidence(EvidenceCreate):
    evidence_id: str = Field(min_length=1)
    day_cutoff_date: date


class ReviewCreate(IlluminationModel):
    """周期评估或阶段评估的输入；两种 scope 的字段互斥。"""

    goal_id: str = Field(min_length=1)
    scope: ReviewScope
    cycle_id: str | None = None
    cycle_result: CycleResult | None = None
    stage_index: int | None = Field(default=None, ge=1)
    window_start: date | None = None
    window_end: date | None = None
    decision: StageDecision | None = None
    gap_bucket: int | None = Field(default=None, ge=0, le=3)
    gap_note: str | None = None
    milestone_reached: bool = False
    summary: str | None = None
    is_deferred: bool = False
    override_reason: str | None = None

    @model_validator(mode="after")
    def _validate_scope_shape(self) -> Self:
        if self.scope is ReviewScope.CYCLE:
            if self.cycle_id is None:
                raise ValueError("cycle 评估必须关联 cycle_id")
            if self.cycle_result is None:
                raise ValueError("cycle 评估必须有 cycle_result")
            if self.stage_index is not None or self.decision is not None or self.is_deferred:
                raise ValueError("cycle 评估不得携带阶段字段")
            return self
        if self.cycle_id is not None:
            raise ValueError("stage 评估不得绑定 cycle_id")
        if self.stage_index is None or self.window_start is None or self.window_end is None:
            raise ValueError("stage 评估必须有 stage_index 与窗口")
        if self.is_deferred and self.decision is not None:
            raise ValueError("顺延的阶段评估不得给出 decision")
        if not self.is_deferred and self.decision is None:
            raise ValueError("阶段评估必须给出 decision 或标记顺延")
        return self


class Review(ReviewCreate):
    review_id: str = Field(min_length=1)
