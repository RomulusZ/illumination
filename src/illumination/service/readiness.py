"""就绪度评估：只看能否排出第一个可执行单元，不看信息是否填满。

规则对齐 B1：L2 需要可判定对象 + 基线（含 unknown）+ 目标值；L3 再加反馈间隔、
目标陈述与已声明的约束。Q1/Q2 答否只停锁回 L2，答不上不算答否。
"""

from __future__ import annotations

from datetime import date
from typing import Self

from pydantic import Field, model_validator

from illumination.types.base import IlluminationModel
from illumination.types.enums import (
    AvailabilityAnswer,
    BaselineState,
    MetricKey,
    ReadinessLevel,
    ValidityAnswer,
)


class ReadinessAnswers(IlluminationModel):
    """入口对话收集到的结构化答案；缺失字段一律保持 None，不填默认值。"""

    direction: str | None = Field(default=None, min_length=1)
    statement: str | None = Field(default=None, min_length=1)
    metric_key: MetricKey | None = None
    baseline_state: BaselineState | None = None
    baseline_value: float | None = None
    target_value: float | None = None
    target_expected_state: str | None = Field(default=None, min_length=1)
    feedback_interval_days: int | None = Field(default=None, ge=1, le=90)
    constraints: tuple[str, ...] = ()
    constraints_none: bool = False
    deadline: date | None = None
    q1_validity: ValidityAnswer = ValidityAnswer.UNKNOWN
    q2_availability: AvailabilityAnswer = AvailabilityAnswer.UNKNOWN

    @model_validator(mode="after")
    def _validate_baseline(self) -> Self:
        if self.baseline_state in {BaselineState.MEASURED, BaselineState.ESTIMATE}:
            if self.baseline_value is None:
                raise ValueError("measured / estimate 基线必须有实测值或估算值")
        if self.baseline_state is BaselineState.UNKNOWN and self.baseline_value is not None:
            raise ValueError("unknown 基线不得携带数值")
        if self.constraints_none and self.constraints:
            raise ValueError("constraints_none 与 constraints 互斥")
        return self


class ReadinessResult(IlluminationModel):
    """判定结果；停锁原因与缺失条件均为稳定标识，不依赖异常文本。"""

    level: ReadinessLevel
    missing_fields: tuple[str, ...]
    stop_lock: bool
    stop_reasons: tuple[str, ...]


def assess_readiness(answers: ReadinessAnswers) -> ReadinessResult:
    """按 B1 的累积条件计算等级，并按 Q1/Q2 答否停锁。"""
    has_direction = bool(answers.direction or answers.statement)
    has_metric = answers.metric_key is not None
    has_baseline = answers.baseline_state is not None
    has_target = answers.target_value is not None or answers.target_expected_state is not None
    has_interval = answers.feedback_interval_days is not None
    has_constraints = bool(answers.constraints) or answers.constraints_none

    if not has_direction:
        level = ReadinessLevel.L0
    elif not (has_metric and has_baseline and has_target):
        level = ReadinessLevel.L1
    elif not (answers.statement and has_interval and has_constraints):
        level = ReadinessLevel.L2
    else:
        level = ReadinessLevel.L3

    stop_reasons: list[str] = []
    if answers.q1_validity is ValidityAnswer.NO:
        stop_reasons.append("q1_invalid")
    if answers.q2_availability is AvailabilityAnswer.NO:
        stop_reasons.append("q2_unavailable")
    if stop_reasons and level is ReadinessLevel.L3:
        level = ReadinessLevel.L2

    missing: list[str] = []
    if answers.statement is None:
        missing.append("statement")
    if not has_metric:
        missing.append("metric")
    if not has_baseline:
        missing.append("baseline")
    if not has_target:
        missing.append("target")
    if not has_interval:
        missing.append("feedback_interval")
    if not has_constraints:
        missing.append("constraints")

    return ReadinessResult(
        level=level,
        missing_fields=tuple(missing),
        stop_lock=bool(stop_reasons),
        stop_reasons=tuple(stop_reasons),
    )
