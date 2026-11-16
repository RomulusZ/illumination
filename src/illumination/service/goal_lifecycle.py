"""目标锁定：就绪度与名额在代码里判定，版本只追加，事务显式提交或回滚。"""

from __future__ import annotations

from datetime import date, timedelta

from illumination.service.errors import (
    GoalLockRejected,
    GoalNotReadyError,
    GoalQuotaExceededError,
)
from illumination.service.readiness import ReadinessResult
from illumination.types.base import IlluminationModel
from illumination.types.enums import GoalRole, GoalStatus, ReadinessLevel
from illumination.types.goal import Goal, GoalVersion
from illumination.types.ports import UnitOfWorkPort

QUOTA_BY_ROLE = {GoalRole.PRIMARY: 1, GoalRole.SECONDARY: 3}
MAX_PLAN_AHEAD_DAYS = 14


class GoalLockResult(IlluminationModel):
    """锁定结果；重复提交返回既有目标，created=False。"""

    goal: Goal
    version: GoalVersion
    created: bool


def lock_goal(
    uow: UnitOfWorkPort,
    *,
    user_id: str,
    goal: Goal,
    version: GoalVersion,
    readiness: ReadinessResult,
    locked_on: date,
) -> GoalLockResult:
    """锁定一个目标；重复 goal_id 幂等返回，不追加第二份 v1。"""
    existing = uow.goals.get(goal.goal_id)
    if existing is not None:
        stored = uow.goals.get_version(existing.goal_id, existing.current_version)
        return GoalLockResult(goal=existing, version=stored or version, created=False)

    if readiness.level is not ReadinessLevel.L3 or readiness.stop_lock:
        raise GoalNotReadyError(
            "目标未达到 L3 或反校验未通过，不能锁定",
            missing_fields=readiness.missing_fields,
            stop_reasons=readiness.stop_reasons,
        )

    _validate_shape(goal, version, locked_on)

    limit = QUOTA_BY_ROLE[goal.role]
    if uow.goals.count_active_by_role(user_id, goal.role) >= limit:
        raise GoalQuotaExceededError(
            f"{goal.role.value} 名额已满（上限 {limit}）",
            role=goal.role.value,
            limit=limit,
        )

    try:
        uow.goals.add(user_id=user_id, goal=goal)
        uow.goals.append_version(version)
        uow.commit()
    except Exception:
        uow.rollback()
        raise

    return GoalLockResult(goal=goal, version=version, created=True)


def _validate_shape(goal: Goal, version: GoalVersion, locked_on: date) -> None:
    if version.goal_id != goal.goal_id:
        raise GoalLockRejected("版本快照必须属于同一目标", reason="version_goal_mismatch")
    if version.version_no != 1:
        raise GoalLockRejected("锁定只能写入首个版本", reason="initial_version_required")
    if version.role is not goal.role or version.intent is not goal.intent:
        raise GoalLockRejected("版本与目标的主次或意图不一致", reason="version_identity_mismatch")

    if goal.status is GoalStatus.ACTIVE:
        if goal.started_at is None:
            raise GoalLockRejected(
                "立即开始的目标必须有 started_at", reason="active_requires_started_at"
            )
        return

    if goal.status is GoalStatus.PLANNED:
        if goal.plan_start_date is None:
            raise GoalLockRejected(
                "指定日期开始的目标必须有 plan_start_date", reason="planned_requires_date"
            )
        if goal.plan_start_date < locked_on:
            raise GoalLockRejected("开始日期不得早于锁定日", reason="plan_start_in_past")
        if goal.plan_start_date > locked_on + timedelta(days=MAX_PLAN_AHEAD_DAYS):
            raise GoalLockRejected("开始日期超过 14 天窗口", reason="plan_start_too_far")
        return

    raise GoalLockRejected("只有 planned / active 目标可以锁定", reason="status_not_lockable")
