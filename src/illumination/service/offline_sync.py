"""离线补传：白名单、服务端幂等、时钟裁决与逐字段冲突结果。

证据与动作完成不需要编辑租约；相同 `client_op_id` 的精确重放返回原指针，
载荷变化或完成时间变化必须显式报冲突，不静默覆盖服务端状态。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, time
from typing import Literal

from pydantic import Field, JsonValue

from illumination.service.action_service import build_action_completion
from illumination.service.errors import (
    ActionNotFoundError,
    ActionRejected,
    ClockSkewRejected,
    CycleNotFoundError,
    EvidenceRejected,
)
from illumination.service.evidence_service import prepare_evidence
from illumination.service.time_rules import SkewStrategy, resolve_client_time
from illumination.types.base import IlluminationModel
from illumination.types.enums import SyncOpOutcome, SyncOpType
from illumination.types.execution import EvidenceCreate, SyncOpRecord
from illumination.types.ports import UnitOfWorkPort

FieldOutcome = Literal["accepted", "rejected", "conflict"]


class FieldDecision(IlluminationModel):
    field: str = Field(min_length=1)
    decision: FieldOutcome
    reason_code: str = Field(min_length=1)


class OfflineOperation(IlluminationModel):
    """本地队列信封；证据使用领域模型，未知编辑字段只用于显式拒绝。"""

    op_type: str = Field(min_length=1)
    client_op_id: str = Field(min_length=1)
    device_id: str = Field(min_length=1)
    captured_at: datetime
    base_rev: int | None = Field(default=None, ge=0)
    evidence: EvidenceCreate | None = None
    action_id: str | None = None
    field: str | None = None
    value: JsonValue = None


class OfflineOperationResult(IlluminationModel):
    client_op_id: str
    op_type: str
    outcome: SyncOpOutcome
    decisions: tuple[FieldDecision, ...]
    result_ref: str | None = None


class OfflineSyncResult(IlluminationModel):
    results: tuple[OfflineOperationResult, ...]


def sync_offline(
    uow: UnitOfWorkPort,
    *,
    user_id: str,
    operations: Sequence[OfflineOperation],
    received_at: datetime,
    timezone: str,
    day_cutoff: time,
    skew_strategy: SkewStrategy,
    skew_limit_minutes: int,
) -> OfflineSyncResult:
    """逐操作提交；失败项不覆盖输入，也不阻塞其他补传项。"""
    results = []
    for operation in operations:
        results.append(
            _sync_one(
                uow,
                user_id=user_id,
                operation=operation,
                received_at=received_at,
                timezone=timezone,
                day_cutoff=day_cutoff,
                skew_strategy=skew_strategy,
                skew_limit_minutes=skew_limit_minutes,
            )
        )
    return OfflineSyncResult(results=tuple(results))


def _sync_one(
    uow: UnitOfWorkPort,
    *,
    user_id: str,
    operation: OfflineOperation,
    received_at: datetime,
    timezone: str,
    day_cutoff: time,
    skew_strategy: SkewStrategy,
    skew_limit_minutes: int,
) -> OfflineOperationResult:
    if operation.op_type not in {item.value for item in SyncOpType}:
        return _result(
            operation,
            SyncOpOutcome.REJECTED,
            [_decision(operation.field or "op_type", "rejected", "offline_operation_not_allowed")],
        )
    try:
        uuid.UUID(operation.client_op_id)
    except ValueError:
        return _result(
            operation,
            SyncOpOutcome.REJECTED,
            [_decision("client_op_id", "rejected", "invalid_client_op_id")],
        )

    record = uow.sync_ops.get(user_id=user_id, client_op_id=operation.client_op_id)
    if record is not None:
        return _replay(uow, operation, record)

    try:
        skew = resolve_client_time(
            operation.captured_at,
            received_at,
            strategy=skew_strategy,
            limit_minutes=skew_limit_minutes,
        )
    except ClockSkewRejected:
        return _reject(uow, user_id, operation, "captured_at", "clock_skew_rejected")
    if operation.op_type == SyncOpType.SUBMIT_EVIDENCE:
        return _submit(
            uow, user_id, operation, skew.resolved_at, skew.clamped, timezone, day_cutoff
        )
    return _complete(uow, user_id, operation, skew.resolved_at, skew.clamped)


def _submit(
    uow: UnitOfWorkPort,
    user_id: str,
    operation: OfflineOperation,
    captured_at: datetime,
    clamped: bool,
    timezone: str,
    day_cutoff: time,
) -> OfflineOperationResult:
    if operation.evidence is None:
        return _reject(uow, user_id, operation, "evidence", "invalid_payload")
    if operation.evidence.captured_at != operation.captured_at:
        return _reject(uow, user_id, operation, "captured_at", "captured_at_mismatch")
    evidence = operation.evidence.model_copy(update={"captured_at": captured_at})
    try:
        created = prepare_evidence(
            uow,
            user_id=user_id,
            evidence=evidence,
            timezone=timezone,
            day_cutoff=day_cutoff,
        )
        uow.evidences.add(created)
        uow.sync_ops.add(_record(user_id, operation, SyncOpOutcome.APPLIED, created.evidence_id))
        uow.commit()
    except EvidenceRejected as error:
        uow.rollback()
        return _reject(uow, user_id, operation, "evidence", error.reason)
    except Exception:
        uow.rollback()
        raise
    decisions = [
        _decision(
            name, "accepted", "clock_clamped" if name == "captured_at" and clamped else "accepted"
        )
        for name in _evidence_fields(evidence)
    ]
    return _result(operation, SyncOpOutcome.APPLIED, decisions, created.evidence_id)


def _complete(
    uow: UnitOfWorkPort,
    user_id: str,
    operation: OfflineOperation,
    completed_at: datetime,
    clamped: bool,
) -> OfflineOperationResult:
    if operation.action_id is None:
        return _reject(uow, user_id, operation, "action_id", "invalid_payload")
    try:
        completion = build_action_completion(
            uow, action_id=operation.action_id, completed_at=completed_at
        )
    except ActionRejected as error:
        return _reject(uow, user_id, operation, "status", error.reason)
    except (ActionNotFoundError, CycleNotFoundError) as error:
        return _reject(uow, user_id, operation, "action_id", error.code)

    if completion.already_completed:
        if completion.action.completed_at == completed_at:
            return _duplicate_action(uow, user_id, operation)
        return _reject(
            uow, user_id, operation, "completed_at", "action_already_completed", conflict=True
        )
    try:
        uow.actions.save(completion.action)
        uow.sync_ops.add(_record(user_id, operation, SyncOpOutcome.APPLIED, operation.action_id))
        uow.commit()
    except Exception:
        uow.rollback()
        raise
    decisions = [
        _decision("status", "accepted", "accepted"),
        _decision("completed_at", "accepted", "clock_clamped" if clamped else "accepted"),
    ]
    return _result(operation, SyncOpOutcome.APPLIED, decisions, operation.action_id)


def _replay(
    uow: UnitOfWorkPort, operation: OfflineOperation, record: SyncOpRecord
) -> OfflineOperationResult:
    if operation.op_type != record.op_type.value:
        return _result(
            operation,
            SyncOpOutcome.REJECTED,
            [_decision("op_type", "conflict", "client_op_payload_mismatch")],
            record.result_ref,
        )
    if record.outcome is SyncOpOutcome.REJECTED:
        return _result(
            operation,
            SyncOpOutcome.REJECTED,
            [
                _decision(
                    operation.field or "captured_at",
                    "rejected",
                    record.error_code or "client_op_rejected",
                )
            ],
            record.result_ref,
        )
    if record.op_type is SyncOpType.SUBMIT_EVIDENCE:
        stored = uow.evidences.get(record.result_ref or "")
        if stored is None or operation.evidence is None:
            return _result(
                operation,
                SyncOpOutcome.REJECTED,
                [_decision("evidence", "rejected", "replay_target_missing")],
            )
        decisions = []
        for name in EvidenceCreate.model_fields:
            current = (
                operation.captured_at
                if name == "captured_at"
                else getattr(operation.evidence, name)
            )
            original = record.captured_at if name == "captured_at" else getattr(stored, name)
            if current is None and original is None:
                continue
            mismatch = current != original
            decisions.append(
                _decision(
                    name,
                    "conflict" if mismatch else "accepted",
                    "client_op_payload_mismatch" if mismatch else "duplicate_operation",
                )
            )
        outcome = (
            SyncOpOutcome.REJECTED
            if any(item.decision == "conflict" for item in decisions)
            else SyncOpOutcome.DUPLICATE
        )
        return _result(operation, outcome, decisions, record.result_ref)

    action = uow.actions.get(record.result_ref or "")
    if action is None or operation.action_id is None:
        return _result(
            operation,
            SyncOpOutcome.REJECTED,
            [_decision("action_id", "rejected", "replay_target_missing")],
        )
    mismatches = []
    if operation.action_id != record.result_ref:
        mismatches.append("action_id")
    if operation.captured_at != record.captured_at:
        mismatches.append("completed_at")
    decisions = [
        _decision(
            name,
            "conflict" if name in mismatches else "accepted",
            "client_op_payload_mismatch" if name in mismatches else "duplicate_operation",
        )
        for name in ("action_id", "completed_at")
    ]
    outcome = SyncOpOutcome.REJECTED if mismatches else SyncOpOutcome.DUPLICATE
    return _result(operation, outcome, decisions, record.result_ref)


def _duplicate_action(
    uow: UnitOfWorkPort, user_id: str, operation: OfflineOperation
) -> OfflineOperationResult:
    try:
        uow.sync_ops.add(_record(user_id, operation, SyncOpOutcome.DUPLICATE, operation.action_id))
        uow.commit()
    except Exception:
        uow.rollback()
        raise
    return _result(
        operation,
        SyncOpOutcome.DUPLICATE,
        [_decision("completed_at", "accepted", "already_completed")],
        operation.action_id,
    )


def _reject(
    uow: UnitOfWorkPort,
    user_id: str,
    operation: OfflineOperation,
    field: str,
    reason: str,
    *,
    conflict: bool = False,
) -> OfflineOperationResult:
    try:
        uow.sync_ops.add(_record(user_id, operation, SyncOpOutcome.REJECTED, error_code=reason))
        uow.commit()
    except Exception:
        uow.rollback()
        raise
    return _result(
        operation,
        SyncOpOutcome.REJECTED,
        [_decision(field, "conflict" if conflict else "rejected", reason)],
    )


def _record(
    user_id: str,
    operation: OfflineOperation,
    outcome: SyncOpOutcome,
    result_ref: str | None = None,
    error_code: str | None = None,
) -> SyncOpRecord:
    return SyncOpRecord(
        user_id=user_id,
        client_op_id=operation.client_op_id,
        device_id=operation.device_id,
        op_type=SyncOpType(operation.op_type),
        captured_at=operation.captured_at,
        outcome=outcome,
        result_ref=result_ref,
        error_code=error_code,
    )


def _evidence_fields(evidence: EvidenceCreate) -> list[str]:
    return [name for name in EvidenceCreate.model_fields if getattr(evidence, name) is not None]


def _decision(field: str, outcome: FieldOutcome, reason: str) -> FieldDecision:
    return FieldDecision(field=field, decision=outcome, reason_code=reason)


def _result(
    operation: OfflineOperation,
    outcome: SyncOpOutcome,
    decisions: list[FieldDecision],
    result_ref: str | None = None,
) -> OfflineOperationResult:
    return OfflineOperationResult(
        client_op_id=operation.client_op_id,
        op_type=operation.op_type,
        outcome=outcome,
        decisions=tuple(decisions),
        result_ref=result_ref,
    )
