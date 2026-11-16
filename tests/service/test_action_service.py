"""动作计划与完成规则。"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from illumination.repo.models import User
from illumination.repo.sql_repositories import SqlUnitOfWork
from illumination.service.action_service import complete_action, plan_action
from illumination.service.cycle_service import activate_cycle, plan_cycle
from illumination.service.errors import (
    ActionNotFoundError,
    ActionRejected,
)
from illumination.types.enums import (
    ActionStatus,
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
from illumination.types.execution import Cycle
from illumination.types.goal import Goal, GoalVersion, Metric

START = datetime(2026, 11, 16, 4, 0, tzinfo=UTC)
END = START + timedelta(days=7)
DONE_AT = START + timedelta(days=1)


def _seed_cycle(session: Session) -> tuple[SqlUnitOfWork, Cycle]:
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

    cycle = plan_cycle(
        uow,
        goal_id=goal.goal_id,
        goal_version_id=str(version.version_id),
        seq=1,
        start_at=START,
        end_at=END,
    )
    return uow, activate_cycle(
        uow, cycle_id=cycle.cycle_id, activated_at=START - timedelta(days=1)
    )


def test_plan_action_persists_pending_observation(session: Session) -> None:
    uow, cycle = _seed_cycle(session)

    action = plan_action(
        uow,
        cycle_id=cycle.cycle_id,
        seq=1,
        title="跑步 30 分钟",
        metric_key=MetricKey.PRIMARY,
        duration_min=30,
    )

    assert action.status is ActionStatus.PENDING
    assert action.metric_key is MetricKey.PRIMARY
    assert uow.actions.get(action.action_id) == action


def test_plan_action_requires_observation(session: Session) -> None:
    uow, cycle = _seed_cycle(session)

    with pytest.raises(ActionRejected) as error:
        plan_action(
            uow,
            cycle_id=cycle.cycle_id,
            seq=1,
            title="随便做点什么",
            metric_key=None,
            duration_min=30,
        )

    assert error.value.reason == "missing_observation"


def test_plan_action_requires_active_cycle(session: Session) -> None:
    uow, cycle = _seed_cycle(session)
    frozen = plan_cycle(
        uow,
        goal_id=cycle.goal_id,
        goal_version_id=cycle.goal_version_id,
        seq=2,
        start_at=END,
        end_at=END + timedelta(days=7),
    )

    with pytest.raises(ActionRejected) as error:
        plan_action(
            uow,
            cycle_id=frozen.cycle_id,
            seq=1,
            title="跑步 30 分钟",
            metric_key=MetricKey.PRIMARY,
            duration_min=30,
        )

    assert error.value.reason == "cycle_not_active"


def test_complete_action_is_idempotent(session: Session) -> None:
    uow, cycle = _seed_cycle(session)
    action = plan_action(
        uow,
        cycle_id=cycle.cycle_id,
        seq=1,
        title="跑步 30 分钟",
        metric_key=MetricKey.PRIMARY,
        duration_min=30,
    )

    first = complete_action(uow, action_id=action.action_id, completed_at=DONE_AT)
    second = complete_action(
        uow, action_id=action.action_id, completed_at=DONE_AT + timedelta(hours=1)
    )

    assert first.already_completed is False
    assert first.action.status is ActionStatus.DONE
    assert first.action.completed_at == DONE_AT
    assert second.already_completed is True
    assert second.action.completed_at == DONE_AT
    assert uow.actions.get(action.action_id) == first.action


def test_complete_action_rejects_reviewed_cycle(session: Session) -> None:
    uow, cycle = _seed_cycle(session)
    action = plan_action(
        uow,
        cycle_id=cycle.cycle_id,
        seq=1,
        title="跑步 30 分钟",
        metric_key=MetricKey.PRIMARY,
        duration_min=30,
    )
    uow.cycles.save(cycle.model_copy(update={"state": CycleState.REVIEWED}))
    uow.commit()

    with pytest.raises(ActionRejected) as error:
        complete_action(uow, action_id=action.action_id, completed_at=DONE_AT)

    assert error.value.reason == "cycle_closed"
    stored = uow.actions.get(action.action_id)
    assert stored is not None and stored.status is ActionStatus.PENDING


def test_complete_missing_action_fails(session: Session) -> None:
    uow, _ = _seed_cycle(session)

    with pytest.raises(ActionNotFoundError) as error:
        complete_action(uow, action_id=str(uuid.uuid4()), completed_at=DONE_AT)

    assert error.value.code == "action_not_found"
