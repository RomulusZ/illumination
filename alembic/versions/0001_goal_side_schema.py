"""goal side persistence schema

Revision ID: 0001_goal_side
Revises:
Create Date: 2026-11-16

目标侧四表：users / goals / goal_versions / goal_metrics。
对尚未建表的 goal_drafts / intents / interval_rules / unit_registry / metric_library
的引用先保留为普通列，外键在对应节点补。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_goal_side"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

ENUMS: dict[str, tuple[str, ...]] = {
    "goal_intent": ("advance", "maintain"),
    "goal_role": ("primary", "secondary"),
    "goal_status": (
        "planned",
        "active",
        "achieved",
        "archived",
        "abandoned",
        "expired",
        "withdrawn",
        "paused",
    ),
    "baseline_state": ("measured", "estimate", "unknown"),
    "metric_kind": ("numeric", "state", "artifact"),
    "metric_direction": ("up", "down", "hold"),
    "threshold_source": ("user_input", "template_default", "user_confirmed_candidate"),
    "threshold_written_in": ("plan", "replan"),
    "interval_source": ("user_input", "template_default", "rule_inferred", "fallback"),
}


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*ENUMS[name], name=name, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    for name, values in ENUMS.items():
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column(
            "timezone", sa.Text(), server_default=sa.text("'Asia/Shanghai'"), nullable=False
        ),
        sa.Column("day_cutoff", sa.Time(), server_default=sa.text("'04:00'"), nullable=False),
        sa.Column(
            "latest_start_time", sa.Time(), server_default=sa.text("'21:00'"), nullable=False
        ),
        sa.Column(
            "reminder_settings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("reminder_settings_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "goals",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("intent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("intent", _enum("goal_intent"), nullable=False),
        sa.Column("role", _enum("goal_role"), nullable=False),
        sa.Column(
            "status",
            _enum("goal_status"),
            server_default=sa.text("'planned'"),
            nullable=False,
        ),
        sa.Column("progress", sa.Text(), server_default=sa.text("'none'"), nullable=False),
        sa.Column("closure_type", sa.Text(), nullable=True),
        sa.Column("parent_goal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("current_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("name_final", sa.Text(), nullable=False),
        sa.Column(
            "checkin_intensity",
            sa.Text(),
            server_default=sa.text("'medium'"),
            nullable=False,
        ),
        sa.Column("stage_span_days", sa.SmallInteger(), nullable=True),
        sa.Column("reminder_at", sa.Time(), nullable=True),
        sa.Column("plan_start_date", sa.Date(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rev", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("lease_owner", sa.Text(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "NOT (intent = 'maintain' AND role = 'primary')",
            name="goal_maintain_not_primary",
        ),
        sa.CheckConstraint(
            "status <> 'planned' OR plan_start_date IS NOT NULL",
            name="goal_planned_needs_date",
        ),
        sa.CheckConstraint(
            "status NOT IN ('achieved','archived','abandoned','expired') "
            "OR closure_type IS NOT NULL",
            name="goal_closed_needs_type",
        ),
        sa.CheckConstraint(
            "progress IN ('none','on_track','at_risk','partial')",
            name="goal_progress_valid",
        ),
        sa.CheckConstraint(
            "closure_type IS NULL OR closure_type IN "
            "('achieved','partial','archived_at_met','replanned','abandoned',"
            "'expired','split','merged')",
            name="goal_closure_type_valid",
        ),
        sa.CheckConstraint(
            "checkin_intensity IN ('light','medium','heavy')",
            name="goal_checkin_intensity_valid",
        ),
        sa.CheckConstraint(
            "stage_span_days IS NULL OR stage_span_days BETWEEN 14 AND 60",
            name="goal_stage_span_valid",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_goal_id"], ["goals.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("draft_id"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "goal_versions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("goal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("intent", _enum("goal_intent"), nullable=False),
        sa.Column("role", _enum("goal_role"), nullable=False),
        sa.Column("feedback_interval_days", sa.SmallInteger(), nullable=False),
        sa.Column("interval_source", _enum("interval_source"), nullable=False),
        sa.Column("interval_rule_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("deadline_end", sa.Date(), nullable=True),
        sa.Column(
            "constraints",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "constraints_none",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("change_kind", sa.String(length=32), nullable=False),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("template_key", sa.Text(), nullable=True),
        sa.Column("template_version", sa.Integer(), nullable=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(intent = 'advance' AND deadline_end IS NOT NULL) "
            "OR (intent = 'maintain' AND deadline_end IS NULL)",
            name="goal_version_window",
        ),
        sa.CheckConstraint(
            "constraints_none OR constraints <> '{}'::jsonb",
            name="goal_version_constraints",
        ),
        sa.CheckConstraint(
            "feedback_interval_days BETWEEN 1 AND 30",
            name="goal_version_interval_valid",
        ),
        sa.CheckConstraint(
            "change_kind IN ('initial','semantic','intent_change','replan')",
            name="goal_version_change_kind_valid",
        ),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("goal_id", "version_no", name="uq_goal_versions_goal_id_version_no"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "goal_metrics",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("goal_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric_key", sa.String(length=16), nullable=False),
        sa.Column("metric_library_key", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", _enum("metric_kind"), nullable=False),
        sa.Column("unit_code", sa.Text(), nullable=False),
        sa.Column("display_unit_code", sa.Text(), nullable=False),
        sa.Column(
            "direction",
            _enum("metric_direction"),
            server_default=sa.text("'up'"),
            nullable=False,
        ),
        sa.Column("baseline_state", _enum("baseline_state"), nullable=False),
        sa.Column("baseline_value", sa.Numeric(), nullable=True),
        sa.Column("baseline_unit_code", sa.Text(), nullable=True),
        sa.Column("baseline_captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("target_value", sa.Numeric(), nullable=True),
        sa.Column("target_expected_state", sa.Text(), nullable=True),
        sa.Column("threshold_source", _enum("threshold_source"), nullable=True),
        sa.Column("written_in", _enum("threshold_written_in"), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "metric_key IN ('primary','secondary_1','secondary_2')",
            name="goal_metric_slot_valid",
        ),
        sa.CheckConstraint(
            "(baseline_state = 'unknown' AND baseline_value IS NULL) OR "
            "(baseline_state IN ('measured','estimate') AND baseline_value IS NOT NULL "
            "AND baseline_unit_code IS NOT NULL AND baseline_captured_at IS NOT NULL)",
            name="metric_baseline_shape",
        ),
        sa.CheckConstraint(
            "(kind = 'numeric' AND target_value IS NOT NULL "
            "AND target_expected_state IS NULL) OR "
            "(kind IN ('state','artifact') AND target_expected_state IS NOT NULL)",
            name="metric_target_shape",
        ),
        sa.CheckConstraint(
            "(target_value IS NULL AND target_expected_state IS NULL) OR "
            "(threshold_source IS NOT NULL AND written_in IS NOT NULL)",
            name="metric_threshold_provenance",
        ),
        sa.ForeignKeyConstraint(["goal_version_id"], ["goal_versions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("goal_version_id", "metric_key", name="uq_goal_metrics_version_slot"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "uq_goals_one_primary",
        "goals",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("role = 'primary' AND status IN ('planned','active')"),
    )
    op.create_index("idx_goals_user_status", "goals", ["user_id", "status"])
    op.create_index(
        "idx_goals_archive",
        "goals",
        ["user_id", sa.text("closed_at DESC")],
        postgresql_where=sa.text("status = 'archived'"),
    )
    op.create_index(
        "idx_goals_parent",
        "goals",
        ["parent_goal_id"],
        postgresql_where=sa.text("parent_goal_id IS NOT NULL"),
    )
    op.create_index("idx_goal_versions", "goal_versions", ["goal_id", sa.text("version_no DESC")])
    op.create_index(
        "uq_metric_primary",
        "goal_metrics",
        ["goal_version_id"],
        unique=True,
        postgresql_where=sa.text("metric_key = 'primary'"),
    )
    op.create_index("idx_metrics_version", "goal_metrics", ["goal_version_id"])


def downgrade() -> None:
    op.drop_table("goal_metrics")
    op.drop_table("goal_versions")
    op.drop_table("goals")
    op.drop_table("users")

    bind = op.get_bind()
    for name in reversed(list(ENUMS)):
        postgresql.ENUM(name=name).drop(bind, checkfirst=True)
