"""Cycle 计划与激活：frozen → active、单 active 与 14 天提前窗口。"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from illumination.repo.models import User
from illumination.repo.sql_repositories import SqlUnitOfWork
from illumination.service.cycle_service import activate_cycle, plan_cycle
from illumination.service.errors import (
    CycleActivationRejected,
    CycleNotFoundError,
)
from illumination.types.enums import (
    BaselineState,
    CycleState,
    GoalChangeKind,
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

START = datetime(2026, 11, 16, 4, 0, tzinfo=UTC)
END = START + timedelta(days=7)


def _seed(session: Session) -> tuple[User, Goal, GoalVersion]:
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
        statement="每周跑步三次，每次 30 分钟",
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
                baseline_state=BaselineState.UNKNOWN,
                target_value=3,
                threshold_source=ThresholdSource.USER_INPUT,
                written_in=ThresholdWriteWindow.PLAN,
            ),
        ),
    )
    uow = SqlUnitOfWork(session)
    uow.goals.add(user_id=str(user.id), goal=goal)
    uow.goals.append_version(version)
    uow.commit()
    return user, goal, version


def test_plan_cycle_starts_frozen(session: Session) -> None:
    _, goal, version = _seed(session)
    uow = SqlUnitOfWork(session)

    cycle = plan_cycle(
        uow,
        goal_id=goal.goal_id,
        goal_version_id=str(version.version_id),
        seq=1,
        start_at=START,
        end_at=END,
    )

    assert cycle.state is CycleState.FROZEN
    assert uow.cycles.get(cycle.cycle_id) == cycle


def test_activate_frozen_cycle(session: Session) -> None:
    _, goal, version = _seed(session)
    uow = SqlUnitOfWork(session)
    cycle = plan_cycle(
        uow,
        goal_id=goal.goal_id,
        goal_version_id=str(version.version_id),
        seq=1,
        start_at=START,
        end_at=END,
    )

    activated = activate_cycle(
        uow, cycle_id=cycle.cycle_id, activated_at=START - timedelta(days=1)
    )

    assert activated.state is CycleState.ACTIVE
    stored = uow.cycles.get(cycle.cycle_id)
    assert stored is not None and stored.state is CycleState.ACTIVE
    assert uow.cycles.find_active(goal.goal_id) == activated


def test_activate_rejects_second_active_cycle(session: Session) -> None:
    _, goal, version = _seed(session)
    uow = SqlUnitOfWork(session)
    first = plan_cycle(
        uow,
        goal_id=goal.goal_id,
        goal_version_id=str(version.version_id),
        seq=1,
        start_at=START,
        end_at=END,
    )
    second = plan_cycle(
        uow,
        goal_id=goal.goal_id,
        goal_version_id=str(version.version_id),
        seq=2,
        start_at=END,
        end_at=END + timedelta(days=7),
    )
    activate_cycle(uow, cycle_id=first.cycle_id, activated_at=START - timedelta(days=1))

    with pytest.raises(CycleActivationRejected) as error:
        activate_cycle(uow, cycle_id=second.cycle_id, activated_at=START)

    assert error.value.reason == "goal_already_active"
    stored = uow.cycles.get(second.cycle_id)
    assert stored is not None and stored.state is CycleState.FROZEN


def test_activate_rejects_more_than_fourteen_days_early(session: Session) -> None:
    _, goal, version = _seed(session)
    uow = SqlUnitOfWork(session)
    cycle = plan_cycle(
        uow,
        goal_id=goal.goal_id,
        goal_version_id=str(version.version_id),
        seq=1,
        start_at=START + timedelta(days=30),
        end_at=END + timedelta(days=30),
    )

    with pytest.raises(CycleActivationRejected) as error:
        activate_cycle(uow, cycle_id=cycle.cycle_id, activated_at=START)

    assert error.value.reason == "activated_too_early"


def test_repeated_activation_is_invalid_transition(session: Session) -> None:
    _, goal, version = _seed(session)
    uow = SqlUnitOfWork(session)
    cycle = plan_cycle(
        uow,
        goal_id=goal.goal_id,
        goal_version_id=str(version.version_id),
        seq=1,
        start_at=START,
        end_at=END,
    )
    activate_cycle(uow, cycle_id=cycle.cycle_id, activated_at=START - timedelta(days=1))

    with pytest.raises(CycleActivationRejected) as error:
        activate_cycle(uow, cycle_id=cycle.cycle_id, activated_at=START)

    assert error.value.reason == "invalid_transition"


def test_activation_of_missing_cycle_fails(session: Session) -> None:
    uow = SqlUnitOfWork(session)

    with pytest.raises(CycleNotFoundError) as error:
        activate_cycle(uow, cycle_id=str(uuid.uuid4()), activated_at=START)

    assert error.value.code == "cycle_not_found"
