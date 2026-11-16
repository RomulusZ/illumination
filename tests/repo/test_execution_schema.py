"""执行侧与幂等 schema 的 PostgreSQL 迁移与约束测试。

迁移测试在一次性空库执行 upgrade → downgrade(0001) → upgrade；约束测试在会话级
测试库上运行，每个用例通过事务回滚与 TRUNCATE 隔离。任何情况下都不回退 SQLite。
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from sqlalchemy import Engine, create_engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from illumination.repo.models import (
    Action,
    Cycle,
    Evidence,
    Goal,
    GoalVersion,
    IdempotencyKey,
    Review,
    SyncOp,
    User,
)
from illumination.types.enums import (
    ActionStatus,
    ActionType,
    CycleResult,
    CycleState,
    EvidenceChannel,
    EvidenceStatus,
    GoalIntent,
    GoalRole,
    GoalStatus,
    IntervalSource,
    ReviewScope,
    StageDecision,
)

EXPECTED_TABLES = {
    "users",
    "goals",
    "goal_versions",
    "goal_metrics",
    "cycles",
    "actions",
    "evidences",
    "reviews",
    "idempotency_keys",
    "sync_ops",
}

START = datetime(2026, 11, 16, 4, 0, tzinfo=UTC)
END = START + timedelta(days=7)


def _alembic_config(repo_root: Path, database_url: str) -> Config:
    config = Config(str(repo_root / "alembic.ini"))
    config.set_main_option("script_location", str(repo_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _table_names(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names()) - {"alembic_version"}


def _index_names(engine: Engine, table: str) -> set[str]:
    return {index["name"] for index in inspect(engine).get_indexes(table)}


def _enum_count(engine: Engine, name: str) -> int:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT count(*) FROM pg_type WHERE typname = :name"), {"name": name}
        ).scalar_one()


def _user() -> User:
    return User(id=uuid.uuid4(), display_name="tester")


def _goal(user_id: uuid.UUID) -> Goal:
    return Goal(
        user_id=user_id,
        intent=GoalIntent.ADVANCE,
        role=GoalRole.PRIMARY,
        status=GoalStatus.ACTIVE,
        name_final="每周跑步三次",
        plan_start_date=date(2026, 11, 16),
    )


def _version(goal_id: uuid.UUID) -> GoalVersion:
    return GoalVersion(
        goal_id=goal_id,
        version_no=1,
        statement="每周跑步三次，每次 30 分钟",
        intent=GoalIntent.ADVANCE,
        role=GoalRole.PRIMARY,
        feedback_interval_days=7,
        interval_source=IntervalSource.USER_INPUT,
        deadline_end=date(2027, 2, 16),
        change_kind="initial",
        constraints_none=True,
        effective_at=START,
    )


def _cycle(goal_id: uuid.UUID, version_id: uuid.UUID, **overrides: Any) -> Cycle:
    values: dict[str, Any] = {
        "goal_id": goal_id,
        "goal_version_id": version_id,
        "seq": 1,
        "state": CycleState.ACTIVE,
        "start_at": START,
        "end_at": END,
    }
    values.update(overrides)
    return Cycle(**values)


def _action(cycle_id: uuid.UUID, goal_id: uuid.UUID, **overrides: Any) -> Action:
    values: dict[str, Any] = {
        "cycle_id": cycle_id,
        "goal_id": goal_id,
        "seq": 1,
        "title": "跑步 30 分钟",
        "metric_key": "primary",
        "action_type": ActionType.ONCE,
        "duration_min": 30,
    }
    values.update(overrides)
    return Action(**values)


def _evidence(goal_id: uuid.UUID, **overrides: Any) -> Evidence:
    values: dict[str, Any] = {
        "goal_id": goal_id,
        "metric_key": "primary",
        "captured_at": START,
        "source_channel": EvidenceChannel.MANUAL,
        "status": EvidenceStatus.CONFIRMED,
        "confirmed_at": START,
        "day_cutoff_date": date(2026, 11, 16),
    }
    values.update(overrides)
    return Evidence(**values)


def _seed_goal(session: Session) -> tuple[Goal, GoalVersion]:
    user = _user()
    session.add(user)
    session.flush()
    goal = _goal(user.id)
    session.add(goal)
    session.flush()
    version = _version(goal.id)
    session.add(version)
    session.flush()
    return goal, version


def test_migrations_are_repeatable(empty_database_url: str, repo_root: Path) -> None:
    config = _alembic_config(repo_root, empty_database_url)

    command.upgrade(config, "head")
    engine = create_engine(empty_database_url)
    try:
        assert _table_names(engine) == EXPECTED_TABLES
        assert {
            foreign_key["referred_table"]
            for foreign_key in inspect(engine).get_foreign_keys("cycles")
        } == {"goals", "goal_versions"}
        assert "uq_cycle_single_active" in _index_names(engine, "cycles")
        assert "uq_review_per_cycle" in _index_names(engine, "reviews")
        assert "uq_evidence_device_external" in _index_names(engine, "evidences")
    finally:
        engine.dispose()

    command.downgrade(config, "0001_goal_side")
    engine = create_engine(empty_database_url)
    try:
        remaining = _table_names(engine)
        assert remaining == {
            "users",
            "goals",
            "goal_versions",
            "goal_metrics",
        }
        assert _enum_count(engine, "cycle_state") == 0
        assert _enum_count(engine, "evidence_status") == 0
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(empty_database_url)
    try:
        assert _table_names(engine) == EXPECTED_TABLES
    finally:
        engine.dispose()


def test_schema_declares_execution_foreign_keys(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)

    def referred(table: str) -> set[str]:
        return {foreign_key["referred_table"] for foreign_key in inspector.get_foreign_keys(table)}

    assert referred("cycles") == {"goals", "goal_versions"}
    assert referred("actions") == {"cycles", "goals"}
    assert referred("evidences") == {"goals", "cycles", "actions"}
    assert referred("reviews") == {"goals", "cycles", "goal_versions"}
    assert referred("idempotency_keys") == {"users"}
    assert referred("sync_ops") == {"users"}


def test_only_one_active_cycle_per_goal(session: Session) -> None:
    goal, version = _seed_goal(session)
    session.add_all([_cycle(goal.id, version.id, seq=1), _cycle(goal.id, version.id, seq=2)])

    with pytest.raises(IntegrityError, match="uq_cycle_single_active"):
        session.commit()


def test_closing_cycle_coexists_with_active(session: Session) -> None:
    goal, version = _seed_goal(session)
    session.add_all(
        [
            _cycle(goal.id, version.id, seq=1),
            _cycle(
                goal.id,
                version.id,
                seq=2,
                state=CycleState.CLOSING,
                closing_until=END + timedelta(days=1),
            ),
        ]
    )

    session.commit()

    assert session.scalar(select(func.count()).select_from(Cycle)) == 2


def test_action_completed_at_matches_status(session: Session) -> None:
    goal, version = _seed_goal(session)
    cycle = _cycle(goal.id, version.id)
    session.add(cycle)
    session.flush()
    session.add(_action(cycle.id, goal.id, status=ActionStatus.DONE))

    with pytest.raises(IntegrityError, match="action_completed_at"):
        session.commit()


def test_action_requires_existing_cycle(session: Session) -> None:
    goal, _ = _seed_goal(session)
    session.add(_action(uuid.uuid4(), goal.id))

    with pytest.raises(IntegrityError, match="actions_cycle_id_fkey"):
        session.commit()


def test_candidate_evidence_must_not_carry_confirmed_at(session: Session) -> None:
    goal, _ = _seed_goal(session)
    session.add(
        _evidence(
            goal.id,
            source_channel=EvidenceChannel.DEVICE,
            source_device="garmin:watch",
            status=EvidenceStatus.CANDIDATE,
            confirmed_at=START,
            expires_at=START + timedelta(days=7),
            source_ref={"external_id": "garmin-activity-1"},
        )
    )

    with pytest.raises(IntegrityError, match="evidence_status_shape"):
        session.commit()


def test_candidate_evidence_must_come_from_device(session: Session) -> None:
    goal, _ = _seed_goal(session)
    session.add(
        _evidence(
            goal.id,
            status=EvidenceStatus.CANDIDATE,
            confirmed_at=None,
            expires_at=START + timedelta(days=7),
        )
    )

    with pytest.raises(IntegrityError, match="evidence_status_shape"):
        session.commit()


def test_device_candidate_requires_external_id(session: Session) -> None:
    goal, _ = _seed_goal(session)
    session.add(
        _evidence(
            goal.id,
            source_channel=EvidenceChannel.DEVICE,
            source_device="garmin:watch",
            status=EvidenceStatus.CANDIDATE,
            confirmed_at=None,
            expires_at=START + timedelta(days=7),
            source_ref={},
        )
    )

    with pytest.raises(IntegrityError, match="evidence_device_shape"):
        session.commit()


def test_device_external_id_is_unique(session: Session) -> None:
    goal, _ = _seed_goal(session)
    payload = {"external_id": "garmin-activity-1"}
    session.add_all(
        [
            _evidence(
                goal.id,
                source_channel=EvidenceChannel.DEVICE,
                source_device="garmin:watch",
                source_ref=payload,
            ),
            _evidence(
                goal.id,
                source_channel=EvidenceChannel.DEVICE,
                source_device="garmin:watch",
                source_ref=payload,
            ),
        ]
    )

    with pytest.raises(IntegrityError, match="uq_evidence_device_external"):
        session.commit()


def test_cycle_review_requires_cycle_and_result(session: Session) -> None:
    goal, _ = _seed_goal(session)
    session.add(Review(goal_id=goal.id, scope=ReviewScope.CYCLE, cycle_result=None))

    with pytest.raises(IntegrityError, match="review_shape"):
        session.commit()


def test_deferred_stage_review_cannot_carry_decision(session: Session) -> None:
    goal, _ = _seed_goal(session)
    session.add(
        Review(
            goal_id=goal.id,
            scope=ReviewScope.STAGE,
            stage_index=1,
            window_start=date(2026, 11, 16),
            window_end=date(2026, 12, 16),
            deferred=True,
            decision=StageDecision.CONTINUE,
            summary="缺动作与预期，顺延",
        )
    )

    with pytest.raises(IntegrityError, match="review_shape"):
        session.commit()


def test_only_one_cycle_review_per_cycle(session: Session) -> None:
    goal, version = _seed_goal(session)
    cycle = _cycle(goal.id, version.id)
    session.add(cycle)
    session.flush()
    session.add_all(
        [
            Review(
                goal_id=goal.id,
                scope=ReviewScope.CYCLE,
                cycle_id=cycle.id,
                cycle_result=CycleResult.MET,
            ),
            Review(
                goal_id=goal.id,
                scope=ReviewScope.CYCLE,
                cycle_id=cycle.id,
                cycle_result=CycleResult.NOT_MET,
            ),
        ]
    )

    with pytest.raises(IntegrityError, match="uq_review_per_cycle"):
        session.commit()


def test_sync_op_client_id_is_unique_per_user(session: Session) -> None:
    user = _user()
    session.add(user)
    session.flush()
    client_op_id = uuid.uuid4()
    session.add_all(
        [
            SyncOp(
                user_id=user.id,
                client_op_id=client_op_id,
                device_id="phone-a",
                op_type="complete_action",
                outcome="applied",
                result_ref=uuid.uuid4(),
            ),
            SyncOp(
                user_id=user.id,
                client_op_id=client_op_id,
                device_id="phone-b",
                op_type="complete_action",
                outcome="duplicate",
                result_ref=uuid.uuid4(),
            ),
        ]
    )

    with pytest.raises(IntegrityError, match="uq_sync_ops_user_client_op"):
        session.commit()


def test_applied_sync_op_requires_result_ref(session: Session) -> None:
    user = _user()
    session.add(user)
    session.flush()
    session.add(
        SyncOp(
            user_id=user.id,
            client_op_id=uuid.uuid4(),
            device_id="phone-a",
            op_type="submit_evidence",
            captured_at=START,
            outcome="applied",
            result_ref=None,
        )
    )

    with pytest.raises(IntegrityError, match="sync_op_shape"):
        session.commit()


def test_idempotency_key_is_unique(session: Session) -> None:
    user = _user()
    session.add(user)
    session.flush()
    values = {
        "key": "req-1",
        "user_id": user.id,
        "scope": "evidence",
        "response_code": 201,
        "response_body": {"evidence_id": str(uuid.uuid4())},
        "expires_at": START + timedelta(days=1),
    }
    session.add_all([IdempotencyKey(**values), IdempotencyKey(**values)])

    with pytest.raises(IntegrityError, match="idempotency_keys_pkey"):
        session.commit()
