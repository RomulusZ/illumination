"""execution and idempotency schema

Revision ID: 0002_execution
Revises: 0001_goal_side
Create Date: 2026-11-16

执行侧六表：cycles / actions / evidences / reviews / idempotency_keys / sync_ops。
离线操作账本按设计使用 `sync_ops`（todo 中的 client_ops），以 schema_v0.1.sql 为准。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_execution"
down_revision: str | None = "0001_goal_side"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

ENUMS: dict[str, tuple[str, ...]] = {
    "cycle_state": ("frozen", "active", "closing", "reviewed"),
    "action_type": ("once", "recurring"),
    "action_status": ("pending", "done", "skipped", "failed"),
    "cycle_result": ("met", "not_met", "insufficient_data", "aborted"),
    "review_scope": ("cycle", "stage"),
    "stage_decision": (
        "continue",
        "adjust_plan",
        "renegotiate",
        "replan",
        "close",
        "continue_maintain",
        "adjust_standard",
        "stop_maintain",
    ),
    "evidence_mode": ("normal", "log_only"),
    "evidence_channel": ("manual", "device"),
    "evidence_status": ("confirmed", "candidate"),
}


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*ENUMS[name], name=name, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    for name, values in ENUMS.items():
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    op.create_table(
        "cycles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("goal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("goal_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column(
            "state",
            _enum("cycle_state"),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closing_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("budget_minutes", sa.Integer(), nullable=True),
        sa.Column("frozen_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unfrozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("freeze_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("end_at > start_at", name="cycle_window"),
        sa.CheckConstraint(
            "state <> 'closing' OR closing_until IS NOT NULL",
            name="cycle_closing_window",
        ),
        sa.CheckConstraint(
            "budget_minutes IS NULL OR budget_minutes >= 0",
            name="cycle_budget_valid",
        ),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["goal_version_id"], ["goal_versions.id"]),
        sa.UniqueConstraint("goal_id", "seq", name="uq_cycles_goal_id_seq"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "actions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("cycle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("goal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "action_type",
            _enum("action_type"),
            server_default=sa.text("'once'"),
            nullable=False,
        ),
        sa.Column(
            "dose",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("duration_min", sa.Integer(), nullable=False),
        sa.Column(
            "metric_key",
            sa.String(length=16),
            server_default=sa.text("'primary'"),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum("action_status"),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "budget_state",
            sa.Text(),
            server_default=sa.text("'allocated'"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rev", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status IN ('done','failed')) = (completed_at IS NOT NULL)",
            name="action_completed_at",
        ),
        sa.CheckConstraint(
            "budget_state IN ('allocated','deferred')",
            name="action_budget_state_valid",
        ),
        sa.ForeignKeyConstraint(["cycle_id"], ["cycles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("cycle_id", "seq", name="uq_actions_cycle_id_seq"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "evidences",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("goal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cycle_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("metric_key", sa.String(length=16), nullable=False),
        sa.Column(
            "mode",
            _enum("evidence_mode"),
            server_default=sa.text("'normal'"),
            nullable=False,
        ),
        sa.Column(
            "source_channel",
            _enum("evidence_channel"),
            server_default=sa.text("'manual'"),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum("evidence_status"),
            server_default=sa.text("'confirmed'"),
            nullable=False,
        ),
        sa.Column(
            "confirmed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_device", sa.Text(), nullable=True),
        sa.Column(
            "source_ref",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("entered_value", sa.Numeric(), nullable=True),
        sa.Column("entered_unit", sa.Text(), nullable=True),
        sa.Column("value_calc", sa.Numeric(), nullable=True),
        sa.Column("unit_code", sa.Text(), nullable=True),
        sa.Column("display_unit_code", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "artifacts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "range_ack",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("rev", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "revisions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("day_cutoff_date", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(entered_value IS NULL AND value_calc IS NULL) OR "
            "(entered_value IS NOT NULL AND value_calc IS NOT NULL "
            "AND entered_unit IS NOT NULL AND unit_code IS NOT NULL)",
            name="evidence_numeric_shape",
        ),
        sa.CheckConstraint(
            "(status = 'confirmed') = (confirmed_at IS NOT NULL) "
            "AND (status = 'candidate') = (expires_at IS NOT NULL) "
            "AND (status <> 'candidate' OR source_channel = 'device')",
            name="evidence_status_shape",
        ),
        sa.CheckConstraint(
            "source_channel <> 'device' OR (source_device IS NOT NULL "
            "AND (status = 'confirmed' OR source_ref ? 'external_id'))",
            name="evidence_device_shape",
        ),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cycle_id"], ["cycles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["action_id"], ["actions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "reviews",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("goal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cycle_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("goal_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scope", _enum("review_scope"), nullable=False),
        sa.Column("cycle_result", _enum("cycle_result"), nullable=True),
        sa.Column(
            "milestone_reached",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("gap_bucket", sa.SmallInteger(), nullable=True),
        sa.Column("gap_note", sa.Text(), nullable=True),
        sa.Column("stage_index", sa.Integer(), nullable=True),
        sa.Column("window_start", sa.Date(), nullable=True),
        sa.Column("window_end", sa.Date(), nullable=True),
        sa.Column("stage_trigger", sa.Text(), nullable=True),
        sa.Column("decision", _enum("stage_decision"), nullable=True),
        sa.Column(
            "next_expectation",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("rubric", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "deferred",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column("mood_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "checks",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "gap_bucket IS NULL OR gap_bucket BETWEEN 0 AND 3",
            name="review_gap_bucket",
        ),
        sa.CheckConstraint(
            "stage_trigger IS NULL OR stage_trigger IN ('milestone','cycle_count','deadline')",
            name="review_stage_trigger_valid",
        ),
        sa.CheckConstraint(
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
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cycle_id"], ["cycles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["goal_version_id"], ["goal_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("response_code", sa.Integer(), nullable=False),
        sa.Column(
            "response_body",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("key"),
    )

    op.create_table(
        "sync_ops",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_op_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", sa.Text(), nullable=False),
        sa.Column("op_type", sa.Text(), nullable=False),
        sa.Column("base_rev", sa.Integer(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "outcome",
            sa.Text(),
            server_default=sa.text("'applied'"),
            nullable=False,
        ),
        sa.Column("result_ref", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "op_type IN ('submit_evidence','complete_action')",
            name="sync_op_type_valid",
        ),
        sa.CheckConstraint(
            "outcome IN ('applied','duplicate','rejected')",
            name="sync_op_outcome_valid",
        ),
        sa.CheckConstraint(
            "(op_type <> 'submit_evidence' OR captured_at IS NOT NULL) "
            "AND (outcome <> 'applied' OR result_ref IS NOT NULL) "
            "AND (outcome <> 'rejected' OR error_code IS NOT NULL)",
            name="sync_op_shape",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "client_op_id", name="uq_sync_ops_user_client_op"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "uq_cycle_single_active",
        "cycles",
        ["goal_id"],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
    )
    op.create_index("idx_cycles_goal", "cycles", ["goal_id", sa.text("seq DESC")])
    op.create_index("idx_cycles_state", "cycles", ["state", "end_at"])
    op.create_index("idx_actions_cycle", "actions", ["cycle_id", "status"])
    op.create_index(
        "idx_evidence_goal_time",
        "evidences",
        ["goal_id", sa.text("captured_at DESC")],
    )
    op.create_index("idx_evidence_cycle", "evidences", ["cycle_id"])
    op.create_index(
        "idx_evidence_log_only",
        "evidences",
        ["goal_id", sa.text("captured_at DESC")],
        postgresql_where=sa.text("mode = 'log_only'"),
    )
    op.create_index(
        "idx_evidence_confirmed",
        "evidences",
        ["goal_id", sa.text("captured_at DESC")],
        postgresql_where=sa.text("status = 'confirmed'"),
    )
    op.create_index(
        "idx_evidence_candidate",
        "evidences",
        ["expires_at"],
        postgresql_where=sa.text("status = 'candidate'"),
    )
    op.create_index(
        "uq_evidence_device_external",
        "evidences",
        [sa.text("(source_ref ->> 'external_id')")],
        unique=True,
        postgresql_where=sa.text(
            "source_channel = 'device' AND (source_ref ->> 'external_id') IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_review_per_cycle",
        "reviews",
        ["cycle_id"],
        unique=True,
        postgresql_where=sa.text("scope = 'cycle'"),
    )
    op.create_index(
        "uq_review_stage",
        "reviews",
        ["goal_id", "stage_index"],
        unique=True,
        postgresql_where=sa.text("scope = 'stage'"),
    )
    op.create_index("idx_reviews_goal", "reviews", ["goal_id", sa.text("created_at DESC")])
    op.create_index(
        "idx_sync_ops_user_time",
        "sync_ops",
        ["user_id", sa.text("received_at DESC")],
    )


def downgrade() -> None:
    op.drop_table("sync_ops")
    op.drop_table("idempotency_keys")
    op.drop_table("reviews")
    op.drop_table("evidences")
    op.drop_table("actions")
    op.drop_table("cycles")

    bind = op.get_bind()
    for name in reversed(list(ENUMS)):
        postgresql.ENUM(name=name).drop(bind, checkfirst=True)
