"""Ports implemented by infrastructure adapters."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from illumination.types.common import ArtifactRef
from illumination.types.enums import GoalRole
from illumination.types.execution import Action, Cycle, Evidence, Review
from illumination.types.goal import Goal, GoalVersion
from illumination.types.llm import LLMRequest, LLMResponse


@runtime_checkable
class LLMPort(Protocol):
    """External LLM capability used by services."""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Return one normalized completion for the request."""
        ...


@runtime_checkable
class ClockPort(Protocol):
    """Injectable time source; tests must not depend on the wall clock."""

    def now(self) -> datetime:
        """Return the current timezone-aware instant."""
        ...


@runtime_checkable
class ArtifactStorePort(Protocol):
    """Durable artifact sink; graph state only carries pointers."""

    def put(self, *, name: str, content: bytes) -> ArtifactRef:
        """Persist content and return its pointer."""
        ...

    def read(self, artifact_id: str) -> bytes:
        """Load the payload for an artifact pointer."""
        ...


@runtime_checkable
class GoalRepositoryPort(Protocol):
    """目标身份与 append-only 版本快照的持久化。"""

    def add(self, *, user_id: str, goal: Goal) -> None:
        """写入目标身份；同一用户的主位约束由存储层兜底。"""
        ...

    def get(self, goal_id: str) -> Goal | None:
        """按 id 读取目标；不存在返回 None。"""
        ...

    def find_active_primary(self, user_id: str) -> Goal | None:
        """读取该用户当前占主位的目标（planned 或 active）。"""
        ...

    def count_active_by_role(self, user_id: str, role: GoalRole) -> int:
        """统计该用户 planned / active 的指标名额占用。"""
        ...

    def append_version(self, version: GoalVersion) -> None:
        """追加一条版本快照及其指标；历史版本不更新。"""
        ...

    def get_version(self, goal_id: str, version_no: int) -> GoalVersion | None:
        """读取指定版本快照；不存在返回 None。"""
        ...


@runtime_checkable
class CycleRepositoryPort(Protocol):
    """节奏单元的持久化。"""

    def add(self, cycle: Cycle) -> None:
        """写入一个单元；单 active 由存储层兜底。"""
        ...

    def get(self, cycle_id: str) -> Cycle | None:
        """按 id 读取单元。"""
        ...

    def find_active(self, goal_id: str) -> Cycle | None:
        """读取目标当前 active 的单元；closing 不计入。"""
        ...

    def save(self, cycle: Cycle) -> None:
        """更新已有单元的状态与窗口；不存在时抛 LookupError。"""
        ...


@runtime_checkable
class ActionRepositoryPort(Protocol):
    """单元内动作的持久化。"""

    def add(self, action: Action) -> None:
        """写入一个动作。"""
        ...

    def get(self, action_id: str) -> Action | None:
        """按 id 读取动作。"""
        ...

    def save(self, action: Action) -> None:
        """更新动作状态与完成时间；不存在时抛 LookupError。"""
        ...


@runtime_checkable
class EvidenceRepositoryPort(Protocol):
    """证据的持久化；计数只读取 confirmed。"""

    def add(self, evidence: Evidence) -> None:
        """写入一条证据。"""
        ...

    def get(self, evidence_id: str) -> Evidence | None:
        """按 id 读取证据。"""
        ...

    def count_confirmed(self, goal_id: str) -> int:
        """统计该目标已成定值的证据条数。"""
        ...


@runtime_checkable
class ReviewRepositoryPort(Protocol):
    """周期与阶段评估的持久化。"""

    def add(self, review: Review) -> None:
        """写入一条评估。"""
        ...

    def get_by_cycle(self, cycle_id: str) -> Review | None:
        """读取某单元的周期评估。"""
        ...


@runtime_checkable
class UnitOfWorkPort(Protocol):
    """服务层使用的事务边界；提交或回滚由调用方显式决定。"""

    @property
    def goals(self) -> GoalRepositoryPort:
        """目标仓储。"""
        ...

    @property
    def cycles(self) -> CycleRepositoryPort:
        """单元仓储。"""
        ...

    @property
    def actions(self) -> ActionRepositoryPort:
        """动作仓储。"""
        ...

    @property
    def evidences(self) -> EvidenceRepositoryPort:
        """证据仓储。"""
        ...

    @property
    def reviews(self) -> ReviewRepositoryPort:
        """评估仓储。"""
        ...

    def commit(self) -> None:
        """提交当前事务。"""
        ...

    def rollback(self) -> None:
        """回滚当前事务，不留半写入。"""
        ...
