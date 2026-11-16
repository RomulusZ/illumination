"""Goal 状态机：24h 撤回、暂停/恢复与显式收口；历史版本不重算。"""

from __future__ import annotations

from datetime import datetime, timedelta

from illumination.service.errors import GoalNotFoundError, GoalTransitionRejected
from illumination.types.enums import GoalClosureType, GoalIntent, GoalStatus
from illumination.types.goal import Goal
from illumination.types.ports import UnitOfWorkPort

WITHDRAWAL_WINDOW = timedelta(hours=24)
ABANDON_UNDO_WINDOW = timedelta(hours=24)

_ALLOWED = {
    GoalStatus.PLANNED: {GoalStatus.ACTIVE, GoalStatus.WITHDRAWN},
    GoalStatus.ACTIVE: {
        GoalStatus.ACHIEVED,
        GoalStatus.ARCHIVED,
        GoalStatus.ABANDONED,
        GoalStatus.EXPIRED,
        GoalStatus.WITHDRAWN,
        GoalStatus.PAUSED,
    },
    GoalStatus.ABANDONED: {GoalStatus.ACTIVE},
    GoalStatus.EXPIRED: {GoalStatus.ARCHIVED},
    GoalStatus.WITHDRAWN: {GoalStatus.PLANNED, GoalStatus.ACTIVE},
    GoalStatus.PAUSED: {GoalStatus.ACTIVE},
    GoalStatus.ACHIEVED: set(),
    GoalStatus.ARCHIVED: set(),
}
_CLOSED_STATUSES = {
    GoalStatus.ACHIEVED,
    GoalStatus.ARCHIVED,
    GoalStatus.ABANDONED,
    GoalStatus.EXPIRED,
}
_DEFAULT_CLOSURES = {
    GoalStatus.ACHIEVED: GoalClosureType.ACHIEVED,
    GoalStatus.ABANDONED: GoalClosureType.ABANDONED,
    GoalStatus.EXPIRED: GoalClosureType.EXPIRED,
}
_ACTIVE_ARCHIVES = {
    GoalClosureType.PARTIAL,
    GoalClosureType.ARCHIVED_AT_MET,
    GoalClosureType.REPLANNED,
}
_EXPIRED_ARCHIVES = {GoalClosureType.EXPIRED, GoalClosureType.REPLANNED}


def transition_goal(
    uow: UnitOfWorkPort,
    *,
    user_id: str,
    goal_id: str,
    target: GoalStatus,
    transitioned_at: datetime,
    closure_type: GoalClosureType | None = None,
) -> Goal:
    """执行一条合法迁移；窗口按显式时间判定，失败不写入。"""
    _require_aware(transitioned_at)
    goal = uow.goals.get_owned(user_id, goal_id)
    if goal is None:
        raise GoalNotFoundError(f"goal {goal_id} 不存在")
    if target not in _ALLOWED[goal.status]:
        raise GoalTransitionRejected("目标状态不允许该迁移", reason="invalid_transition")

    if target is GoalStatus.WITHDRAWN:
        _validate_withdrawal(uow, goal, transitioned_at)
    if goal.status is GoalStatus.ABANDONED:
        assert goal.closed_at is not None
        if transitioned_at > goal.closed_at + ABANDON_UNDO_WINDOW:
            raise GoalTransitionRejected(
                "放弃后的 24 小时撤销窗口已过", reason="abandon_undo_window_expired"
            )
    if goal.status is GoalStatus.WITHDRAWN:
        restored = GoalStatus.PLANNED if goal.started_at is None else GoalStatus.ACTIVE
        if target is not restored:
            raise GoalTransitionRejected("撤回目标只能恢复原状态", reason="invalid_transition")
    if goal.status is GoalStatus.PLANNED and target is GoalStatus.ACTIVE:
        assert goal.plan_start_date is not None
        if transitioned_at.date() < goal.plan_start_date:
            raise GoalTransitionRejected("目标尚未到开始日期", reason="plan_start_in_future")
    if target is GoalStatus.EXPIRED and goal.intent is GoalIntent.MAINTAIN:
        raise GoalTransitionRejected("维持目标没有到期状态", reason="maintain_cannot_expire")

    closure = _closure_for(goal, target, closure_type)
    started_at = goal.started_at
    if goal.status is GoalStatus.PLANNED and target is GoalStatus.ACTIVE:
        started_at = transitioned_at
    closed_at = (
        transitioned_at if target in _CLOSED_STATUSES or target is GoalStatus.WITHDRAWN else None
    )
    transitioned = goal.model_copy(
        update={
            "status": target,
            "started_at": started_at,
            "closed_at": closed_at,
            "closure_type": closure,
        }
    )
    try:
        uow.goals.save(transitioned)
        uow.commit()
    except Exception:
        uow.rollback()
        raise
    return transitioned


def _validate_withdrawal(uow: UnitOfWorkPort, goal: Goal, moment: datetime) -> None:
    version = uow.goals.get_version(goal.goal_id, goal.current_version)
    if version is None:
        raise GoalTransitionRejected("锁定版本不存在", reason="goal_version_not_found")
    if moment > version.effective_at + WITHDRAWAL_WINDOW:
        raise GoalTransitionRejected(
            "锁定后的 24 小时撤回窗口已过", reason="withdrawal_window_expired"
        )


def _closure_for(
    goal: Goal, target: GoalStatus, closure_type: GoalClosureType | None
) -> GoalClosureType | None:
    if target not in _CLOSED_STATUSES:
        if closure_type is not None:
            raise GoalTransitionRejected(
                "非收口状态不得携带收口原因", reason="closure_type_not_allowed"
            )
        return None
    if target is GoalStatus.ARCHIVED:
        if closure_type is None:
            raise GoalTransitionRejected("归档必须显式给出原因", reason="closure_type_required")
        allowed = _EXPIRED_ARCHIVES if goal.status is GoalStatus.EXPIRED else _ACTIVE_ARCHIVES
        if closure_type not in allowed:
            raise GoalTransitionRejected("归档原因与来源状态不符", reason="closure_type_mismatch")
        return closure_type

    expected = _DEFAULT_CLOSURES[target]
    if closure_type is not None and closure_type is not expected:
        raise GoalTransitionRejected("收口原因与目标状态不符", reason="closure_type_mismatch")
    return expected


def _require_aware(moment: datetime) -> None:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("transitioned_at 必须是带时区的时间")
