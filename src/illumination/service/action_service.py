"""动作计划与完成：必须绑定观测项，完成必须有合法周期。"""

from __future__ import annotations

import uuid
from datetime import datetime

from illumination.service.errors import (
    ActionNotFoundError,
    ActionRejected,
    CycleNotFoundError,
)
from illumination.types.base import IlluminationModel
from illumination.types.enums import ActionStatus, ActionType, CycleState, MetricKey
from illumination.types.execution import Action
from illumination.types.ports import UnitOfWorkPort


class ActionCompletionResult(IlluminationModel):
    """完成结果；重复完成返回原记录，already_completed=True。"""

    action: Action
    already_completed: bool


def plan_action(
    uow: UnitOfWorkPort,
    *,
    cycle_id: str,
    seq: int,
    title: str,
    metric_key: MetricKey | None,
    duration_min: int,
    action_type: ActionType = ActionType.ONCE,
    is_deferred: bool = False,
) -> Action:
    """在 active 单元内创建一个绑定观测项的动作。"""
    cycle = uow.cycles.get(cycle_id)
    if cycle is None:
        raise CycleNotFoundError(f"cycle {cycle_id} 不存在")
    if cycle.state is not CycleState.ACTIVE:
        raise ActionRejected("只有 active 单元可以排动作", reason="cycle_not_active")
    if metric_key is None:
        raise ActionRejected("动作必须绑定一个观测项", reason="missing_observation")
    if duration_min <= 0:
        raise ActionRejected("动作时长必须大于 0", reason="invalid_duration")

    action = Action(
        action_id=str(uuid.uuid4()),
        cycle_id=cycle_id,
        goal_id=cycle.goal_id,
        seq=seq,
        title=title,
        metric_key=metric_key,
        action_type=action_type,
        duration_min=duration_min,
        is_deferred=is_deferred,
    )
    try:
        uow.actions.add(action)
        uow.commit()
    except Exception:
        uow.rollback()
        raise
    return action


def complete_action(
    uow: UnitOfWorkPort, *, action_id: str, completed_at: datetime
) -> ActionCompletionResult:
    """完成动作；重复完成原样返回，不增加任何计数。"""
    result = build_action_completion(uow, action_id=action_id, completed_at=completed_at)
    if result.already_completed:
        return result
    try:
        uow.actions.save(result.action)
        uow.commit()
    except Exception:
        uow.rollback()
        raise
    return result


def build_action_completion(
    uow: UnitOfWorkPort, *, action_id: str, completed_at: datetime
) -> ActionCompletionResult:
    """校验并构造完成结果，不写库、不提交，供离线组合同事务落账。"""
    _require_aware(completed_at, "completed_at")
    action = uow.actions.get(action_id)
    if action is None:
        raise ActionNotFoundError(f"action {action_id} 不存在")
    if action.status in {ActionStatus.DONE, ActionStatus.FAILED}:
        return ActionCompletionResult(action=action, already_completed=True)
    if action.status is not ActionStatus.PENDING:
        raise ActionRejected("只有 pending 动作可以完成", reason="invalid_transition")

    cycle = uow.cycles.get(action.cycle_id)
    if cycle is None:
        raise CycleNotFoundError(f"cycle {action.cycle_id} 不存在")
    if cycle.state is CycleState.REVIEWED:
        raise ActionRejected("周期已收口，不能再完成动作", reason="cycle_closed")
    if cycle.state is CycleState.FROZEN:
        raise ActionRejected("周期尚未激活", reason="cycle_not_active")

    completed = action.model_copy(
        update={"status": ActionStatus.DONE, "completed_at": completed_at}
    )
    return ActionCompletionResult(action=completed, already_completed=False)


def _require_aware(moment: datetime, name: str) -> None:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{name} 必须是带时区的时间")
