"""PostgreSQL engine and session factories for repository adapters.

只接受 PostgreSQL；任何其他数据库 URL 在构造阶段直接失败，不做静默降级。
连接信息来自 `Settings`，模块层不创建全局客户端。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from illumination.config.settings import Settings
from illumination.repo.errors import RepoError


class UnsupportedDatabaseError(RepoError):
    """URL 不是 PostgreSQL，或无法解析。"""


class DatabaseUnavailableError(RepoError):
    """数据库暂时不可用；调用方必须显式处理，不得回退其他存储。"""


def require_postgresql_url(database_url: str) -> URL:
    """解析并校验 PostgreSQL URL，返回不可变 URL 对象。"""
    try:
        url = make_url(database_url)
    except ArgumentError as exc:
        raise UnsupportedDatabaseError("无法解析数据库 URL") from exc
    if url.get_backend_name() != "postgresql":
        raise UnsupportedDatabaseError(
            f"仅支持 PostgreSQL，收到 {url.get_backend_name()}；禁止 SQLite 替代或回退"
        )
    return url


def create_engine_from_url(database_url: str) -> Engine:
    """按 PostgreSQL URL 创建引擎；不连接数据库，只做方言校验。"""
    url = require_postgresql_url(database_url)
    return create_engine(url, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """创建会话工厂；`expire_on_commit=False` 让提交后的对象仍可读取。"""
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@dataclass(slots=True)
class Database:
    """引擎与事务边界的组合；仅由组合根或测试持有。"""

    engine: Engine
    session_factory: sessionmaker[Session] = field(init=False)

    def __post_init__(self) -> None:
        self.session_factory = create_session_factory(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """成功即提交，异常回滚；不留半写入。"""
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def ping(self) -> None:
        """显式探活；失败必须暴露为稳定错误，而不是 fallback。"""
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            raise DatabaseUnavailableError("PostgreSQL 不可用") from exc

    def dispose(self) -> None:
        self.engine.dispose()


def build_database(settings: Settings) -> Database:
    """组合根入口：从 Settings 构造 PostgreSQL 引擎与会话工厂。"""
    return Database(engine=create_engine_from_url(settings.illumination_database_url))
