"""证据提交、两种幂等通道与 confirmed 计数。"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from illumination.repo.models import Evidence as EvidenceRow
from illumination.repo.models import IdempotencyKey as IdempotencyRow
from illumination.repo.models import User
from illumination.repo.sql_repositories import SqlUnitOfWork
from illumination.service.errors import EvidenceRejected
from illumination.service.evidence_service import EvidenceSubmissionResult, submit_evidence
from illumination.types.enums import (
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
    SyncOpType,
    ThresholdSource,
    ThresholdWriteWindow,
)
from illumination.types.execution import EvidenceCreate
from illumination.types.goal import Goal, GoalVersion, Metric

TIMEZONE = "Asia/Shanghai"
DAY_CUTOFF = time(4, 0)
RECORDED_AT = datetime(2026, 11, 16, 6, 0, tzinfo=UTC)
CAPTURED_AT = datetime(2026, 11, 15, 19, 30, tzinfo=UTC)
EXPIRES_AT = CAPTURED_AT + timedelta(days=1)


def _seed_goal(session: Session, display_name: str = "tester") -> tuple[User, Goal]:
    user = User(id=uuid.uuid4(), display_name=display_name)
    session.add(user)
    session.flush()
    goal = Goal(
        goal_id=str(uuid.uuid4()),
        name_final="每周跑步三次",
        intent=GoalIntent.ADVANCE,
        role=GoalRole.PRIMARY,
        status=GoalStatus.ACTIVE,
        started_at=CAPTURED_AT,
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
        effective_at=CAPTURED_AT,
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
    return user, goal


def _manual(goal_id: str, **overrides: object) -> EvidenceCreate:
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


def _candidate(goal_id: str) -> EvidenceCreate:
    return EvidenceCreate(
        goal_id=goal_id,
        metric_key=MetricKey.PRIMARY,
        captured_at=CAPTURED_AT,
        source_channel=EvidenceChannel.DEVICE,
        status=EvidenceStatus.CANDIDATE,
        source_device="garmin:watch",
        external_id="garmin-activity-1",
        expires_at=EXPIRES_AT,
    )


def _submit(
    uow: SqlUnitOfWork,
    user_id: str,
    evidence: EvidenceCreate,
    *,
    idempotency_key: str | None = None,
    client_op_id: str | None = None,
) -> EvidenceSubmissionResult:
    return submit_evidence(
        uow,
        user_id=user_id,
        evidence=evidence,
        recorded_at=RECORDED_AT,
        timezone=TIMEZONE,
        day_cutoff=DAY_CUTOFF,
        idempotency_key=idempotency_key,
        client_op_id=client_op_id,
        device_id="phone-1" if client_op_id else None,
    )


def test_submit_confirmed_evidence_uses_business_date(session: Session) -> None:
    user, goal = _seed_goal(session)
    uow = SqlUnitOfWork(session)

    result = _submit(uow, str(user.id), _manual(goal.goal_id))

    assert result.already_recorded is False
    assert result.confirmed_count == 1
    stored = uow.evidences.get(result.evidence.evidence_id)
    assert stored == result.evidence
    # 本地 03:30 早于 04:00 日界，归入前一个业务日。
    assert stored is not None and stored.day_cutoff_date == date(2026, 11, 15)


def test_idempotency_key_replays_original_evidence(session: Session) -> None:
    user, goal = _seed_goal(session)
    uow = SqlUnitOfWork(session)

    first = _submit(uow, str(user.id), _manual(goal.goal_id), idempotency_key="req-1")
    second = _submit(uow, str(user.id), _manual(goal.goal_id), idempotency_key="req-1")

    assert first.already_recorded is False
    assert second.already_recorded is True
    assert second.evidence == first.evidence
    rows = session.scalars(select(EvidenceRow)).all()
    assert len(rows) == 1
    assert session.scalar(select(func.count()).select_from(IdempotencyRow)) == 1


def test_client_op_id_replays_original_evidence(session: Session) -> None:
    user, goal = _seed_goal(session)
    uow = SqlUnitOfWork(session)
    op_id = str(uuid.uuid4())

    first = _submit(uow, str(user.id), _manual(goal.goal_id), client_op_id=op_id)
    second = _submit(uow, str(user.id), _manual(goal.goal_id), client_op_id=op_id)

    assert first.already_recorded is False
    assert second.already_recorded is True
    assert second.evidence == first.evidence
    assert len(session.scalars(select(EvidenceRow)).all()) == 1

    record = uow.sync_ops.get(user_id=str(user.id), client_op_id=op_id)
    assert record is not None
    assert record.op_type is SyncOpType.SUBMIT_EVIDENCE
    assert record.outcome is SyncOpOutcome.APPLIED
    assert record.result_ref == first.evidence.evidence_id


def test_candidate_evidence_is_persisted_but_not_counted(session: Session) -> None:
    user, goal = _seed_goal(session)
    uow = SqlUnitOfWork(session)

    confirmed = _submit(uow, str(user.id), _manual(goal.goal_id))
    candidate = _submit(uow, str(user.id), _candidate(goal.goal_id))

    assert confirmed.confirmed_count == 1
    assert candidate.confirmed_count == 1
    stored = uow.evidences.get(candidate.evidence.evidence_id)
    assert stored is not None and stored.status is EvidenceStatus.CANDIDATE


def test_idempotency_key_is_isolated_between_users(session: Session) -> None:
    user_a, goal_a = _seed_goal(session, "alice")
    user_b, goal_b = _seed_goal(session, "bob")
    uow = SqlUnitOfWork(session)

    first = _submit(uow, str(user_a.id), _manual(goal_a.goal_id), idempotency_key="shared")
    second = _submit(uow, str(user_b.id), _manual(goal_b.goal_id), idempotency_key="shared")

    assert first.already_recorded is False
    assert second.already_recorded is False
    assert second.evidence.evidence_id != first.evidence.evidence_id
    assert second.confirmed_count == 1
    assert len(session.scalars(select(EvidenceRow)).all()) == 2


def test_goal_must_belong_to_user(session: Session) -> None:
    _, goal = _seed_goal(session)
    stranger = User(id=uuid.uuid4(), display_name="stranger")
    session.add(stranger)
    session.commit()
    uow = SqlUnitOfWork(session)

    with pytest.raises(EvidenceRejected) as error:
        _submit(uow, str(stranger.id), _manual(goal.goal_id))

    assert error.value.reason == "goal_not_found"
    assert session.scalar(select(func.count()).select_from(EvidenceRow)) == 0
