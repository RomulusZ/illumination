"""Goal identity, append-only versions and metric contracts."""

from __future__ import annotations

from datetime import date, datetime
from typing import Self

from pydantic import Field, model_validator

from illumination.types.base import IlluminationModel
from illumination.types.enums import (
    BaselineState,
    GoalChangeKind,
    GoalIntent,
    GoalRole,
    GoalStatus,
    IntervalSource,
    MetricDirection,
    MetricKey,
    MetricKind,
    ThresholdSource,
    ThresholdWriteWindow,
)


class IntentCreate(IlluminationModel):
    """入口对话产出的意图；不占目标名额。"""

    statement: str = Field(min_length=1)
    situation: str | None = None
    intended_start_date: date | None = None


class Intent(IntentCreate):
    intent_id: str = Field(min_length=1)


class Metric(IlluminationModel):
    """目标版本内的一个观测项；基线三态与目标形态由代码校验。"""

    metric_key: MetricKey
    name: str = Field(min_length=1)
    kind: MetricKind
    unit_code: str = Field(min_length=1)
    display_unit_code: str = Field(min_length=1)
    direction: MetricDirection = MetricDirection.UP
    baseline_state: BaselineState
    baseline_value: float | None = None
    baseline_unit_code: str | None = None
    baseline_captured_at: datetime | None = None
    target_value: float | None = None
    target_expected_state: str | None = None
    threshold_source: ThresholdSource | None = None
    written_in: ThresholdWriteWindow | None = None

    @model_validator(mode="after")
    def _validate_baseline(self) -> Self:
        if self.baseline_state is BaselineState.UNKNOWN:
            if self.baseline_value is not None:
                raise ValueError("基线为 unknown 时不得给出数值")
            return self
        if (
            self.baseline_value is None
            or self.baseline_unit_code is None
            or self.baseline_captured_at is None
        ):
            raise ValueError("measured / estimate 基线必须带数值、单位与采集时间")
        return self

    @model_validator(mode="after")
    def _validate_target(self) -> Self:
        if self.kind is MetricKind.NUMERIC:
            if self.target_value is None or self.target_expected_state is not None:
                raise ValueError("数值型指标必须给出目标值，且不得带期望状态")
        elif self.target_expected_state is None:
            raise ValueError("状态型 / 产出型指标必须给出期望状态")
        if (self.target_value is not None or self.target_expected_state is not None) and (
            self.threshold_source is None or self.written_in is None
        ):
            raise ValueError("目标值必须带阈值来源与写入窗口")
        return self


class GoalVersionCreate(IlluminationModel):
    """一次可追加的验收口径快照输入。"""

    statement: str = Field(min_length=1)
    intent: GoalIntent
    role: GoalRole
    feedback_interval_days: int = Field(ge=1, le=30)
    interval_source: IntervalSource
    deadline_end: date | None = None
    constraints: tuple[str, ...] = ()
    has_no_constraints: bool = False
    change_kind: GoalChangeKind
    change_note: str | None = Field(default=None, max_length=200)
    effective_at: datetime
    metrics: tuple[Metric, ...]

    @model_validator(mode="after")
    def _validate_deadline(self) -> Self:
        if self.intent is GoalIntent.ADVANCE and self.deadline_end is None:
            raise ValueError("advance 目标必须有 deadline_end")
        if self.intent is GoalIntent.MAINTAIN and self.deadline_end is not None:
            raise ValueError("maintain 目标不得有 deadline_end")
        return self

    @model_validator(mode="after")
    def _validate_constraints(self) -> Self:
        if self.has_no_constraints and self.constraints:
            raise ValueError("已声明无约束时不得再提供约束条目")
        if not self.has_no_constraints and not self.constraints:
            raise ValueError("约束必须显式声明为「暂时没有」")
        return self

    @model_validator(mode="after")
    def _validate_metric_quota(self) -> Self:
        primary_count = sum(metric.metric_key is MetricKey.PRIMARY for metric in self.metrics)
        if primary_count != 1:
            raise ValueError("必须有且仅有一个主指标")
        if len({metric.metric_key for metric in self.metrics}) != len(self.metrics):
            raise ValueError("指标槽位不能重复")
        return self


class GoalVersion(GoalVersionCreate):
    """已落库的版本；append-only，历史行不更新。"""

    goal_id: str = Field(min_length=1)
    version_no: int = Field(ge=1)
    version_id: str | None = Field(default=None, min_length=1)


class Goal(IlluminationModel):
    """目标的稳定身份；验收口径在版本快照里。"""

    goal_id: str = Field(min_length=1)
    name_final: str = Field(min_length=1)
    intent: GoalIntent
    role: GoalRole
    status: GoalStatus = GoalStatus.PLANNED
    intent_id: str | None = None
    current_version: int = Field(default=1, ge=1)
    plan_start_date: date | None = None
    started_at: datetime | None = None
    closed_at: datetime | None = None

    @model_validator(mode="after")
    def _validate_role(self) -> Self:
        if self.intent is GoalIntent.MAINTAIN and self.role is GoalRole.PRIMARY:
            raise ValueError("maintain 目标不能占用主位")
        return self


class GoalCreate(IlluminationModel):
    """锁定目标时的输入：稳定身份字段 + 首个版本快照。"""

    name_final: str = Field(min_length=1)
    intent_id: str | None = None
    version: GoalVersionCreate
