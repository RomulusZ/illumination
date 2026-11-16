"""PostgreSQL 测试库共享 fixture。

迁移测试使用一次性空库；约束测试共用一个会话级 schema 库，每个用例通过事务回滚
与 TRUNCATE 隔离。任何情况下都不回退 SQLite。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from alembic import command
from illumination.config.settings import Settings

DOMAIN_TABLES = {
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


@pytest.fixture(scope="session")
def migrated_engine(schema_database_url: str, repo_root) -> Iterator[Engine]:
    config = Config(str(repo_root / "alembic.ini"))
    config.set_main_option("script_location", str(repo_root / "alembic"))
    config.set_main_option("sqlalchemy.url", schema_database_url)
    command.upgrade(config, "head")
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
            text(
                "TRUNCATE sync_ops, idempotency_keys, reviews, evidences, actions, cycles, "
                "goal_metrics, goal_versions, goals, users RESTART IDENTITY CASCADE"
            )
        )
