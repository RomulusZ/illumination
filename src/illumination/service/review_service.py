"""周期评估：期末宽限、confirmed 主指标聚合与 append-only 收口。"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import Field

from illumination.service.errors import CycleNotFoundError, CycleReviewRejected
from illumination.types.base import IlluminationModel
from illumination.types.enums import (
    ActionStatus,
    CycleResult,
    CycleState,
    EvidenceMode,
    EvidenceStatus,
    MetricDirection,
    MetricKey,
    MetricKind,
    ReviewScope,
)
from illumination.types.execution import Cycle, Evidence, Review
from illumination.types.goal import Metric
from illumination.types.ports import UnitOfWorkPort

GRACE_PERIOD = timedelta(days=1)


class CycleReviewRule(IlluminationModel):
    """由指标模板显式提供的判定口径；服务不猜测模板默认值。"""

    aggregation: Literal["sum", "mean"]
    minimum_data_points: int = Field(ge=1)


class CycleReviewResult(IlluminationModel):
    """评估及当时的输入计数快照；正文证据不进入返回值。"""

    review: Review
    cycle: Cycle
    confirmed_evidence_count: int
    data_point_count: int
    action_count: int
    completed_action_count: int
    observed_value: float | None


def start_closing(uow: UnitOfWorkPort, *, cycle_id: str, closed_at: datetime) -> Cycle:
    """期末把 active 单元转为 closing；只进入宽限，不自动 reviewed。"""
    _require_aware(closed_at, "closed_at")
    cycle = _get_cycle(uow, cycle_id)
    if cycle.state is not CycleState.ACTIVE:
        raise CycleReviewRejected("只有 active 单元可以进入宽限", reason="invalid_transition")
    if closed_at < cycle.end_at:
        raise CycleReviewRejected("单元尚未到期", reason="cycle_not_ended")

    closing = cycle.model_copy(
        update={"state": CycleState.CLOSING, "closing_until": cycle.end_at + GRACE_PERIOD}
    )
    try:
        uow.cycles.save(closing)
        uow.commit()
    except Exception:
        uow.rollback()
        raise
    return closing


def review_cycle(
    uow: UnitOfWorkPort,
    *,
    cycle_id: str,
    reviewed_at: datetime,
    rule: CycleReviewRule,
    milestone_reached: bool = False,
    summary: str | None = None,
) -> CycleReviewResult:
    """宽限结束后计算结论，并在同一事务内追加 Review 和关闭单元。"""
    _require_aware(reviewed_at, "reviewed_at")
    cycle = _get_cycle(uow, cycle_id)
    if uow.reviews.get_by_cycle(cycle_id) is not None:
        raise CycleReviewRejected("单元已有评估，历史结论不可重算", reason="review_exists")
    if cycle.state is not CycleState.CLOSING or cycle.closing_until is None:
        raise CycleReviewRejected("只有 closing 单元可以正常评估", reason="invalid_transition")
    if reviewed_at < cycle.closing_until:
        raise CycleReviewRejected("证据宽限期尚未结束", reason="grace_period_active")

    version = uow.goals.get_version_by_id(cycle.goal_version_id)
    if version is None:
        raise CycleReviewRejected("单元绑定的目标版本不存在", reason="goal_version_not_found")
    metric = next(item for item in version.metrics if item.metric_key is MetricKey.PRIMARY)
    evidences = uow.evidences.list_for_cycle(cycle_id)
    actions = uow.actions.list_for_cycle(cycle_id)
    outcome, observed, evidence_count, point_count = _evaluate(cycle, metric, evidences, rule)
    review = Review(
        review_id=str(uuid.uuid4()),
        goal_id=cycle.goal_id,
        scope=ReviewScope.CYCLE,
        cycle_id=cycle_id,
        cycle_result=outcome,
        milestone_reached=milestone_reached,
        summary=summary,
    )
    reviewed = cycle.model_copy(update={"state": CycleState.REVIEWED})
    try:
        uow.reviews.add(review)
        uow.cycles.save(reviewed)
        uow.commit()
    except Exception:
        uow.rollback()
        raise
    return CycleReviewResult(
        review=review,
        cycle=reviewed,
        confirmed_evidence_count=evidence_count,
        data_point_count=point_count,
        action_count=len(actions),
        completed_action_count=sum(action.status is ActionStatus.DONE for action in actions),
        observed_value=observed,
    )


def _evaluate(
    cycle: Cycle, metric: Metric, evidences: list[Evidence], rule: CycleReviewRule
) -> tuple[CycleResult, float | None, int, int]:
    assert cycle.closing_until is not None
    eligible = [
        evidence
        for evidence in evidences
        if evidence.status is EvidenceStatus.CONFIRMED
        and evidence.mode is EvidenceMode.NORMAL
        and evidence.metric_key is MetricKey.PRIMARY
        and evidence.value_calc is not None
        and cycle.start_at <= evidence.captured_at < cycle.closing_until
    ]
    by_day: dict[date, list[float]] = defaultdict(list)
    for evidence in eligible:
        assert evidence.value_calc is not None
        by_day[evidence.day_cutoff_date].append(evidence.value_calc)
    daily_values = [sum(values) / len(values) for _, values in sorted(by_day.items())]
    observed = (
        (sum(daily_values) if rule.aggregation == "sum" else sum(daily_values) / len(daily_values))
        if daily_values
        else None
    )
    if (
        metric.kind is not MetricKind.NUMERIC
        or metric.target_value is None
        or len(daily_values) < rule.minimum_data_points
    ):
        return CycleResult.INSUFFICIENT_DATA, observed, len(eligible), len(daily_values)
    assert observed is not None
    met = {
        MetricDirection.UP: observed >= metric.target_value,
        MetricDirection.DOWN: observed <= metric.target_value,
        MetricDirection.HOLD: observed == metric.target_value,
    }[metric.direction]
    result = CycleResult.MET if met else CycleResult.NOT_MET
    return result, observed, len(eligible), len(daily_values)


def _get_cycle(uow: UnitOfWorkPort, cycle_id: str) -> Cycle:
    cycle = uow.cycles.get(cycle_id)
    if cycle is None:
        raise CycleNotFoundError(f"cycle {cycle_id} 不存在")
    return cycle


def _require_aware(moment: datetime, name: str) -> None:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{name} 必须是带时区的时间")
