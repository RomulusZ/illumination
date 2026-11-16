"""周期评估：期末宽限、confirmed 主指标聚合与 reviewed 收口。"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Literal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from illumination.repo.models import Review as ReviewRow
from illumination.repo.models import User
from illumination.repo.sql_repositories import SqlUnitOfWork
from illumination.service.errors import CycleReviewRejected
from illumination.service.review_service import (
    CycleReviewRule,
    review_cycle,
    start_closing,
)
from illumination.types.enums import (
    ActionStatus,
    BaselineState,
    CycleResult,
    CycleState,
    EvidenceChannel,
    EvidenceStatus,
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
from illumination.types.execution import Action, Cycle, Evidence
from illumination.types.goal import Goal, GoalVersion, Metric

START = datetime(2026, 11, 16, 4, 0, tzinfo=UTC)
END = START + timedelta(days=7)
GRACE_END = END + timedelta(days=1)


def _seed(session: Session, *, target_value: float = 6) -> tuple[SqlUnitOfWork, Cycle]:
    user = User(id=uuid.uuid4(), display_name="tester")
    session.add(user)
    session.flush()
    goal = Goal(
        goal_id=str(uuid.uuid4()),
        name_final="每周跑步三次",
        intent=GoalIntent.ADVANCE,
        role=GoalRole.PRIMARY,
        status=GoalStatus.ACTIVE,
        started_at=START,
    )
    version = GoalVersion(
        version_id=str(uuid.uuid4()),
        goal_id=goal.goal_id,
        version_no=1,
        statement="每周跑步三次",
        intent=GoalIntent.ADVANCE,
        role=GoalRole.PRIMARY,
        feedback_interval_days=7,
        interval_source=IntervalSource.USER_INPUT,
        deadline_end=date(2027, 2, 16),
        has_no_constraints=True,
        change_kind=GoalChangeKind.INITIAL,
        effective_at=START,
        metrics=(
            Metric(
                metric_key=MetricKey.PRIMARY,
                name="每周跑步次数",
                kind=MetricKind.NUMERIC,
                unit_code="count",
                display_unit_code="count",
                direction=MetricDirection.UP,
                baseline_state=BaselineState.UNKNOWN,
                target_value=target_value,
                threshold_source=ThresholdSource.USER_INPUT,
                written_in=ThresholdWriteWindow.PLAN,
            ),
        ),
    )
    cycle = Cycle(
        cycle_id=str(uuid.uuid4()),
        goal_id=goal.goal_id,
        goal_version_id=str(version.version_id),
        seq=1,
        state=CycleState.ACTIVE,
        start_at=START,
        end_at=END,
    )
    uow = SqlUnitOfWork(session)
    uow.goals.add(user_id=str(user.id), goal=goal)
    uow.goals.append_version(version)
    uow.cycles.add(cycle)
    uow.commit()
    return uow, cycle


def _evidence(
    cycle: Cycle,
    *,
    captured_at: datetime,
    value: float | None = 1,
    status: EvidenceStatus = EvidenceStatus.CONFIRMED,
    metric_key: MetricKey = MetricKey.PRIMARY,
) -> Evidence:
    is_candidate = status is EvidenceStatus.CANDIDATE
    return Evidence(
        evidence_id=str(uuid.uuid4()),
        goal_id=cycle.goal_id,
        cycle_id=cycle.cycle_id,
        metric_key=metric_key,
        captured_at=captured_at,
        day_cutoff_date=captured_at.date(),
        source_channel=EvidenceChannel.DEVICE if is_candidate else EvidenceChannel.MANUAL,
        status=status,
        confirmed_at=None if is_candidate else captured_at,
        expires_at=captured_at + timedelta(hours=1) if is_candidate else None,
        source_device="watch" if is_candidate else None,
        external_id=str(uuid.uuid4()) if is_candidate else None,
        entered_value=value,
        entered_unit="count" if value is not None else None,
        value_calc=value,
        unit_code="count" if value is not None else None,
    )


def _action(cycle: Cycle, *, status: ActionStatus) -> Action:
    return Action(
        action_id=str(uuid.uuid4()),
        cycle_id=cycle.cycle_id,
        goal_id=cycle.goal_id,
        seq=1 if status is ActionStatus.DONE else 2,
        title="跑步",
        metric_key=MetricKey.PRIMARY,
        duration_min=30,
        status=status,
        completed_at=END if status is ActionStatus.DONE else None,
    )


def _closing(uow: SqlUnitOfWork, cycle: Cycle) -> Cycle:
    return start_closing(uow, cycle_id=cycle.cycle_id, closed_at=END)


def test_start_closing_sets_one_day_grace_without_review(session: Session) -> None:
    uow, cycle = _seed(session)

    closing = _closing(uow, cycle)

    assert closing.state is CycleState.CLOSING
    assert closing.closing_until == GRACE_END
    assert uow.reviews.get_by_cycle(cycle.cycle_id) is None
    assert uow.cycles.get(cycle.cycle_id) == closing


def test_start_closing_before_cycle_end_is_rejected(session: Session) -> None:
    uow, cycle = _seed(session)

    with pytest.raises(CycleReviewRejected) as error:
        start_closing(uow, cycle_id=cycle.cycle_id, closed_at=END - timedelta(seconds=1))

    assert error.value.reason == "cycle_not_ended"
    stored = uow.cycles.get(cycle.cycle_id)
    assert stored is not None and stored.state is CycleState.ACTIVE


def test_review_during_grace_is_rejected_without_partial_write(session: Session) -> None:
    uow, cycle = _seed(session)
    _closing(uow, cycle)

    with pytest.raises(CycleReviewRejected) as error:
        review_cycle(
            uow,
            cycle_id=cycle.cycle_id,
            reviewed_at=GRACE_END - timedelta(seconds=1),
            rule=CycleReviewRule(aggregation="sum", minimum_data_points=1),
        )

    assert error.value.reason == "grace_period_active"
    stored = uow.cycles.get(cycle.cycle_id)
    assert stored is not None and stored.state is CycleState.CLOSING
    assert uow.reviews.get_by_cycle(cycle.cycle_id) is None


def test_no_confirmed_evidence_closes_as_insufficient_data(session: Session) -> None:
    uow, cycle = _seed(session)
    _closing(uow, cycle)
    uow.evidences.add(
        _evidence(cycle, captured_at=START, value=100, status=EvidenceStatus.CANDIDATE)
    )
    uow.commit()

    result = review_cycle(
        uow,
        cycle_id=cycle.cycle_id,
        reviewed_at=GRACE_END,
        rule=CycleReviewRule(aggregation="sum", minimum_data_points=1),
    )

    assert result.review.cycle_result is CycleResult.INSUFFICIENT_DATA
    assert result.confirmed_evidence_count == 0
    assert result.data_point_count == 0
    assert result.observed_value is None
    assert result.cycle.state is CycleState.REVIEWED


def test_review_sums_daily_means_and_ignores_candidate_and_secondary(
    session: Session,
) -> None:
    uow, cycle = _seed(session, target_value=6)
    _closing(uow, cycle)
    uow.evidences.add(_evidence(cycle, captured_at=START, value=2))
    uow.evidences.add(_evidence(cycle, captured_at=START + timedelta(hours=2), value=4))
    uow.evidences.add(_evidence(cycle, captured_at=START + timedelta(days=1), value=3))
    uow.evidences.add(
        _evidence(
            cycle,
            captured_at=START + timedelta(days=2),
            value=100,
            status=EvidenceStatus.CANDIDATE,
        )
    )
    uow.evidences.add(
        _evidence(
            cycle,
            captured_at=START + timedelta(days=2),
            value=100,
            metric_key=MetricKey.SECONDARY_1,
        )
    )
    uow.actions.add(_action(cycle, status=ActionStatus.DONE))
    uow.actions.add(_action(cycle, status=ActionStatus.PENDING))
    uow.commit()
    rule = CycleReviewRule(aggregation="sum", minimum_data_points=2)

    result = review_cycle(uow, cycle_id=cycle.cycle_id, reviewed_at=GRACE_END, rule=rule)

    assert result.review.cycle_result is CycleResult.MET
    assert result.confirmed_evidence_count == 3
    assert result.data_point_count == 2
    assert result.observed_value == 6
    assert result.action_count == 2
    assert result.completed_action_count == 1
    assert result.review.gap_bucket is None


def test_enough_confirmed_data_below_target_is_not_met(session: Session) -> None:
    uow, cycle = _seed(session, target_value=5)
    _closing(uow, cycle)
    uow.evidences.add(_evidence(cycle, captured_at=START, value=2))
    uow.evidences.add(_evidence(cycle, captured_at=START + timedelta(days=1), value=1))
    uow.commit()

    result = review_cycle(
        uow,
        cycle_id=cycle.cycle_id,
        reviewed_at=GRACE_END,
        rule=CycleReviewRule(aggregation="mean", minimum_data_points=2),
    )

    assert result.review.cycle_result is CycleResult.NOT_MET
    assert result.observed_value == 1.5


def test_late_evidence_inside_grace_counts_but_after_grace_does_not(
    session: Session,
) -> None:
    uow, cycle = _seed(session, target_value=2)
    _closing(uow, cycle)
    uow.evidences.add(_evidence(cycle, captured_at=END + timedelta(hours=2), value=2))
    uow.evidences.add(_evidence(cycle, captured_at=GRACE_END, value=100))
    uow.commit()

    result = review_cycle(
        uow,
        cycle_id=cycle.cycle_id,
        reviewed_at=GRACE_END,
        rule=CycleReviewRule(aggregation="sum", minimum_data_points=1),
    )

    assert result.review.cycle_result is CycleResult.MET
    assert result.confirmed_evidence_count == 1
    assert result.observed_value == 2


def test_duplicate_review_is_rejected_and_history_is_not_recalculated(
    session: Session,
) -> None:
    uow, cycle = _seed(session)
    _closing(uow, cycle)
    rule = CycleReviewRule(aggregation="sum", minimum_data_points=1)
    first = review_cycle(uow, cycle_id=cycle.cycle_id, reviewed_at=GRACE_END, rule=rule)
    uow.evidences.add(_evidence(cycle, captured_at=START, value=100))
    uow.commit()

    with pytest.raises(CycleReviewRejected) as error:
        review_cycle(uow, cycle_id=cycle.cycle_id, reviewed_at=GRACE_END, rule=rule)

    assert error.value.reason == "review_exists"
    assert uow.reviews.get_by_cycle(cycle.cycle_id) == first.review
    assert session.scalar(select(func.count()).select_from(ReviewRow)) == 1


@pytest.mark.parametrize(
    ("operation", "expected_reason"),
    [
        ("review_active", "invalid_transition"),
        ("close_reviewed", "invalid_transition"),
    ],
)
def test_invalid_state_transitions_are_rejected(
    session: Session, operation: Literal["review_active", "close_reviewed"], expected_reason: str
) -> None:
    uow, cycle = _seed(session)
    rule = CycleReviewRule(aggregation="sum", minimum_data_points=1)
    if operation == "close_reviewed":
        _closing(uow, cycle)
        review_cycle(uow, cycle_id=cycle.cycle_id, reviewed_at=GRACE_END, rule=rule)

    with pytest.raises(CycleReviewRejected) as error:
        if operation == "review_active":
            review_cycle(uow, cycle_id=cycle.cycle_id, reviewed_at=GRACE_END, rule=rule)
        else:
            start_closing(uow, cycle_id=cycle.cycle_id, closed_at=GRACE_END)

    assert error.value.reason == expected_reason
