"""目标锁定：就绪度、名额、版本快照与事务回滚。"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from illumination.repo.models import GoalVersion as GoalVersionRow
from illumination.repo.models import User
from illumination.repo.sql_repositories import SqlUnitOfWork
from illumination.service.errors import (
    GoalLockRejected,
    GoalNotReadyError,
    GoalQuotaExceededError,
)
from illumination.service.goal_lifecycle import lock_goal
from illumination.service.readiness import ReadinessResult
from illumination.types.enums import (
    BaselineState,
    GoalChangeKind,
    GoalIntent,
    GoalRole,
    GoalStatus,
    IntervalSource,
    MetricKey,
    MetricKind,
    ReadinessLevel,
    ThresholdSource,
    ThresholdWriteWindow,
)
from illumination.types.goal import Goal, GoalVersion, Metric

START = datetime(2026, 11, 16, 4, 0, tzinfo=UTC)
LOCKED_ON = date(2026, 11, 16)


def _user(session: Session) -> User:
    user = User(id=uuid.uuid4(), display_name="tester")
    session.add(user)
    session.flush()
    return user


def _goal(**overrides: object) -> Goal:
    values: dict[str, object] = {
        "goal_id": str(uuid.uuid4()),
        "name_final": "每周跑步三次",
        "intent": GoalIntent.ADVANCE,
        "role": GoalRole.PRIMARY,
        "status": GoalStatus.ACTIVE,
        "started_at": START,
    }
    values.update(overrides)
    return Goal(**values)  # type: ignore[arg-type]


def _metric() -> Metric:
    return Metric(
        metric_key=MetricKey.PRIMARY,
        name="每周跑步次数",
        kind=MetricKind.NUMERIC,
        unit_code="count",
        display_unit_code="count",
        baseline_state=BaselineState.UNKNOWN,
        target_value=3,
        threshold_source=ThresholdSource.USER_INPUT,
        written_in=ThresholdWriteWindow.PLAN,
    )


def _version(
    goal_id: str,
    *,
    role: GoalRole = GoalRole.PRIMARY,
    version_id: str | None = None,
) -> GoalVersion:
    return GoalVersion(
        version_id=version_id or str(uuid.uuid4()),
        goal_id=goal_id,
        version_no=1,
        statement="每周跑步三次，每次 30 分钟",
        intent=GoalIntent.ADVANCE,
        role=role,
        feedback_interval_days=7,
        interval_source=IntervalSource.USER_INPUT,
        deadline_end=date(2027, 2, 16),
        has_no_constraints=True,
        change_kind=GoalChangeKind.INITIAL,
        effective_at=START,
        metrics=(_metric(),),
    )


def _ready() -> ReadinessResult:
    return ReadinessResult(
        level=ReadinessLevel.L3, missing_fields=(), stop_lock=False, stop_reasons=()
    )


def test_lock_creates_goal_and_first_version(session: Session) -> None:
    user = _user(session)
    goal = _goal()
    version = _version(goal.goal_id)
    uow = SqlUnitOfWork(session)

    result = lock_goal(
        uow,
        user_id=str(user.id),
        goal=goal,
        version=version,
        readiness=_ready(),
        locked_on=LOCKED_ON,
    )

    assert result.created is True
    assert result.goal == goal
    assert result.version.version_id == version.version_id
    assert uow.goals.get(goal.goal_id) == goal
    stored = uow.goals.get_version(goal.goal_id, 1)
    assert stored is not None and stored.metrics[0].target_value == 3


def test_lock_rejects_not_ready_goal(session: Session) -> None:
    user = _user(session)
    goal = _goal()
    uow = SqlUnitOfWork(session)
    not_ready = ReadinessResult(
        level=ReadinessLevel.L2,
        missing_fields=("feedback_interval",),
        stop_lock=False,
        stop_reasons=(),
    )

    with pytest.raises(GoalNotReadyError) as error:
        lock_goal(
            uow,
            user_id=str(user.id),
            goal=goal,
            version=_version(goal.goal_id),
            readiness=not_ready,
            locked_on=LOCKED_ON,
        )

    assert error.value.code == "goal_not_ready"
    assert error.value.missing_fields == ("feedback_interval",)
    assert uow.goals.get(goal.goal_id) is None


def test_lock_rejects_second_primary_goal(session: Session) -> None:
    user = _user(session)
    first = _goal()
    uow = SqlUnitOfWork(session)
    lock_goal(
        uow,
        user_id=str(user.id),
        goal=first,
        version=_version(first.goal_id),
        readiness=_ready(),
        locked_on=LOCKED_ON,
    )

    second = _goal()
    with pytest.raises(GoalQuotaExceededError) as error:
        lock_goal(
            uow,
            user_id=str(user.id),
            goal=second,
            version=_version(second.goal_id),
            readiness=_ready(),
            locked_on=LOCKED_ON,
        )

    assert error.value.code == "goal_quota_exceeded"
    assert uow.goals.count_active_by_role(str(user.id), GoalRole.PRIMARY) == 1


def test_duplicate_lock_returns_existing_goal(session: Session) -> None:
    user = _user(session)
    goal = _goal()
    version = _version(goal.goal_id)
    uow = SqlUnitOfWork(session)
    first = lock_goal(
        uow,
        user_id=str(user.id),
        goal=goal,
        version=version,
        readiness=_ready(),
        locked_on=LOCKED_ON,
    )

    second = lock_goal(
        uow,
        user_id=str(user.id),
        goal=goal,
        version=version,
        readiness=_ready(),
        locked_on=LOCKED_ON,
    )

    assert first.created is True
    assert second.created is False
    assert second.goal == goal
    assert session.scalar(select(func.count()).select_from(GoalVersionRow)) == 1


def test_lock_rolls_back_when_version_write_fails(session: Session) -> None:
    user = _user(session)
    first = _goal()
    uow = SqlUnitOfWork(session)
    locked = lock_goal(
        uow,
        user_id=str(user.id),
        goal=first,
        version=_version(first.goal_id),
        readiness=_ready(),
        locked_on=LOCKED_ON,
    )
    reused_version_id = str(locked.version.version_id)

    second = _goal(role=GoalRole.SECONDARY)
    with pytest.raises(IntegrityError):
        lock_goal(
            uow,
            user_id=str(user.id),
            goal=second,
            version=_version(
                second.goal_id, role=GoalRole.SECONDARY, version_id=reused_version_id
            ),
            readiness=_ready(),
            locked_on=LOCKED_ON,
        )

    assert uow.goals.get(second.goal_id) is None


def test_lock_accepts_planned_goal_within_fourteen_days(session: Session) -> None:
    user = _user(session)
    goal = _goal(status=GoalStatus.PLANNED, started_at=None, plan_start_date=date(2026, 11, 23))
    uow = SqlUnitOfWork(session)

    result = lock_goal(
        uow,
        user_id=str(user.id),
        goal=goal,
        version=_version(goal.goal_id),
        readiness=_ready(),
        locked_on=LOCKED_ON,
    )

    assert result.created is True
    assert uow.goals.get(goal.goal_id) == goal


def test_lock_rejects_planned_goal_beyond_fourteen_days(session: Session) -> None:
    user = _user(session)
    goal = _goal(status=GoalStatus.PLANNED, started_at=None, plan_start_date=date(2026, 12, 1))
    uow = SqlUnitOfWork(session)

    with pytest.raises(GoalLockRejected) as error:
        lock_goal(
            uow,
            user_id=str(user.id),
            goal=goal,
            version=_version(goal.goal_id),
            readiness=_ready(),
            locked_on=LOCKED_ON,
        )

    assert error.value.code == "goal_lock_rejected"
    assert error.value.reason == "plan_start_too_far"
