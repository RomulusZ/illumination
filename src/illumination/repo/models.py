"""目标侧与执行侧持久化模型。

列名、枚举与检查约束对齐 `db/schema_v0.1.sql`。已落地 users / goals /
goal_versions / goal_metrics 以及 cycles / actions / evidences / reviews /
idempotency_keys / sync_ops。对尚未建表的 `goal_drafts`、`intents`、
`interval_rules`、`unit_registry`、`metric_library` 的引用先保留为普通列，
外键在对应节点建表后再补，避免迁移依赖不存在的表。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from illumination.types.enums import (
    ActionStatus,
    ActionType,
    BaselineState,
    CycleResult,
    CycleState,
    EvidenceChannel,
    EvidenceMode,
    EvidenceStatus,
    GoalIntent,
    GoalRole,
    GoalStatus,
    IntervalSource,
    MetricDirection,
    MetricKind,
    ReviewScope,
    StageDecision,
    ThresholdSource,
    ThresholdWriteWindow,
)


class Base(DeclarativeBase):
    """所有持久化模型的声明基类。"""


def _enum(enum_class: type[StrEnum], name: str) -> SAEnum:
    """PostgreSQL 原生枚举，落库值使用成员值而非成员名。"""
    return SAEnum(
        enum_class,
        name=name,
        native_enum=True,
        values_callable=lambda members: [member.value for member in members],
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    display_name: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'Asia/Shanghai'")
    )
    day_cutoff: Mapped[time] = mapped_column(Time, nullable=False, server_default=text("'04:00'"))
    latest_start_time: Mapped[time] = mapped_column(
        Time, nullable=False, server_default=text("'21:00'")
    )
    reminder_settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    reminder_settings_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Goal(Base):
    __tablename__ = "goals"
    __table_args__ = (
        CheckConstraint(
            "NOT (intent = 'maintain' AND role = 'primary')",
            name="goal_maintain_not_primary",
        ),
        CheckConstraint(
            "status <> 'planned' OR plan_start_date IS NOT NULL",
            name="goal_planned_needs_date",
        ),
        CheckConstraint(
            "status NOT IN ('achieved','archived','abandoned','expired') "
            "OR closure_type IS NOT NULL",
            name="goal_closed_needs_type",
        ),
        CheckConstraint(
            "progress IN ('none','on_track','at_risk','partial')",
            name="goal_progress_valid",
        ),
        CheckConstraint(
            "closure_type IS NULL OR closure_type IN "
            "('achieved','partial','archived_at_met','replanned','abandoned',"
            "'expired','split','merged')",
            name="goal_closure_type_valid",
        ),
        CheckConstraint(
            "checkin_intensity IN ('light','medium','heavy')",
            name="goal_checkin_intensity_valid",
        ),
        CheckConstraint(
            "stage_span_days IS NULL OR stage_span_days BETWEEN 14 AND 60",
            name="goal_stage_span_valid",
        ),
        Index(
            "uq_goals_one_primary",
            "user_id",
            unique=True,
            postgresql_where=text("role = 'primary' AND status IN ('planned','active')"),
        ),
        Index("idx_goals_user_status", "user_id", "status"),
        Index(
            "idx_goals_archive",
            "user_id",
            text("closed_at DESC"),
            postgresql_where=text("status = 'archived'"),
        ),
        Index(
            "idx_goals_parent",
            "parent_goal_id",
            postgresql_where=text("parent_goal_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    draft_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), unique=True)
    intent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    intent: Mapped[GoalIntent] = mapped_column(_enum(GoalIntent, "goal_intent"), nullable=False)
    role: Mapped[GoalRole] = mapped_column(_enum(GoalRole, "goal_role"), nullable=False)
    status: Mapped[GoalStatus] = mapped_column(
        _enum(GoalStatus, "goal_status"), nullable=False, server_default=text("'planned'")
    )
    progress: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'none'"))
    closure_type: Mapped[str | None] = mapped_column(Text)
    parent_goal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goals.id", ondelete="SET NULL")
    )
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    name_final: Mapped[str] = mapped_column(Text, nullable=False)
    checkin_intensity: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'medium'")
    )
    stage_span_days: Mapped[int | None] = mapped_column(SmallInteger)
    reminder_at: Mapped[time | None] = mapped_column(Time)
    plan_start_date: Mapped[date | None] = mapped_column(Date)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rev: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GoalVersion(Base):
    __tablename__ = "goal_versions"
    __table_args__ = (
        CheckConstraint(
            "(intent = 'advance' AND deadline_end IS NOT NULL) "
            "OR (intent = 'maintain' AND deadline_end IS NULL)",
            name="goal_version_window",
        ),
        CheckConstraint(
            "constraints_none OR constraints <> '{}'::jsonb",
            name="goal_version_constraints",
        ),
        CheckConstraint(
            "feedback_interval_days BETWEEN 1 AND 30",
            name="goal_version_interval_valid",
        ),
        CheckConstraint(
            "change_kind IN ('initial','semantic','intent_change','replan')",
            name="goal_version_change_kind_valid",
        ),
        UniqueConstraint("goal_id", "version_no", name="uq_goal_versions_goal_id_version_no"),
        Index("idx_goal_versions", "goal_id", text("version_no DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    goal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goals.id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[GoalIntent] = mapped_column(_enum(GoalIntent, "goal_intent"), nullable=False)
    role: Mapped[GoalRole] = mapped_column(_enum(GoalRole, "goal_role"), nullable=False)
    feedback_interval_days: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    interval_source: Mapped[IntervalSource] = mapped_column(
        _enum(IntervalSource, "interval_source"), nullable=False
    )
    interval_rule_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    deadline_end: Mapped[date | None] = mapped_column(Date)
    constraints: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    constraints_none: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    change_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    change_reason: Mapped[str | None] = mapped_column(Text)
    change_note: Mapped[str | None] = mapped_column(Text)
    template_key: Mapped[str | None] = mapped_column(Text)
    template_version: Mapped[int | None] = mapped_column(Integer)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GoalMetric(Base):
    __tablename__ = "goal_metrics"
    __table_args__ = (
        CheckConstraint(
            "metric_key IN ('primary','secondary_1','secondary_2')",
            name="goal_metric_slot_valid",
        ),
        CheckConstraint(
            "(baseline_state = 'unknown' AND baseline_value IS NULL) OR "
            "(baseline_state IN ('measured','estimate') AND baseline_value IS NOT NULL "
            "AND baseline_unit_code IS NOT NULL AND baseline_captured_at IS NOT NULL)",
            name="metric_baseline_shape",
        ),
        CheckConstraint(
            "(kind = 'numeric' AND target_value IS NOT NULL "
            "AND target_expected_state IS NULL) OR "
            "(kind IN ('state','artifact') AND target_expected_state IS NOT NULL)",
            name="metric_target_shape",
        ),
        CheckConstraint(
            "(target_value IS NULL AND target_expected_state IS NULL) OR "
            "(threshold_source IS NOT NULL AND written_in IS NOT NULL)",
            name="metric_threshold_provenance",
        ),
        UniqueConstraint("goal_version_id", "metric_key", name="uq_goal_metrics_version_slot"),
        Index(
            "uq_metric_primary",
            "goal_version_id",
            unique=True,
            postgresql_where=text("metric_key = 'primary'"),
        ),
        Index("idx_metrics_version", "goal_version_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    goal_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("goal_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    metric_key: Mapped[str] = mapped_column(String(16), nullable=False)
    metric_library_key: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[MetricKind] = mapped_column(_enum(MetricKind, "metric_kind"), nullable=False)
    unit_code: Mapped[str] = mapped_column(Text, nullable=False)
    display_unit_code: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[MetricDirection] = mapped_column(
        _enum(MetricDirection, "metric_direction"),
        nullable=False,
        server_default=text("'up'"),
    )
    baseline_state: Mapped[BaselineState] = mapped_column(
        _enum(BaselineState, "baseline_state"), nullable=False
    )
    baseline_value: Mapped[Decimal | None] = mapped_column(Numeric)
    baseline_unit_code: Mapped[str | None] = mapped_column(Text)
    baseline_captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    target_value: Mapped[Decimal | None] = mapped_column(Numeric)
    target_expected_state: Mapped[str | None] = mapped_column(Text)
    threshold_source: Mapped[ThresholdSource | None] = mapped_column(
        _enum(ThresholdSource, "threshold_source")
    )
    written_in: Mapped[ThresholdWriteWindow | None] = mapped_column(
        _enum(ThresholdWriteWindow, "threshold_written_in")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Cycle(Base):
    __tablename__ = "cycles"
    __table_args__ = (
        CheckConstraint("end_at > start_at", name="cycle_window"),
        CheckConstraint(
            "state <> 'closing' OR closing_until IS NOT NULL",
            name="cycle_closing_window",
        ),
        CheckConstraint(
            "budget_minutes IS NULL OR budget_minutes >= 0", name="cycle_budget_valid"
        ),
        UniqueConstraint("goal_id", "seq", name="uq_cycles_goal_id_seq"),
        # 同一目标同一时刻只能有一个 active 单元；closing 不纳入唯一键。
        Index(
            "uq_cycle_single_active",
            "goal_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
        Index("idx_cycles_goal", "goal_id", text("seq DESC")),
        Index("idx_cycles_state", "state", "end_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    goal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goals.id", ondelete="CASCADE"), nullable=False
    )
    goal_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goal_versions.id"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[CycleState] = mapped_column(
        _enum(CycleState, "cycle_state"), nullable=False, server_default=text("'active'")
    )
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closing_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    budget_minutes: Mapped[int | None] = mapped_column(Integer)
    frozen_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unfrozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    freeze_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Action(Base):
    __tablename__ = "actions"
    __table_args__ = (
        CheckConstraint(
            "(status IN ('done','failed')) = (completed_at IS NOT NULL)",
            name="action_completed_at",
        ),
        CheckConstraint(
            "budget_state IN ('allocated','deferred')",
            name="action_budget_state_valid",
        ),
        UniqueConstraint("cycle_id", "seq", name="uq_actions_cycle_id_seq"),
        Index("idx_actions_cycle", "cycle_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    cycle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cycles.id", ondelete="CASCADE"), nullable=False
    )
    goal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goals.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    action_type: Mapped[ActionType] = mapped_column(
        _enum(ActionType, "action_type"), nullable=False, server_default=text("'once'")
    )
    dose: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    metric_key: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'primary'")
    )
    status: Mapped[ActionStatus] = mapped_column(
        _enum(ActionStatus, "action_status"), nullable=False, server_default=text("'pending'")
    )
    budget_state: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'allocated'")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rev: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Evidence(Base):
    __tablename__ = "evidences"
    __table_args__ = (
        CheckConstraint(
            "(entered_value IS NULL AND value_calc IS NULL) OR "
            "(entered_value IS NOT NULL AND value_calc IS NOT NULL "
            "AND entered_unit IS NOT NULL AND unit_code IS NOT NULL)",
            name="evidence_numeric_shape",
        ),
        CheckConstraint(
            "(status = 'confirmed') = (confirmed_at IS NOT NULL) "
            "AND (status = 'candidate') = (expires_at IS NOT NULL) "
            "AND (status <> 'candidate' OR source_channel = 'device')",
            name="evidence_status_shape",
        ),
        CheckConstraint(
            "source_channel <> 'device' OR (source_device IS NOT NULL "
            "AND (status = 'confirmed' OR source_ref ? 'external_id'))",
            name="evidence_device_shape",
        ),
        Index("idx_evidence_goal_time", "goal_id", text("captured_at DESC")),
        Index("idx_evidence_cycle", "cycle_id"),
        Index(
            "idx_evidence_log_only",
            "goal_id",
            text("captured_at DESC"),
            postgresql_where=text("mode = 'log_only'"),
        ),
        Index(
            "idx_evidence_confirmed",
            "goal_id",
            text("captured_at DESC"),
            postgresql_where=text("status = 'confirmed'"),
        ),
        Index(
            "idx_evidence_candidate",
            "expires_at",
            postgresql_where=text("status = 'candidate'"),
        ),
        Index(
            "uq_evidence_device_external",
            text("(source_ref ->> 'external_id')"),
            unique=True,
            postgresql_where=text(
                "source_channel = 'device' AND (source_ref ->> 'external_id') IS NOT NULL"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    goal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goals.id", ondelete="CASCADE"), nullable=False
    )
    cycle_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cycles.id", ondelete="SET NULL")
    )
    action_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("actions.id", ondelete="SET NULL")
    )
    metric_key: Mapped[str] = mapped_column(String(16), nullable=False)
    mode: Mapped[EvidenceMode] = mapped_column(
        _enum(EvidenceMode, "evidence_mode"), nullable=False, server_default=text("'normal'")
    )
    source_channel: Mapped[EvidenceChannel] = mapped_column(
        _enum(EvidenceChannel, "evidence_channel"),
        nullable=False,
        server_default=text("'manual'"),
    )
    status: Mapped[EvidenceStatus] = mapped_column(
        _enum(EvidenceStatus, "evidence_status"),
        nullable=False,
        server_default=text("'confirmed'"),
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_device: Mapped[str | None] = mapped_column(Text)
    source_ref: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    entered_value: Mapped[Decimal | None] = mapped_column(Numeric)
    entered_unit: Mapped[str | None] = mapped_column(Text)
    value_calc: Mapped[Decimal | None] = mapped_column(Numeric)
    unit_code: Mapped[str | None] = mapped_column(Text)
    display_unit_code: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    artifacts: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    range_ack: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    rev: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    revisions: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    day_cutoff_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint(
            "gap_bucket IS NULL OR gap_bucket BETWEEN 0 AND 3", name="review_gap_bucket"
        ),
        CheckConstraint(
            "stage_trigger IS NULL OR stage_trigger IN ('milestone','cycle_count','deadline')",
            name="review_stage_trigger_valid",
        ),
        CheckConstraint(
            "(scope = 'cycle' "
            "AND cycle_id IS NOT NULL AND cycle_result IS NOT NULL "
            "AND stage_index IS NULL AND decision IS NULL AND deferred = false) OR "
            "(scope = 'stage' "
            "AND cycle_id IS NULL AND stage_index IS NOT NULL "
            "AND window_start IS NOT NULL AND window_end IS NOT NULL "
            "AND ((deferred = false AND decision IS NOT NULL) "
            "OR (deferred = true AND decision IS NULL AND next_expectation IS NULL)))",
            name="review_shape",
        ),
        Index(
            "uq_review_per_cycle",
            "cycle_id",
            unique=True,
            postgresql_where=text("scope = 'cycle'"),
        ),
        Index(
            "uq_review_stage",
            "goal_id",
            "stage_index",
            unique=True,
            postgresql_where=text("scope = 'stage'"),
        ),
        Index("idx_reviews_goal", "goal_id", text("created_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    goal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goals.id", ondelete="CASCADE"), nullable=False
    )
    cycle_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cycles.id", ondelete="CASCADE")
    )
    goal_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goal_versions.id")
    )
    scope: Mapped[ReviewScope] = mapped_column(_enum(ReviewScope, "review_scope"), nullable=False)
    cycle_result: Mapped[CycleResult | None] = mapped_column(_enum(CycleResult, "cycle_result"))
    milestone_reached: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    gap_bucket: Mapped[int | None] = mapped_column(SmallInteger)
    gap_note: Mapped[str | None] = mapped_column(Text)
    stage_index: Mapped[int | None] = mapped_column(Integer)
    window_start: Mapped[date | None] = mapped_column(Date)
    window_end: Mapped[date | None] = mapped_column(Date)
    stage_trigger: Mapped[str | None] = mapped_column(Text)
    decision: Mapped[StageDecision | None] = mapped_column(_enum(StageDecision, "stage_decision"))
    next_expectation: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    rubric: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    summary: Mapped[str | None] = mapped_column(Text)
    deferred: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    override_reason: Mapped[str | None] = mapped_column(Text)
    mood_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    checks: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IdempotencyKey(Base):
    """在线请求的短 TTL 重放缓存；与离线 sync_ops 分工，避免两份真相。"""

    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    response_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SyncOp(Base):
    """离线补传的操作账本；按 (user_id, client_op_id) 去重并保留裁决结果。"""

    __tablename__ = "sync_ops"
    __table_args__ = (
        CheckConstraint(
            "op_type IN ('submit_evidence','complete_action')",
            name="sync_op_type_valid",
        ),
        CheckConstraint(
            "outcome IN ('applied','duplicate','rejected')",
            name="sync_op_outcome_valid",
        ),
        CheckConstraint(
            "(op_type <> 'submit_evidence' OR captured_at IS NOT NULL) "
            "AND (outcome <> 'applied' OR result_ref IS NOT NULL) "
            "AND (outcome <> 'rejected' OR error_code IS NOT NULL)",
            name="sync_op_shape",
        ),
        UniqueConstraint("user_id", "client_op_id", name="uq_sync_ops_user_client_op"),
        Index("idx_sync_ops_user_time", "user_id", text("received_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    client_op_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    device_id: Mapped[str] = mapped_column(Text, nullable=False)
    op_type: Mapped[str] = mapped_column(Text, nullable=False)
    base_rev: Mapped[int | None] = mapped_column(Integer)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    outcome: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'applied'"))
    result_ref: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    error_code: Mapped[str | None] = mapped_column(Text)
