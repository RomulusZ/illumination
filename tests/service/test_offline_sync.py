"""离线补传的白名单、幂等、时钟与逐字段裁决。"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from illumination.repo.models import Evidence as EvidenceRow
from illumination.repo.models import SyncOp as SyncOpRow
from illumination.repo.models import User
from illumination.repo.sql_repositories import SqlUnitOfWork
from illumination.service.action_service import plan_action
from illumination.service.cycle_service import activate_cycle, plan_cycle
from illumination.service.offline_sync import (
    FieldDecision,
    OfflineOperation,
    OfflineOperationResult,
    sync_offline,
)
from illumination.types.enums import (
    ActionStatus,
    BaselineState,
    EvidenceChannel,
    EvidenceStatus,
    GoalChangeKind,
    GoalIntent,
    GoalRole,
    GoalStatus,
    IntervalSource,
    MetricKey,
    MetricKind,
    SyncOpOutcome,
    ThresholdSource,
    ThresholdWriteWindow,
)
from illumination.types.execution import Cycle, EvidenceCreate
from illumination.types.goal import Goal, GoalVersion, Metric

TIMEZONE = "Asia/Shanghai"
DAY_CUTOFF = time(4, 0)
RECEIVED_AT = datetime(2026, 11, 16, 6, 0, tzinfo=UTC)
CAPTURED_AT = RECEIVED_AT - timedelta(minutes=5)
START = datetime(2026, 11, 16, 4, 0, tzinfo=UTC)
END = START + timedelta(days=7)


def _seed_cycle(session: Session) -> tuple[User, Goal, Cycle, SqlUnitOfWork]:
    user = User(id=uuid.uuid4(), display_name="tester")
    session.add(user)
    session.flush()
    goal = Goal(
        goal_id=str(uuid.uuid4()),
        name_final="每周跑步三次",
        intent=GoalIntent.ADVANCE,
        role=GoalRole.PRIMARY,
        status=GoalStatus.ACTIVE,
        started_at=START,
    )
    version = GoalVersion(
        version_id=str(uuid.uuid4()),
        goal_id=goal.goal_id,
        version_no=1,
        statement="每周跑步三次，每次 30 分钟",
        intent=GoalIntent.ADVANCE,
        role=GoalRole.PRIMARY,
        feedback_interval_days=7,
        interval_source=IntervalSource.USER_INPUT,
        deadline_end=date(2027, 2, 16),
        has_no_constraints=True,
        change_kind=GoalChangeKind.INITIAL,
        effective_at=START,
        metrics=(
            Metric(
                metric_key=MetricKey.PRIMARY,
                name="每周跑步次数",
                kind=MetricKind.NUMERIC,
                unit_code="count",
                display_unit_code="count",
                baseline_state=BaselineState.UNKNOWN,
                target_value=3,
                threshold_source=ThresholdSource.USER_INPUT,
                written_in=ThresholdWriteWindow.PLAN,
            ),
        ),
    )
    uow = SqlUnitOfWork(session)
    uow.goals.add(user_id=str(user.id), goal=goal)
    uow.goals.append_version(version)
    uow.commit()
    cycle = plan_cycle(
        uow,
        goal_id=goal.goal_id,
        goal_version_id=version.version_id,
        seq=1,
        start_at=START,
        end_at=END,
    )
    cycle = activate_cycle(uow, cycle_id=cycle.cycle_id, activated_at=START)
    return user, goal, cycle, uow


def _evidence(goal_id: str, **overrides: object) -> EvidenceCreate:
    values: dict[str, object] = {
        "goal_id": goal_id,
        "metric_key": MetricKey.PRIMARY,
        "captured_at": CAPTURED_AT,
        "source_channel": EvidenceChannel.MANUAL,
        "status": EvidenceStatus.CONFIRMED,
        "confirmed_at": CAPTURED_AT,
        "entered_value": 1.0,
        "entered_unit": "count",
        "value_calc": 1.0,
        "unit_code": "count",
        "display_unit_code": "次",
    }
    values.update(overrides)
    return EvidenceCreate(**values)  # type: ignore[arg-type]


def _operation(
    *,
    op_type: str = "submit_evidence",
    client_op_id: str | None = None,
    captured_at: datetime = CAPTURED_AT,
    evidence: EvidenceCreate | None = None,
    action_id: str | None = None,
    field: str | None = None,
    value: object = None,
) -> OfflineOperation:
    return OfflineOperation(
        op_type=op_type,
        client_op_id=client_op_id or str(uuid.uuid4()),
        device_id="phone-1",
        captured_at=captured_at,
        base_rev=1,
        evidence=evidence,
        action_id=action_id,
        field=field,
        value=value,
    )


def _sync(
    uow: SqlUnitOfWork,
    user_id: str,
    operations: list[OfflineOperation],
    *,
    strategy: str = "clamp_and_warn",
) -> list[OfflineOperationResult]:
    return list(
        sync_offline(
            uow,
            user_id=user_id,
            operations=operations,
            received_at=RECEIVED_AT,
            timezone=TIMEZONE,
            day_cutoff=DAY_CUTOFF,
            skew_strategy=strategy,  # type: ignore[arg-type]
            skew_limit_minutes=10,
        ).results
    )


def _decision(result: OfflineOperationResult, field: str) -> FieldDecision:
    return next(item for item in result.decisions if item.field == field)


def test_submit_evidence_and_complete_action_are_accepted(session: Session) -> None:
    user, goal, cycle, uow = _seed_cycle(session)
    action = plan_action(
        uow,
        cycle_id=cycle.cycle_id,
        seq=1,
        title="跑步 30 分钟",
        metric_key=MetricKey.PRIMARY,
        duration_min=30,
    )

    results = _sync(
        uow,
        str(user.id),
        [
            _operation(evidence=_evidence(goal.goal_id, action_id=action.action_id)),
            _operation(op_type="complete_action", action_id=action.action_id),
        ],
    )

    assert [item.outcome for item in results] == [
        SyncOpOutcome.APPLIED,
        SyncOpOutcome.APPLIED,
    ]
    assert all(
        decision.decision == "accepted" for result in results for decision in result.decisions
    )
    stored = uow.actions.get(action.action_id)
    assert stored is not None and stored.status is ActionStatus.DONE
    assert stored.completed_at == CAPTURED_AT
    assert session.scalar(select(func.count()).select_from(EvidenceRow)) == 1
    assert session.scalar(select(func.count()).select_from(SyncOpRow)) == 2


def test_goal_edit_is_rejected_per_field(session: Session) -> None:
    user, goal, _, uow = _seed_cycle(session)

    result = _sync(
        uow,
        str(user.id),
        [_operation(op_type="edit_goal", field="statement", value="改成每天跑步")],
    )[0]

    assert result.outcome is SyncOpOutcome.REJECTED
    assert result.result_ref is None
    assert result.decisions == (
        FieldDecision(
            field="statement",
            decision="rejected",
            reason_code="offline_operation_not_allowed",
        ),
    )
    assert uow.goals.get(goal.goal_id) == goal
    assert session.scalar(select(func.count()).select_from(SyncOpRow)) == 0


def test_replayed_packet_returns_duplicate_without_extra_writes(session: Session) -> None:
    user, goal, _, uow = _seed_cycle(session)
    operation = _operation(evidence=_evidence(goal.goal_id))

    first = _sync(uow, str(user.id), [operation])[0]
    second = _sync(uow, str(user.id), [operation])[0]

    assert first.outcome is SyncOpOutcome.APPLIED
    assert second.outcome is SyncOpOutcome.DUPLICATE
    assert second.result_ref == first.result_ref
    assert _decision(second, "value_calc").reason_code == "duplicate_operation"
    assert session.scalar(select(func.count()).select_from(EvidenceRow)) == 1
    assert session.scalar(select(func.count()).select_from(SyncOpRow)) == 1


def test_clock_skew_is_clamped_with_visible_reason(session: Session) -> None:
    user, goal, _, uow = _seed_cycle(session)
    client_at = RECEIVED_AT + timedelta(minutes=20)
    evidence = _evidence(goal.goal_id, captured_at=client_at, confirmed_at=client_at)

    result = _sync(
        uow,
        str(user.id),
        [_operation(captured_at=client_at, evidence=evidence)],
    )[0]

    assert result.outcome is SyncOpOutcome.APPLIED
    decision = _decision(result, "captured_at")
    assert decision.decision == "accepted"
    assert decision.reason_code == "clock_clamped"
    stored = uow.evidences.get(result.result_ref or "")
    assert stored is not None and stored.captured_at == RECEIVED_AT
    assert stored.entered_value == 1.0


def test_clock_skew_reject_strategy_records_rejection(session: Session) -> None:
    user, goal, _, uow = _seed_cycle(session)
    client_at = RECEIVED_AT + timedelta(minutes=20)
    evidence = _evidence(goal.goal_id, captured_at=client_at, confirmed_at=client_at)
    operation = _operation(captured_at=client_at, evidence=evidence)

    result = _sync(uow, str(user.id), [operation], strategy="reject")[0]

    assert result.outcome is SyncOpOutcome.REJECTED
    assert _decision(result, "captured_at").decision == "rejected"
    assert _decision(result, "captured_at").reason_code == "clock_skew_rejected"
    assert session.scalar(select(func.count()).select_from(EvidenceRow)) == 0
    record = uow.sync_ops.get(user_id=str(user.id), client_op_id=operation.client_op_id)
    assert record is not None and record.outcome is SyncOpOutcome.REJECTED
    assert record.error_code == "clock_skew_rejected"


def test_action_completion_time_conflict_does_not_overwrite(session: Session) -> None:
    user, _, cycle, uow = _seed_cycle(session)
    action = plan_action(
        uow,
        cycle_id=cycle.cycle_id,
        seq=1,
        title="跑步 30 分钟",
        metric_key=MetricKey.PRIMARY,
        duration_min=30,
    )
    first = _sync(
        uow,
        str(user.id),
        [_operation(op_type="complete_action", action_id=action.action_id)],
    )[0]
    later = CAPTURED_AT + timedelta(hours=1)

    second = _sync(
        uow,
        str(user.id),
        [
            _operation(
                op_type="complete_action",
                action_id=action.action_id,
                captured_at=later,
            )
        ],
    )[0]

    assert first.outcome is SyncOpOutcome.APPLIED
    assert second.outcome is SyncOpOutcome.REJECTED
    decision = _decision(second, "completed_at")
    assert decision.decision == "conflict"
    assert decision.reason_code == "action_already_completed"
    stored = uow.actions.get(action.action_id)
    assert stored is not None and stored.completed_at == CAPTURED_AT


def test_reused_client_op_id_reports_changed_evidence_fields(session: Session) -> None:
    user, goal, _, uow = _seed_cycle(session)
    op_id = str(uuid.uuid4())
    first = _sync(
        uow,
        str(user.id),
        [_operation(client_op_id=op_id, evidence=_evidence(goal.goal_id))],
    )[0]
    changed = _evidence(goal.goal_id, entered_value=2.0, value_calc=2.0)

    second = _sync(
        uow,
        str(user.id),
        [_operation(client_op_id=op_id, evidence=changed)],
    )[0]

    assert second.outcome is SyncOpOutcome.REJECTED
    assert second.result_ref == first.result_ref
    for field in ("entered_value", "value_calc"):
        decision = _decision(second, field)
        assert decision.decision == "conflict"
        assert decision.reason_code == "client_op_payload_mismatch"
    stored = uow.evidences.get(first.result_ref or "")
    assert stored is not None and stored.entered_value == 1.0
    assert session.scalar(select(func.count()).select_from(EvidenceRow)) == 1
    assert session.scalar(select(func.count()).select_from(SyncOpRow)) == 1
