"""Goal 撤回、恢复、暂停与收口状态机。"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from illumination.repo.models import GoalVersion as GoalVersionRow
from illumination.repo.models import User
from illumination.repo.sql_repositories import SqlUnitOfWork
from illumination.service.errors import GoalNotFoundError, GoalTransitionRejected
from illumination.service.goal_closure import transition_goal
from illumination.types.enums import (
    BaselineState,
    GoalChangeKind,
    GoalClosureType,
    GoalIntent,
    GoalRole,
    GoalStatus,
    IntervalSource,
    MetricKey,
    MetricKind,
    ThresholdSource,
    ThresholdWriteWindow,
)
from illumination.types.goal import Goal, GoalVersion, Metric

LOCKED_AT = datetime(2026, 11, 16, 4, 0, tzinfo=UTC)
NOW = LOCKED_AT + timedelta(hours=1)

CLOSURE_BY_STATUS = {
    GoalStatus.ACHIEVED: GoalClosureType.ACHIEVED,
    GoalStatus.ARCHIVED: GoalClosureType.PARTIAL,
    GoalStatus.ABANDONED: GoalClosureType.ABANDONED,
    GoalStatus.EXPIRED: GoalClosureType.EXPIRED,
}
LEGAL_TRANSITIONS = {
    (GoalStatus.PLANNED, GoalStatus.ACTIVE),
    (GoalStatus.PLANNED, GoalStatus.WITHDRAWN),
    (GoalStatus.ACTIVE, GoalStatus.ACHIEVED),
    (GoalStatus.ACTIVE, GoalStatus.ARCHIVED),
    (GoalStatus.ACTIVE, GoalStatus.ABANDONED),
    (GoalStatus.ACTIVE, GoalStatus.EXPIRED),
    (GoalStatus.ACTIVE, GoalStatus.WITHDRAWN),
    (GoalStatus.ACTIVE, GoalStatus.PAUSED),
    (GoalStatus.ABANDONED, GoalStatus.ACTIVE),
    (GoalStatus.EXPIRED, GoalStatus.ARCHIVED),
    (GoalStatus.WITHDRAWN, GoalStatus.PLANNED),
    (GoalStatus.WITHDRAWN, GoalStatus.ACTIVE),
    (GoalStatus.PAUSED, GoalStatus.ACTIVE),
}


def _user(session: Session) -> User:
    user = User(id=uuid.uuid4(), display_name="tester")
    session.add(user)
    session.flush()
    return user


def _goal(status: GoalStatus = GoalStatus.ACTIVE, **overrides: object) -> Goal:
    values: dict[str, object] = {
        "goal_id": str(uuid.uuid4()),
        "name_final": "每周跑步三次",
        "intent": GoalIntent.ADVANCE,
        "role": GoalRole.PRIMARY,
        "status": status,
        "started_at": LOCKED_AT if status is not GoalStatus.PLANNED else None,
        "plan_start_date": date(2026, 11, 16) if status is GoalStatus.PLANNED else None,
        "closed_at": LOCKED_AT if status in CLOSURE_BY_STATUS else None,
        "closure_type": CLOSURE_BY_STATUS.get(status),
    }
    values.update(overrides)
    return Goal(**values)  # type: ignore[arg-type]


def _version(goal_id: str) -> GoalVersion:
    return GoalVersion(
        version_id=str(uuid.uuid4()),
        goal_id=goal_id,
        version_no=1,
        statement="每周跑步三次，每次 30 分钟",
        intent=GoalIntent.ADVANCE,
        role=GoalRole.PRIMARY,
        feedback_interval_days=7,
        interval_source=IntervalSource.USER_INPUT,
        deadline_end=date(2027, 2, 16),
        has_no_constraints=True,
        change_kind=GoalChangeKind.INITIAL,
        effective_at=LOCKED_AT,
        metrics=(
            Metric(
                metric_key=MetricKey.PRIMARY,
                name="每周跑步次数",
                kind=MetricKind.NUMERIC,
                unit_code="count",
                display_unit_code="count",
                baseline_state=BaselineState.UNKNOWN,
                target_value=3,
                threshold_source=ThresholdSource.USER_INPUT,
                written_in=ThresholdWriteWindow.PLAN,
            ),
        ),
    )


def _stored(session: Session, goal: Goal) -> tuple[User, SqlUnitOfWork]:
    user = _user(session)
    uow = SqlUnitOfWork(session)
    uow.goals.add(user_id=str(user.id), goal=goal)
    uow.goals.append_version(_version(goal.goal_id))
    uow.commit()
    return user, uow


def _transition(
    user: User,
    uow: SqlUnitOfWork,
    goal: Goal,
    target: GoalStatus,
    *,
    at: datetime = NOW,
    closure_type: GoalClosureType | None = None,
) -> Goal:
    return transition_goal(
        uow,
        user_id=str(user.id),
        goal_id=goal.goal_id,
        target=target,
        transitioned_at=at,
        closure_type=closure_type,
    )


@pytest.mark.parametrize("source", [GoalStatus.PLANNED, GoalStatus.ACTIVE])
def test_withdraw_within_window_preserves_goal_and_version(
    session: Session, source: GoalStatus
) -> None:
    goal = _goal(source)
    user, uow = _stored(session, goal)
    original_version = uow.goals.get_version(goal.goal_id, 1)

    result = _transition(user, uow, goal, GoalStatus.WITHDRAWN, at=LOCKED_AT + timedelta(hours=24))

    assert result.status is GoalStatus.WITHDRAWN
    assert result.closed_at == LOCKED_AT + timedelta(hours=24)
    assert uow.goals.get(goal.goal_id) == result
    assert uow.goals.get_version(goal.goal_id, 1) == original_version
    assert session.scalar(select(func.count()).select_from(GoalVersionRow)) == 1


def test_planned_goal_activates_on_start_date(session: Session) -> None:
    goal = _goal(GoalStatus.PLANNED)
    user, uow = _stored(session, goal)

    result = _transition(user, uow, goal, GoalStatus.ACTIVE)

    assert result.status is GoalStatus.ACTIVE
    assert result.started_at == NOW
    assert result.plan_start_date == goal.plan_start_date


def test_withdraw_after_window_is_rejected(session: Session) -> None:
    goal = _goal()
    user, uow = _stored(session, goal)

    with pytest.raises(GoalTransitionRejected) as error:
        _transition(
            user,
            uow,
            goal,
            GoalStatus.WITHDRAWN,
            at=LOCKED_AT + timedelta(hours=24, microseconds=1),
        )

    assert error.value.reason == "withdrawal_window_expired"
    assert uow.goals.get(goal.goal_id) == goal


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (GoalStatus.PAUSED, GoalStatus.ACTIVE),
        (GoalStatus.WITHDRAWN, GoalStatus.ACTIVE),
        (GoalStatus.WITHDRAWN, GoalStatus.PLANNED),
    ],
)
def test_resume_paths_restore_without_new_version(
    session: Session, source: GoalStatus, target: GoalStatus
) -> None:
    goal = _goal(
        source,
        started_at=None if target is GoalStatus.PLANNED else LOCKED_AT,
        plan_start_date=date(2026, 11, 16) if target is GoalStatus.PLANNED else None,
    )
    user, uow = _stored(session, goal)

    result = _transition(user, uow, goal, target, at=LOCKED_AT + timedelta(days=30))

    assert result.status is target
    assert result.closed_at is None
    assert result.current_version == 1
    assert session.scalar(select(func.count()).select_from(GoalVersionRow)) == 1


def test_pause_and_resume_return_to_active(session: Session) -> None:
    goal = _goal()
    user, uow = _stored(session, goal)

    paused = _transition(user, uow, goal, GoalStatus.PAUSED)
    resumed = _transition(user, uow, paused, GoalStatus.ACTIVE)

    assert paused.status is GoalStatus.PAUSED
    assert resumed.status is GoalStatus.ACTIVE
    assert resumed.started_at == goal.started_at


@pytest.mark.parametrize(
    ("target", "closure_type"),
    [
        (GoalStatus.ACHIEVED, GoalClosureType.ACHIEVED),
        (GoalStatus.ARCHIVED, GoalClosureType.PARTIAL),
        (GoalStatus.ABANDONED, GoalClosureType.ABANDONED),
        (GoalStatus.EXPIRED, GoalClosureType.EXPIRED),
    ],
)
def test_active_goal_closes_with_typed_closure(
    session: Session, target: GoalStatus, closure_type: GoalClosureType
) -> None:
    goal = _goal()
    user, uow = _stored(session, goal)

    result = _transition(user, uow, goal, target, closure_type=closure_type)

    assert result.status is target
    assert result.closure_type is closure_type
    assert result.closed_at == NOW
    assert result.current_version == 1


def test_expired_goal_can_only_archive(session: Session) -> None:
    goal = _goal(GoalStatus.EXPIRED)
    user, uow = _stored(session, goal)

    result = _transition(
        user,
        uow,
        goal,
        GoalStatus.ARCHIVED,
        closure_type=GoalClosureType.EXPIRED,
    )

    assert result.status is GoalStatus.ARCHIVED
    assert result.closure_type is GoalClosureType.EXPIRED


def test_abandoned_goal_can_be_undone_within_twenty_four_hours(session: Session) -> None:
    goal = _goal(GoalStatus.ABANDONED)
    user, uow = _stored(session, goal)

    result = _transition(
        user,
        uow,
        goal,
        GoalStatus.ACTIVE,
        at=LOCKED_AT + timedelta(hours=24),
    )

    assert result.status is GoalStatus.ACTIVE
    assert result.closed_at is None
    assert result.closure_type is None


def test_abandoned_goal_cannot_be_undone_after_twenty_four_hours(session: Session) -> None:
    goal = _goal(GoalStatus.ABANDONED)
    user, uow = _stored(session, goal)

    with pytest.raises(GoalTransitionRejected) as error:
        _transition(
            user,
            uow,
            goal,
            GoalStatus.ACTIVE,
            at=LOCKED_AT + timedelta(hours=24, microseconds=1),
        )

    assert error.value.reason == "abandon_undo_window_expired"
    assert uow.goals.get(goal.goal_id) == goal


def test_archive_requires_explicit_reason(session: Session) -> None:
    goal = _goal()
    user, uow = _stored(session, goal)

    with pytest.raises(GoalTransitionRejected) as error:
        _transition(user, uow, goal, GoalStatus.ARCHIVED)

    assert error.value.reason == "closure_type_required"


def test_terminal_status_cannot_be_changed(session: Session) -> None:
    goal = _goal(GoalStatus.ACHIEVED)
    user, uow = _stored(session, goal)

    with pytest.raises(GoalTransitionRejected) as error:
        _transition(user, uow, goal, GoalStatus.ACTIVE)

    assert error.value.reason == "invalid_transition"
    assert uow.goals.get(goal.goal_id) == goal


def test_maintain_goal_cannot_expire(session: Session) -> None:
    goal = _goal(intent=GoalIntent.MAINTAIN, role=GoalRole.SECONDARY)
    user, uow = _stored(session, goal)

    with pytest.raises(GoalTransitionRejected) as error:
        _transition(
            user,
            uow,
            goal,
            GoalStatus.EXPIRED,
            closure_type=GoalClosureType.EXPIRED,
        )

    assert error.value.reason == "maintain_cannot_expire"


def test_transition_rejects_goal_owned_by_another_user(session: Session) -> None:
    goal = _goal()
    _, uow = _stored(session, goal)
    other = _user(session)

    with pytest.raises(GoalNotFoundError):
        _transition(other, uow, goal, GoalStatus.PAUSED)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (source, target)
        for source in GoalStatus
        for target in GoalStatus
        if (source, target) not in LEGAL_TRANSITIONS
    ],
)
def test_every_illegal_transition_is_rejected(
    session: Session, source: GoalStatus, target: GoalStatus
) -> None:
    goal = _goal(source)
    user, uow = _stored(session, goal)

    with pytest.raises(GoalTransitionRejected) as error:
        _transition(
            user,
            uow,
            goal,
            target,
            closure_type=CLOSURE_BY_STATUS.get(target),
        )

    assert error.value.code == "goal_transition_rejected"
    assert error.value.reason == "invalid_transition"
    assert uow.goals.get(goal.goal_id) == goal
