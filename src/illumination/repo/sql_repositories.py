"""SQLAlchemy 仓储与 UnitOfWork；只服务 PostgreSQL。

服务层只依赖 `types.ports` 的 Protocol，本模块负责领域契约与 ORM 行之间的映射。
写入即 flush，让唯一约束与检查约束在事务内尽早暴露；提交或回滚由调用方决定。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from illumination.repo.models import Action as ActionRow
from illumination.repo.models import Cycle as CycleRow
from illumination.repo.models import Evidence as EvidenceRow
from illumination.repo.models import Goal as GoalRow
from illumination.repo.models import GoalMetric as MetricRow
from illumination.repo.models import GoalVersion as VersionRow
from illumination.repo.models import IdempotencyKey as IdempotencyRow
from illumination.repo.models import Review as ReviewRow
from illumination.repo.models import SyncOp as SyncOpRow
from illumination.types.enums import (
    EvidenceStatus,
    GoalChangeKind,
    GoalRole,
    GoalStatus,
    MetricKey,
    SyncOpOutcome,
    SyncOpType,
)
from illumination.types.execution import (
    Action,
    Cycle,
    Evidence,
    IdempotencyRecord,
    Review,
    SyncOpRecord,
)
from illumination.types.goal import Goal, GoalVersion, Metric
from illumination.types.ports import (
    ActionRepositoryPort,
    CycleRepositoryPort,
    EvidenceRepositoryPort,
    GoalRepositoryPort,
    IdempotencyRepositoryPort,
    ReviewRepositoryPort,
    SyncOpRepositoryPort,
)

_METRIC_ORDER = {key: index for index, key in enumerate(MetricKey)}


def _uuid(value: str) -> uuid.UUID:
    return uuid.UUID(value)


def _maybe_uuid(value: str | None) -> uuid.UUID | None:
    return _uuid(value) if value else None


def _request_key(user_id: str, scope: str, key: str) -> str:
    """把用户与 scope 编进幂等主键，避免跨用户碰撞。"""
    return f"{scope}:{user_id}:{key}"


class SqlGoalRepository:
    """目标身份与版本快照；版本只追加，不提供更新入口。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, *, user_id: str, goal: Goal) -> None:
        self._session.add(
            GoalRow(
                id=_uuid(goal.goal_id),
                user_id=_uuid(user_id),
                intent_id=_maybe_uuid(goal.intent_id),
                intent=goal.intent,
                role=goal.role,
                status=goal.status,
                name_final=goal.name_final,
                current_version=goal.current_version,
                plan_start_date=goal.plan_start_date,
                started_at=goal.started_at,
                closed_at=goal.closed_at,
            )
        )
        self._session.flush()

    def get(self, goal_id: str) -> Goal | None:
        row = self._session.get(GoalRow, _uuid(goal_id))
        return _to_goal(row) if row is not None else None

    def get_owned(self, user_id: str, goal_id: str) -> Goal | None:
        row = self._session.scalars(
            select(GoalRow).where(
                GoalRow.id == _uuid(goal_id),
                GoalRow.user_id == _uuid(user_id),
            )
        ).first()
        return _to_goal(row) if row is not None else None

    def find_active_primary(self, user_id: str) -> Goal | None:
        row = self._session.scalars(
            select(GoalRow)
            .where(
                GoalRow.user_id == _uuid(user_id),
                GoalRow.role == GoalRole.PRIMARY,
                GoalRow.status.in_([GoalStatus.PLANNED, GoalStatus.ACTIVE]),
            )
            .order_by(GoalRow.created_at.desc())
            .limit(1)
        ).first()
        return _to_goal(row) if row is not None else None

    def count_active_by_role(self, user_id: str, role: GoalRole) -> int:
        count = self._session.scalar(
            select(func.count())
            .select_from(GoalRow)
            .where(
                GoalRow.user_id == _uuid(user_id),
                GoalRow.role == role,
                GoalRow.status.in_([GoalStatus.PLANNED, GoalStatus.ACTIVE]),
            )
        )
        return int(count or 0)

    def append_version(self, version: GoalVersion) -> None:
        row, metrics = _version_rows(version)
        self._session.add(row)
        # 先落版本行，保证指标的复合外键在 flush 时已可见。
        self._session.flush()
        self._session.add_all(metrics)
        self._session.flush()

    def get_version(self, goal_id: str, version_no: int) -> GoalVersion | None:
        row = self._session.scalars(
            select(VersionRow).where(
                VersionRow.goal_id == _uuid(goal_id),
                VersionRow.version_no == version_no,
            )
        ).first()
        if row is None:
            return None
        metrics = self._session.scalars(
            select(MetricRow).where(MetricRow.goal_version_id == row.id)
        ).all()
        return _to_version(row, list(metrics))


