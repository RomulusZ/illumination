"""SQL 仓储与 UnitOfWork 的真实 PostgreSQL 测试。

覆盖保存/读取、版本 append-only、事务回滚、单 active 单元与并发重复版本。
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from illumination.repo.models import GoalVersion as GoalVersionRow
from illumination.repo.models import User
from illumination.repo.sql_repositories import SqlUnitOfWork
from illumination.types.enums import (
    BaselineState,
    CycleResult,
    CycleState,
    EvidenceChannel,
    EvidenceStatus,
    GoalChangeKind,
    GoalClosureType,
    GoalIntent,
    GoalRole,
    GoalStatus,
    IntervalSource,
    MetricKey,
    MetricKind,
    ReviewScope,
    ThresholdSource,
    ThresholdWriteWindow,
)
from illumination.types.execution import Cycle, Evidence, Review
from illumination.types.goal import Goal, GoalVersion, Metric
from illumination.types.ports import UnitOfWorkPort

START = datetime(2026, 11, 16, 4, 0, tzinfo=UTC)
END = START + timedelta(days=7)


def _user(session: Session) -> User:
    user = User(id=uuid.uuid4(), display_name="tester")
    session.add(user)
    session.flush()
    return user


def _goal() -> Goal:
    return Goal(
        goal_id=str(uuid.uuid4()),
        name_final="每周跑步三次",
        intent=GoalIntent.ADVANCE,
        role=GoalRole.PRIMARY,
        status=GoalStatus.ACTIVE,
        started_at=START,
    )


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
    version_no: int = 1,
    version_id: str | None = None,
    statement: str = "每周跑步三次，每次 30 分钟",
) -> GoalVersion:
    return GoalVersion(
        version_id=version_id or str(uuid.uuid4()),
        goal_id=goal_id,
        version_no=version_no,
        statement=statement,
        intent=GoalIntent.ADVANCE,
        role=GoalRole.PRIMARY,
        feedback_interval_days=7,
        interval_source=IntervalSource.USER_INPUT,
        deadline_end=date(2027, 2, 16),
        has_no_constraints=True,
        change_kind=GoalChangeKind.INITIAL,
        effective_at=START,
        metrics=(_metric(),),
    )


def _cycle(goal_id: str, version_id: str, **overrides: object) -> Cycle:
    values: dict[str, object] = {
        "cycle_id": str(uuid.uuid4()),
        "goal_id": goal_id,
        "goal_version_id": version_id,
        "seq": 1,
        "state": CycleState.ACTIVE,
        "start_at": START,
        "end_at": END,
    }
    values.update(overrides)
    return Cycle(**values)  # type: ignore[arg-type]


def _evidence(goal_id: str) -> Evidence:
    return Evidence(
        evidence_id=str(uuid.uuid4()),
        goal_id=goal_id,
        metric_key=MetricKey.PRIMARY,
        captured_at=START,
        source_channel=EvidenceChannel.MANUAL,
        status=EvidenceStatus.CONFIRMED,
        confirmed_at=START,
        day_cutoff_date=date(2026, 11, 16),
    )


def test_uow_persists_goal_versions_and_active_primary(session: Session) -> None:
    user = _user(session)
    other = _user(session)
    goal = _goal()
    version = _version(goal.goal_id)
    uow = SqlUnitOfWork(session)
    assert isinstance(uow, UnitOfWorkPort)

    uow.goals.add(user_id=str(user.id), goal=goal)
    uow.goals.append_version(version)
    uow.commit()
    session.expire_all()

    assert uow.goals.get(goal.goal_id) == goal
    assert uow.goals.find_active_primary(str(user.id)) == goal
    assert uow.goals.find_active_primary(str(other.id)) is None
    loaded = uow.goals.get_version(goal.goal_id, 1)
    assert loaded is not None
    assert loaded.version_id == version.version_id
    assert loaded.metrics[0].metric_key is MetricKey.PRIMARY
    assert loaded.metrics[0].target_value == 3


def test_goal_save_updates_closure_without_touching_version(session: Session) -> None:
    user = _user(session)
    goal = _goal()
    version = _version(goal.goal_id)
    uow = SqlUnitOfWork(session)
    uow.goals.add(user_id=str(user.id), goal=goal)
    uow.goals.append_version(version)
    uow.commit()

    closed = goal.model_copy(
        update={
            "status": GoalStatus.ACHIEVED,
            "closed_at": START,
            "closure_type": GoalClosureType.ACHIEVED,
        }
    )
    uow.goals.save(closed)
    uow.commit()
    session.expire_all()

    assert uow.goals.get(goal.goal_id) == closed
    assert uow.goals.get_version(goal.goal_id, 1) == version


def test_version_append_keeps_history_and_rejects_duplicate(session: Session) -> None:
    user = _user(session)
    goal = _goal()
    uow = SqlUnitOfWork(session)
    uow.goals.add(user_id=str(user.id), goal=goal)
    uow.goals.append_version(_version(goal.goal_id, version_no=1, statement="v1"))
    uow.goals.append_version(_version(goal.goal_id, version_no=2, statement="v2"))
    uow.commit()

    first = uow.goals.get_version(goal.goal_id, 1)
    assert first is not None and first.statement == "v1"
    assert session.scalar(select(func.count()).select_from(GoalVersionRow)) == 2

    with pytest.raises(IntegrityError, match="uq_goal_versions"):
        uow.goals.append_version(_version(goal.goal_id, version_no=1, statement="dup"))
    uow.rollback()


def test_rollback_leaves_no_rows(session: Session) -> None:
    user = _user(session)
    goal = _goal()
    uow = SqlUnitOfWork(session)
    uow.goals.add(user_id=str(user.id), goal=goal)
    uow.goals.append_version(_version(goal.goal_id))

    uow.rollback()

    assert uow.goals.get(goal.goal_id) is None
    assert uow.goals.get_version(goal.goal_id, 1) is None


def test_cycle_repository_roundtrip_and_single_active(session: Session) -> None:
    user = _user(session)
    goal = _goal()
    version = _version(goal.goal_id)
    cycle = _cycle(goal.goal_id, str(version.version_id))
    uow = SqlUnitOfWork(session)
    uow.goals.add(user_id=str(user.id), goal=goal)
    uow.goals.append_version(version)
    uow.cycles.add(cycle)
    uow.commit()

    assert uow.cycles.get(cycle.cycle_id) == cycle
    assert uow.cycles.find_active(goal.goal_id) == cycle

    with pytest.raises(IntegrityError, match="uq_cycle_single_active"):
        uow.cycles.add(_cycle(goal.goal_id, str(version.version_id), seq=2))
    uow.rollback()


def test_evidence_repository_counts_only_confirmed(session: Session) -> None:
    user = _user(session)
    goal = _goal()
    first = _evidence(goal.goal_id)
    second = _evidence(goal.goal_id)
    uow = SqlUnitOfWork(session)
    uow.goals.add(user_id=str(user.id), goal=goal)
    uow.evidences.add(first)
    uow.evidences.add(second)
    uow.commit()

    assert uow.evidences.get(first.evidence_id) == first
    assert uow.evidences.count_confirmed(goal.goal_id) == 2


def test_review_repository_roundtrip(session: Session) -> None:
    user = _user(session)
    goal = _goal()
    version = _version(goal.goal_id)
    cycle = _cycle(goal.goal_id, str(version.version_id))
    review = Review(
        review_id=str(uuid.uuid4()),
        goal_id=goal.goal_id,
        scope=ReviewScope.CYCLE,
        cycle_id=cycle.cycle_id,
        cycle_result=CycleResult.MET,
    )
    uow = SqlUnitOfWork(session)
    uow.goals.add(user_id=str(user.id), goal=goal)
    uow.goals.append_version(version)
    uow.cycles.add(cycle)
    uow.reviews.add(review)
    uow.commit()

    assert uow.reviews.get_by_cycle(cycle.cycle_id) == review


def test_concurrent_version_append_rejects_duplicate(
    session: Session, migrated_engine: Engine
) -> None:
    user = _user(session)
    goal = _goal()
    uow = SqlUnitOfWork(session)
    uow.goals.add(user_id=str(user.id), goal=goal)
    uow.goals.append_version(_version(goal.goal_id, version_no=1))
    uow.commit()

    with Session(migrated_engine) as first, Session(migrated_engine) as second:
        winner = SqlUnitOfWork(first)
        loser = SqlUnitOfWork(second)
        winner.goals.append_version(_version(goal.goal_id, version_no=2))
        winner.commit()

        with pytest.raises(IntegrityError, match="uq_goal_versions"):
            loser.goals.append_version(_version(goal.goal_id, version_no=2))
        loser.rollback()
