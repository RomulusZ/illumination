"""证据提交：manual / candidate / confirmed、两种幂等通道与 confirmed 计数。

幂等只保存指针：在线通道写 `idempotency_keys`，离线通道写 `sync_ops`；
重放时按指针读回原证据，不复制正文。统计只读取 confirmed。
"""

from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta

from illumination.service.errors import EvidenceRejected
from illumination.service.time_rules import business_date
from illumination.types.base import IlluminationModel
from illumination.types.enums import CycleState, SyncOpOutcome, SyncOpType
from illumination.types.execution import Evidence, EvidenceCreate, SyncOpRecord
from illumination.types.ports import UnitOfWorkPort

SCOPE = "evidence.submit"
IDEMPOTENCY_TTL = timedelta(hours=24)


class EvidenceSubmissionResult(IlluminationModel):
    """提交结果；重放返回原证据，already_recorded=True。"""

    evidence: Evidence
    already_recorded: bool
    confirmed_count: int


def submit_evidence(
    uow: UnitOfWorkPort,
    *,
    user_id: str,
    evidence: EvidenceCreate,
    recorded_at: datetime,
    timezone: str,
    day_cutoff: time,
    idempotency_key: str | None = None,
    client_op_id: str | None = None,
    device_id: str | None = None,
) -> EvidenceSubmissionResult:
    """提交一次证据；两路幂等键任意一路命中即返回原结果。"""
    _require_aware(recorded_at, "recorded_at")
    _require_aware(evidence.captured_at, "captured_at")
    if client_op_id is not None:
        _validate_client_op(client_op_id, device_id)

    if idempotency_key is not None:
        replayed = _replay_by_key(uow, user_id, idempotency_key, evidence.goal_id)
        if replayed is not None:
            return replayed
    if client_op_id is not None:
        replayed = _replay_by_client_op(uow, user_id, client_op_id, evidence.goal_id)
        if replayed is not None:
            return replayed

    if uow.goals.get_owned(user_id, evidence.goal_id) is None:
        raise EvidenceRejected("目标不存在或不属于当前用户", reason="goal_not_found")
    cycle_id = _resolve_cycle(uow, evidence)

    payload = evidence.model_dump()
    payload["cycle_id"] = cycle_id
    created = Evidence(
        evidence_id=str(uuid.uuid4()),
        day_cutoff_date=business_date(
            evidence.captured_at, timezone=timezone, day_cutoff=day_cutoff
        ),
        **payload,
    )
    try:
        uow.evidences.add(created)
        if idempotency_key is not None:
            uow.idempotency.save(
                user_id=user_id,
                scope=SCOPE,
                key=idempotency_key,
                evidence_id=created.evidence_id,
                expires_at=recorded_at + IDEMPOTENCY_TTL,
            )
        if client_op_id is not None and device_id is not None:
            uow.sync_ops.add(
                SyncOpRecord(
                    user_id=user_id,
                    client_op_id=client_op_id,
                    device_id=device_id,
                    op_type=SyncOpType.SUBMIT_EVIDENCE,
                    captured_at=evidence.captured_at,
                    outcome=SyncOpOutcome.APPLIED,
                    result_ref=created.evidence_id,
                )
            )
        uow.commit()
    except Exception:
        uow.rollback()
        raise
    return _result(uow, created, already_recorded=False)


def count_confirmed(uow: UnitOfWorkPort, *, goal_id: str) -> int:
    """目标级统计入口；候选证据不参与。"""
    return uow.evidences.count_confirmed(goal_id)


def _resolve_cycle(uow: UnitOfWorkPort, evidence: EvidenceCreate) -> str | None:
    """校验动作/单元归属，并让动作决定缺省的 cycle_id。"""
    cycle_id = evidence.cycle_id
    if evidence.action_id is not None:
        action = uow.actions.get(evidence.action_id)
        if action is None:
            raise EvidenceRejected("动作不存在", reason="action_not_found")
        if action.goal_id != evidence.goal_id:
            raise EvidenceRejected("动作不属于该目标", reason="action_goal_mismatch")
        if cycle_id is None:
            cycle_id = action.cycle_id
    if cycle_id is None:
        return None
    cycle = uow.cycles.get(cycle_id)
    if cycle is None:
        raise EvidenceRejected("单元不存在", reason="cycle_not_found")
    if cycle.goal_id != evidence.goal_id:
        raise EvidenceRejected("单元不属于该目标", reason="cycle_goal_mismatch")
    if cycle.state is CycleState.REVIEWED:
        raise EvidenceRejected("周期已收口，不能再补证据", reason="cycle_closed")
    return cycle_id


def _replay_by_key(
    uow: UnitOfWorkPort, user_id: str, key: str, goal_id: str
) -> EvidenceSubmissionResult | None:
    record = uow.idempotency.find(user_id=user_id, scope=SCOPE, key=key)
    if record is None:
        return None
    return _replay(uow, record.evidence_id, goal_id, conflict="idempotency_conflict")


def _replay_by_client_op(
    uow: UnitOfWorkPort, user_id: str, client_op_id: str, goal_id: str
) -> EvidenceSubmissionResult | None:
    record = uow.sync_ops.get(user_id=user_id, client_op_id=client_op_id)
    if record is None:
        return None
    if record.outcome is SyncOpOutcome.REJECTED:
        raise EvidenceRejected(
            "该离线操作已被拒绝", reason=record.error_code or "client_op_rejected"
        )
    if record.result_ref is None:
        return None
    return _replay(uow, record.result_ref, goal_id, conflict="client_op_conflict")


def _replay(
    uow: UnitOfWorkPort, evidence_id: str, goal_id: str, *, conflict: str
) -> EvidenceSubmissionResult:
    stored = uow.evidences.get(evidence_id)
    if stored is None:
        raise EvidenceRejected("幂等记录指向的证据已不存在", reason="replay_target_missing")
    if stored.goal_id != goal_id:
        raise EvidenceRejected("幂等键已用于另一个目标", reason=conflict)
    return _result(uow, stored, already_recorded=True)


def _result(
    uow: UnitOfWorkPort, evidence: Evidence, *, already_recorded: bool
) -> EvidenceSubmissionResult:
    return EvidenceSubmissionResult(
        evidence=evidence,
        already_recorded=already_recorded,
        confirmed_count=uow.evidences.count_confirmed(evidence.goal_id),
    )


def _validate_client_op(client_op_id: str, device_id: str | None) -> None:
    if device_id is None:
        raise EvidenceRejected("离线补传必须提供 device_id", reason="missing_device_id")
    try:
        uuid.UUID(client_op_id)
    except ValueError as error:
        raise EvidenceRejected(
            "client_op_id 必须是 UUID", reason="invalid_client_op_id"
        ) from error


def _require_aware(moment: datetime, name: str) -> None:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{name} 必须是带时区的时间")