class SqlCycleRepository:
    """节奏单元；单 active 由数据库部分唯一索引兜底。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, cycle: Cycle) -> None:
        self._session.add(_cycle_row(cycle))
        self._session.flush()

    def get(self, cycle_id: str) -> Cycle | None:
        row = self._session.get(CycleRow, _uuid(cycle_id))
        return _to_cycle(row) if row is not None else None

    def find_active(self, goal_id: str) -> Cycle | None:
        row = self._session.scalars(
            select(CycleRow).where(
                CycleRow.goal_id == _uuid(goal_id),
                CycleRow.state == "active",
            )
        ).first()
        return _to_cycle(row) if row is not None else None

    def save(self, cycle: Cycle) -> None:
        row = self._session.get(CycleRow, _uuid(cycle.cycle_id))
        if row is None:
            raise LookupError(f"cycle {cycle.cycle_id} 不存在")
        row.state = cycle.state
        row.start_at = cycle.start_at
        row.end_at = cycle.end_at
        row.closing_until = cycle.closing_until
        row.budget_minutes = cycle.budget_minutes
        self._session.flush()


class SqlActionRepository:
    """单元内动作；完成时间与状态由检查约束绑定。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, action: Action) -> None:
        self._session.add(_action_row(action))
        self._session.flush()

    def get(self, action_id: str) -> Action | None:
        row = self._session.get(ActionRow, _uuid(action_id))
        return _to_action(row) if row is not None else None

    def save(self, action: Action) -> None:
        row = self._session.get(ActionRow, _uuid(action.action_id))
        if row is None:
            raise LookupError(f"action {action.action_id} 不存在")
        row.status = action.status
        row.completed_at = action.completed_at
        row.budget_state = "deferred" if action.is_deferred else "allocated"
        self._session.flush()


class SqlEvidenceRepository:
    """证据；统计只读取 confirmed。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, evidence: Evidence) -> None:
        self._session.add(_evidence_row(evidence))
        self._session.flush()

    def get(self, evidence_id: str) -> Evidence | None:
        row = self._session.get(EvidenceRow, _uuid(evidence_id))
        return _to_evidence(row) if row is not None else None

    def count_confirmed(self, goal_id: str) -> int:
        count = self._session.scalar(
            select(func.count())
            .select_from(EvidenceRow)
            .where(
                EvidenceRow.goal_id == _uuid(goal_id),
                EvidenceRow.status == EvidenceStatus.CONFIRMED,
            )
        )
        return int(count or 0)


class SqlReviewRepository:
    """周期与阶段评估。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, review: Review) -> None:
        self._session.add(_review_row(review))
        self._session.flush()

    def get_by_cycle(self, cycle_id: str) -> Review | None:
        row = self._session.scalars(
            select(ReviewRow).where(ReviewRow.cycle_id == _uuid(cycle_id))
        ).first()
        return _to_review(row) if row is not None else None


