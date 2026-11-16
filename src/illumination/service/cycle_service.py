"""Cycle 计划与激活：frozen → active，单 active 与提前 14 天窗口。"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from illumination.service.errors import (
    CycleActivationRejected,
    CycleNotFoundError,
)
from illumination.types.enums import CycleState
from illumination.types.execution import Cycle
from illumination.types.ports import UnitOfWorkPort

MAX_EARLY_ACTIVATION_DAYS = 14


def plan_cycle(
    uow: UnitOfWorkPort,
    *,
    goal_id: str,
    goal_version_id: str,
    seq: int,
    start_at: datetime,
    end_at: datetime,
    budget_minutes: int | None = None,
) -> Cycle:
    """创建 frozen 单元；窗口左闭右开，激活前不参与执行。"""
    _require_aware(start_at, "start_at")
    _require_aware(end_at, "end_at")
    cycle = Cycle(
        cycle_id=str(uuid.uuid4()),
        goal_id=goal_id,
        goal_version_id=goal_version_id,
        seq=seq,
        state=CycleState.FROZEN,
        start_at=start_at,
        end_at=end_at,
        budget_minutes=budget_minutes,
    )
    try:
        uow.cycles.add(cycle)
        uow.commit()
    except Exception:
        uow.rollback()
        raise
    return cycle


def activate_cycle(uow: UnitOfWorkPort, *, cycle_id: str, activated_at: datetime) -> Cycle:
    """把 frozen 单元激活为 active；重复激活或名额冲突返回稳定 reason。"""
    _require_aware(activated_at, "activated_at")
    cycle = uow.cycles.get(cycle_id)
    if cycle is None:
        raise CycleNotFoundError(f"cycle {cycle_id} 不存在")
    if cycle.state is not CycleState.FROZEN:
        raise CycleActivationRejected("只有 frozen 单元可以激活", reason="invalid_transition")
    if cycle.start_at - activated_at > timedelta(days=MAX_EARLY_ACTIVATION_DAYS):
        raise CycleActivationRejected("提前激活不得超过 14 天", reason="activated_too_early")

    active = uow.cycles.find_active(cycle.goal_id)
    if active is not None and active.cycle_id != cycle.cycle_id:
        raise CycleActivationRejected("该目标已有 active 单元", reason="goal_already_active")

    activated = cycle.model_copy(update={"state": CycleState.ACTIVE})
    try:
        uow.cycles.save(activated)
        uow.commit()
    except Exception:
        uow.rollback()
        raise
    return activated


def _require_aware(moment: datetime, name: str) -> None:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{name} 必须是带时区的时间")
