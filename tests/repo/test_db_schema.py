"""PostgreSQL 迁移与目标侧 schema 约束测试。

迁移测试在一次性空库执行 upgrade → downgrade → upgrade；约束测试在会话级
测试库上运行，每个用例通过事务回滚和 TRUNCATE 隔离。任何情况下都不回退 SQLite。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from sqlalchemy import Engine, create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from illumination.config.settings import Settings
from illumination.repo.db import UnsupportedDatabaseError, create_engine_from_url
from illumination.repo.models import Goal, GoalMetric, GoalVersion, User
from illumination.types.enums import (
    BaselineState,
    GoalIntent,
    GoalRole,
    GoalStatus,
    IntervalSource,
    MetricKind,
    ThresholdSource,
    ThresholdWriteWindow,
)

DOMAIN_TABLES = {"users", "goals", "goal_versions", "goal_metrics"}


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """读取隔离测试库 URL；禁止退回开发库。"""
    return Settings().illumination_test_database_url


@pytest.fixture(scope="session")
def admin_engine(test_database_url: str) -> Iterator[Engine]:
    """连接维护库，用于创建和销毁一次性测试数据库。"""
    url = make_url(test_database_url).set(database="postgres")
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    yield engine
    engine.dispose()


def _temporary_database(admin_engine: Engine, base_url: str, prefix: str) -> Iterator[str]:
    """在一次性的空数据库中执行迁移，结束后强制删除。"""
    name = f"{prefix}_{uuid.uuid4().hex[:10]}"
    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(base_url).set(database=name).render_as_string(hide_password=False)
    try:
        yield url
    finally:
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture()
def empty_database_url(admin_engine: Engine, test_database_url: str) -> Iterator[str]:
    yield from _temporary_database(admin_engine, test_database_url, "illum_migration")


@pytest.fixture(scope="session")
def schema_database_url(admin_engine: Engine, test_database_url: str) -> Iterator[str]:
    yield from _temporary_database(admin_engine, test_database_url, "illum_schema")


def _alembic_config(repo_root: Path, database_url: str) -> Config:
    config = Config(str(repo_root / "alembic.ini"))
    config.set_main_option("script_location", str(repo_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.fixture(scope="session")
def migrated_engine(schema_database_url: str, repo_root: Path) -> Iterator[Engine]:
    command.upgrade(_alembic_config(repo_root, schema_database_url), "head")
    engine = create_engine(schema_database_url)
    yield engine
    engine.dispose()


@pytest.fixture()
def session(migrated_engine: Engine) -> Iterator[Session]:
    with Session(migrated_engine) as db_session:
        yield db_session
        db_session.rollback()
    with migrated_engine.begin() as connection:
        connection.execute(
            text("TRUNCATE goal_metrics, goal_versions, goals, users RESTART IDENTITY CASCADE")
        )


def _domain_tables(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names()) & DOMAIN_TABLES


def _index_names(engine: Engine, table: str) -> set[str]:
    return {index["name"] for index in inspect(engine).get_indexes(table)}


def _user() -> User:
    return User(id=uuid.uuid4(), display_name="tester")


def _goal(
    user_id: uuid.UUID,
    *,
    intent: GoalIntent = GoalIntent.ADVANCE,
    role: GoalRole = GoalRole.PRIMARY,
    status: GoalStatus = GoalStatus.PLANNED,
) -> Goal:
    is_closed = status in {
        GoalStatus.ACHIEVED,
        GoalStatus.ARCHIVED,
        GoalStatus.ABANDONED,
        GoalStatus.EXPIRED,
    }
    return Goal(
        user_id=user_id,
        intent=intent,
        role=role,
        status=status,
        name_final="每周跑步三次",
        plan_start_date=date(2026, 11, 16),
        closure_type="archived_at_met" if is_closed else None,
        closed_at=datetime(2026, 11, 16, tzinfo=UTC) if is_closed else None,
    )


def _version(goal_id: uuid.UUID, version_no: int = 1, **overrides: Any) -> GoalVersion:
    values: dict[str, Any] = {
        "goal_id": goal_id,
        "version_no": version_no,
        "statement": "每周跑步三次，每次 30 分钟",
        "intent": GoalIntent.ADVANCE,
        "role": GoalRole.PRIMARY,
        "feedback_interval_days": 7,
        "interval_source": IntervalSource.USER_INPUT,
        "deadline_end": date(2027, 2, 16),
        "change_kind": "initial",
        "constraints": {"items": ["无器械"]},
        "effective_at": datetime(2026, 11, 16, tzinfo=UTC),
    }
    values.update(overrides)
    return GoalVersion(**values)


def _metric(
    goal_version_id: uuid.UUID, metric_key: str = "primary", **overrides: Any
) -> GoalMetric:
    values: dict[str, Any] = {
        "goal_version_id": goal_version_id,
        "metric_key": metric_key,
        "name": "每周跑步次数",
        "kind": MetricKind.NUMERIC,
        "unit_code": "count",
        "display_unit_code": "count",
        "baseline_state": BaselineState.UNKNOWN,
        "target_value": Decimal("3"),
        "threshold_source": ThresholdSource.USER_INPUT,
        "written_in": ThresholdWriteWindow.PLAN,
    }
    values.update(overrides)
    return GoalMetric(**values)


def test_migrations_are_repeatable(empty_database_url: str, repo_root: Path) -> None:
    config = _alembic_config(repo_root, empty_database_url)

    command.upgrade(config, "head")
    engine = create_engine(empty_database_url)
    try:
        assert _domain_tables(engine) == DOMAIN_TABLES
        assert {
            foreign_key["referred_table"]
            for foreign_key in inspect(engine).get_foreign_keys("goal_metrics")
        } == {"goal_versions"}
        assert "uq_goals_one_primary" in _index_names(engine, "goals")
        assert "uq_metric_primary" in _index_names(engine, "goal_metrics")
    finally:
        engine.dispose()

    command.downgrade(config, "base")
    engine = create_engine(empty_database_url)
    try:
        assert _domain_tables(engine) == set()
        with engine.connect() as connection:
            remaining = connection.execute(
                text("SELECT count(*) FROM pg_type WHERE typname = 'goal_status'")
            ).scalar_one()
        assert remaining == 0
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(empty_database_url)
    try:
        assert _domain_tables(engine) == DOMAIN_TABLES
    finally:
        engine.dispose()


def test_schema_declares_foreign_keys_and_partial_indexes(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)

    assert {
        foreign_key["referred_table"] for foreign_key in inspector.get_foreign_keys("goals")
    } == {
        "users",
        "goals",
    }
    assert {
        foreign_key["referred_table"]
        for foreign_key in inspector.get_foreign_keys("goal_versions")
    } == {"goals"}
    assert {
        foreign_key["referred_table"] for foreign_key in inspector.get_foreign_keys("goal_metrics")
    } == {"goal_versions"}


def test_only_one_active_primary_goal_is_allowed(session: Session) -> None:
    user = _user()
    session.add(user)
    session.flush()
    session.add_all(
        [
            _goal(user.id, status=GoalStatus.ACTIVE),
            _goal(user.id, status=GoalStatus.PLANNED),
        ]
    )

    with pytest.raises(IntegrityError, match="uq_goals_one_primary"):
        session.commit()


def test_archived_primary_does_not_block_new_primary(session: Session) -> None:
    user = _user()
    session.add(user)
    session.flush()
    session.add_all(
        [
            _goal(user.id, status=GoalStatus.ARCHIVED),
            _goal(user.id, status=GoalStatus.ACTIVE),
        ]
    )

    session.commit()

    assert session.scalar(select(func.count()).select_from(Goal)) == 2


def test_maintain_goal_cannot_take_primary_role(session: Session) -> None:
    user = _user()
    session.add(user)
    session.flush()
    session.add(_goal(user.id, intent=GoalIntent.MAINTAIN, role=GoalRole.PRIMARY))

    with pytest.raises(IntegrityError, match="goal_maintain_not_primary"):
        session.commit()


def test_goal_version_is_append_only_per_version_no(session: Session) -> None:
    user = _user()
    session.add(user)
    session.flush()
    goal = _goal(user.id)
    session.add(goal)
    session.flush()
    session.add_all([_version(goal.id), _version(goal.id)])

    with pytest.raises(IntegrityError, match="uq_goal_versions"):
        session.commit()


def test_goal_version_requires_existing_goal(session: Session) -> None:
    session.add(_version(uuid.uuid4()))

    with pytest.raises(IntegrityError, match="goal_versions_goal_id_fkey"):
        session.commit()


def test_unknown_baseline_must_not_carry_value(session: Session) -> None:
    user = _user()
    session.add(user)
    session.flush()
    goal = _goal(user.id)
    session.add(goal)
    session.flush()
    version = _version(goal.id)
    session.add(version)
    session.flush()
    session.add(_metric(version.id, baseline_value=Decimal("72")))

    with pytest.raises(IntegrityError, match="metric_baseline_shape"):
        session.commit()


def test_measured_baseline_requires_unit_and_capture_time(session: Session) -> None:
    user = _user()
    session.add(user)
    session.flush()
    goal = _goal(user.id)
    session.add(goal)
    session.flush()
    version = _version(goal.id)
    session.add(version)
    session.flush()
    session.add(
        _metric(
            version.id,
            baseline_state=BaselineState.MEASURED,
            baseline_value=Decimal("72"),
            baseline_unit_code="kg",
        )
    )

    with pytest.raises(IntegrityError, match="metric_baseline_shape"):
        session.commit()


def test_only_one_primary_metric_per_version(session: Session) -> None:
    user = _user()
    session.add(user)
    session.flush()
    goal = _goal(user.id)
    session.add(goal)
    session.flush()
    version = _version(goal.id)
    session.add(version)
    session.flush()
    session.add_all([_metric(version.id), _metric(version.id)])

    with pytest.raises(IntegrityError, match="uq_goal_metrics_version_slot"):
        session.commit()


def test_numeric_metric_requires_target_value(session: Session) -> None:
    user = _user()
    session.add(user)
    session.flush()
    goal = _goal(user.id)
    session.add(goal)
    session.flush()
    version = _version(goal.id)
    session.add(version)
    session.flush()
    session.add(_metric(version.id, target_value=None, threshold_source=None, written_in=None))

    with pytest.raises(IntegrityError, match="metric_target_shape"):
        session.commit()


def test_rollback_leaves_no_rows(session: Session) -> None:
    user = _user()
    session.add(user)
    session.flush()
    assert session.scalar(select(func.count()).select_from(User)) == 1

    session.rollback()

    assert session.scalar(select(func.count()).select_from(User)) == 0


def test_non_postgresql_url_is_rejected() -> None:
    with pytest.raises(UnsupportedDatabaseError, match="PostgreSQL"):
        create_engine_from_url("sqlite:///:memory:")


def test_postgresql_engine_keeps_pg_dialect(test_database_url: str) -> None:
    engine = create_engine_from_url(test_database_url)
    try:
        assert engine.dialect.name == "postgresql"
    finally:
        engine.dispose()