class SqlIdempotencyRepository:
    """在线幂等缓存；主键由 user、scope 与请求键组合，天然跨用户隔离。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def find(self, *, user_id: str, scope: str, key: str) -> IdempotencyRecord | None:
        row = self._session.get(IdempotencyRow, _request_key(user_id, scope, key))
        if row is None or row.user_id is None or str(row.user_id) != user_id:
            return None
        return IdempotencyRecord(
            user_id=user_id,
            scope=row.scope,
            key=key,
            evidence_id=str(row.response_body["evidence_id"]),
            expires_at=row.expires_at,
        )

    def save(
        self,
        *,
        user_id: str,
        scope: str,
        key: str,
        evidence_id: str,
        expires_at: datetime,
    ) -> None:
        self._session.add(
            IdempotencyRow(
                key=_request_key(user_id, scope, key),
                user_id=_uuid(user_id),
                scope=scope,
                response_code=201,
                response_body={"evidence_id": evidence_id},
                expires_at=expires_at,
            )
        )
        self._session.flush()


class SqlSyncOpRepository:
    """离线操作账本；唯一键由数据库兜底。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, *, user_id: str, client_op_id: str) -> SyncOpRecord | None:
        row = self._session.scalars(
            select(SyncOpRow).where(
                SyncOpRow.user_id == _uuid(user_id),
                SyncOpRow.client_op_id == _uuid(client_op_id),
            )
        ).first()
        return _to_sync_op(row) if row is not None else None

    def add(self, record: SyncOpRecord) -> None:
        self._session.add(
            SyncOpRow(
                user_id=_uuid(record.user_id),
                client_op_id=_uuid(record.client_op_id),
                device_id=record.device_id,
                op_type=record.op_type.value,
                captured_at=record.captured_at,
                outcome=record.outcome.value,
                result_ref=_maybe_uuid(record.result_ref),
                error_code=record.error_code,
            )
        )
        self._session.flush()


class SqlUnitOfWork:
    """一次事务内的仓储集合；服务层通过 Protocol 使用。"""

    def __init__(self, session: Session) -> None:
        self._session = session
        self.goals: GoalRepositoryPort = SqlGoalRepository(session)
        self.cycles: CycleRepositoryPort = SqlCycleRepository(session)
        self.actions: ActionRepositoryPort = SqlActionRepository(session)
        self.evidences: EvidenceRepositoryPort = SqlEvidenceRepository(session)
        self.reviews: ReviewRepositoryPort = SqlReviewRepository(session)
        self.idempotency: IdempotencyRepositoryPort = SqlIdempotencyRepository(session)
        self.sync_ops: SyncOpRepositoryPort = SqlSyncOpRepository(session)

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()


def _to_goal(row: GoalRow) -> Goal:
    return Goal(
        goal_id=str(row.id),
        name_final=row.name_final,
        intent=row.intent,
        role=row.role,
        status=row.status,
        intent_id=str(row.intent_id) if row.intent_id else None,
        current_version=row.current_version,
        plan_start_date=row.plan_start_date,
        started_at=row.started_at,
        closed_at=row.closed_at,
    )


def _version_rows(version: GoalVersion) -> tuple[VersionRow, list[MetricRow]]:
    version_id = _uuid(version.version_id) if version.version_id else uuid.uuid4()
    row = VersionRow(
        id=version_id,
        goal_id=_uuid(version.goal_id),
        version_no=version.version_no,
        statement=version.statement,
        intent=version.intent,
        role=version.role,
        feedback_interval_days=version.feedback_interval_days,
        interval_source=version.interval_source,
        deadline_end=version.deadline_end,
        constraints={"items": list(version.constraints)},
        constraints_none=version.has_no_constraints,
        change_kind=version.change_kind.value,
        change_note=version.change_note,
        effective_at=version.effective_at,
    )
    metrics = [_metric_row(version_id, metric) for metric in version.metrics]
    return row, metrics


