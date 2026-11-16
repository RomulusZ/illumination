"""目标侧持久化模型：users / goals / goal_versions / goal_metrics。

列名、枚举与检查约束对齐 `db/schema_v0.1.sql`。P0 只落地目标侧四表；对尚未建表的
`goal_drafts`、`intents`、`interval_rules`、`unit_registry`、`metric_library` 的引用
先保留为普通列，外键在对应节点建表后再补，避免首个迁移依赖不存在的表。
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
    BaselineState,
    GoalIntent,
    GoalRole,
    GoalStatus,
    IntervalSource,
    MetricDirection,
    MetricKind,
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