def _metric_row(version_id: uuid.UUID, metric: Metric) -> MetricRow:
    return MetricRow(
        goal_version_id=version_id,
        metric_key=metric.metric_key.value,
        name=metric.name,
        kind=metric.kind,
        unit_code=metric.unit_code,
        display_unit_code=metric.display_unit_code,
        direction=metric.direction,
        baseline_state=metric.baseline_state,
        baseline_value=metric.baseline_value,
        baseline_unit_code=metric.baseline_unit_code,
        baseline_captured_at=metric.baseline_captured_at,
        target_value=metric.target_value,
        target_expected_state=metric.target_expected_state,
        threshold_source=metric.threshold_source,
        written_in=metric.written_in,
    )


def _to_version(row: VersionRow, metrics: list[MetricRow]) -> GoalVersion:
    ordered = sorted(metrics, key=lambda metric: _METRIC_ORDER[MetricKey(metric.metric_key)])
    constraints: Any = row.constraints
    return GoalVersion(
        version_id=str(row.id),
        goal_id=str(row.goal_id),
        version_no=row.version_no,
        statement=row.statement,
        intent=row.intent,
        role=row.role,
        feedback_interval_days=row.feedback_interval_days,
        interval_source=row.interval_source,
        deadline_end=row.deadline_end,
        constraints=tuple(constraints.get("items", [])),
        has_no_constraints=row.constraints_none,
        change_kind=GoalChangeKind(row.change_kind),
        change_note=row.change_note,
        effective_at=row.effective_at,
        metrics=tuple(_to_metric(metric) for metric in ordered),
    )


def _to_metric(row: MetricRow) -> Metric:
    return Metric(
        metric_key=MetricKey(row.metric_key),
        name=row.name,
        kind=row.kind,
        unit_code=row.unit_code,
        display_unit_code=row.display_unit_code,
        direction=row.direction,
        baseline_state=row.baseline_state,
        baseline_value=float(row.baseline_value) if row.baseline_value is not None else None,
        baseline_unit_code=row.baseline_unit_code,
        baseline_captured_at=row.baseline_captured_at,
        target_value=float(row.target_value) if row.target_value is not None else None,
        target_expected_state=row.target_expected_state,
        threshold_source=row.threshold_source,
        written_in=row.written_in,
    )


def _cycle_row(cycle: Cycle) -> CycleRow:
    return CycleRow(
        id=_uuid(cycle.cycle_id),
        goal_id=_uuid(cycle.goal_id),
        goal_version_id=_uuid(cycle.goal_version_id),
        seq=cycle.seq,
        state=cycle.state,
        start_at=cycle.start_at,
        end_at=cycle.end_at,
        closing_until=cycle.closing_until,
        budget_minutes=cycle.budget_minutes,
    )


def _to_cycle(row: CycleRow) -> Cycle:
    return Cycle(
        cycle_id=str(row.id),
        goal_id=str(row.goal_id),
        goal_version_id=str(row.goal_version_id),
        seq=row.seq,
        state=row.state,
        start_at=row.start_at,
        end_at=row.end_at,
        closing_until=row.closing_until,
        budget_minutes=row.budget_minutes,
    )


def _action_row(action: Action) -> ActionRow:
    return ActionRow(
        id=_uuid(action.action_id),
        cycle_id=_uuid(action.cycle_id),
        goal_id=_uuid(action.goal_id),
        seq=action.seq,
        title=action.title,
        action_type=action.action_type,
        duration_min=action.duration_min,
        metric_key=action.metric_key.value,
        status=action.status,
        budget_state="deferred" if action.is_deferred else "allocated",
        completed_at=action.completed_at,
    )


def _to_action(row: ActionRow) -> Action:
    return Action(
        action_id=str(row.id),
        cycle_id=str(row.cycle_id),
        goal_id=str(row.goal_id),
        seq=row.seq,
        title=row.title,
        metric_key=MetricKey(row.metric_key),
        action_type=row.action_type,
        duration_min=row.duration_min,
        is_deferred=row.budget_state == "deferred",
        status=row.status,
        completed_at=row.completed_at,
    )


def _evidence_row(evidence: Evidence) -> EvidenceRow:
    return EvidenceRow(
        id=_uuid(evidence.evidence_id),
        goal_id=_uuid(evidence.goal_id),
        cycle_id=_maybe_uuid(evidence.cycle_id),
        action_id=_maybe_uuid(evidence.action_id),
        metric_key=evidence.metric_key.value,
        mode=evidence.mode,
        source_channel=evidence.source_channel,
        status=evidence.status,
        confirmed_at=evidence.confirmed_at,
        expires_at=evidence.expires_at,
        source_device=evidence.source_device,
        source_ref=({"external_id": evidence.external_id} if evidence.external_id else {}),
        entered_value=evidence.entered_value,
        entered_unit=evidence.entered_unit,
        value_calc=evidence.value_calc,
        unit_code=evidence.unit_code,
        display_unit_code=evidence.display_unit_code,
        note=evidence.note,
        captured_at=evidence.captured_at,
        day_cutoff_date=evidence.day_cutoff_date,
    )


def _to_evidence(row: EvidenceRow) -> Evidence:
    return Evidence(
        evidence_id=str(row.id),
        goal_id=str(row.goal_id),
        cycle_id=str(row.cycle_id) if row.cycle_id else None,
        action_id=str(row.action_id) if row.action_id else None,
        metric_key=MetricKey(row.metric_key),
        mode=row.mode,
        source_channel=row.source_channel,
        status=row.status,
        confirmed_at=row.confirmed_at,
        expires_at=row.expires_at,
        source_device=row.source_device,
        external_id=row.source_ref.get("external_id"),
        entered_value=float(row.entered_value) if row.entered_value is not None else None,
        entered_unit=row.entered_unit,
        value_calc=float(row.value_calc) if row.value_calc is not None else None,
        unit_code=row.unit_code,
        display_unit_code=row.display_unit_code,
        note=row.note,
        captured_at=row.captured_at,
        day_cutoff_date=row.day_cutoff_date,
    )


def _review_row(review: Review) -> ReviewRow:
    return ReviewRow(
        id=_uuid(review.review_id),
        goal_id=_uuid(review.goal_id),
        cycle_id=_maybe_uuid(review.cycle_id),
        scope=review.scope,
        cycle_result=review.cycle_result,
        milestone_reached=review.milestone_reached,
        gap_bucket=review.gap_bucket,
        gap_note=review.gap_note,
        stage_index=review.stage_index,
        window_start=review.window_start,
        window_end=review.window_end,
        decision=review.decision,
        summary=review.summary,
        deferred=review.is_deferred,
        override_reason=review.override_reason,
    )


def _to_review(row: ReviewRow) -> Review:
    return Review(
        review_id=str(row.id),
        goal_id=str(row.goal_id),
        scope=row.scope,
        cycle_id=str(row.cycle_id) if row.cycle_id else None,
        cycle_result=row.cycle_result,
        stage_index=row.stage_index,
        window_start=row.window_start,
        window_end=row.window_end,
        decision=row.decision,
        gap_bucket=row.gap_bucket,
        gap_note=row.gap_note,
        milestone_reached=row.milestone_reached,
        summary=row.summary,
        is_deferred=row.deferred,
        override_reason=row.override_reason,
    )


def _to_sync_op(row: SyncOpRow) -> SyncOpRecord:
    return SyncOpRecord(
        user_id=str(row.user_id),
        client_op_id=str(row.client_op_id),
        device_id=row.device_id,
        op_type=SyncOpType(row.op_type),
        captured_at=row.captured_at,
        outcome=SyncOpOutcome(row.outcome),
        result_ref=str(row.result_ref) if row.result_ref else None,
        error_code=row.error_code,
    )
