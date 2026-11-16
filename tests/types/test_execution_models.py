"""Cycle, action, evidence and review contract tests."""

from datetime import UTC, date, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from illumination.types import (
    Action,
    ActionCreate,
    ActionStatus,
    ActionType,
    Cycle,
    CycleCreate,
    CycleResult,
    CycleState,
    Evidence,
    EvidenceChannel,
    EvidenceCreate,
    EvidenceMode,
    EvidenceStatus,
    MetricKey,
    Review,
    ReviewCreate,
    ReviewScope,
    StageDecision,
)

_START = datetime(2026, 3, 1, 4, tzinfo=UTC)


def _cycle(**overrides: Any) -> Cycle:
    data: dict[str, Any] = {
        "cycle_id": "cycle_1",
        "goal_id": "goal_1",
        "goal_version_id": "version_1",
        "seq": 1,
        "state": CycleState.ACTIVE,
        "start_at": _START,
        "end_at": datetime(2026, 3, 8, 4, tzinfo=UTC),
        "budget_minutes": 180,
    }
    data.update(overrides)
    return Cycle(**data)


def _action(**overrides: Any) -> Action:
    data: dict[str, Any] = {
        "action_id": "action_1",
        "cycle_id": "cycle_1",
        "goal_id": "goal_1",
        "seq": 1,
        "title": "慢跑 20 分钟",
        "metric_key": MetricKey.PRIMARY,
        "action_type": ActionType.ONCE,
        "duration_min": 20,
    }
    data.update(overrides)
    return Action(**data)


def _evidence(**overrides: Any) -> Evidence:
    data: dict[str, Any] = {
        "evidence_id": "evidence_1",
        "goal_id": "goal_1",
        "cycle_id": "cycle_1",
        "metric_key": MetricKey.PRIMARY,
        "captured_at": _START,
        "source_channel": EvidenceChannel.MANUAL,
        "status": EvidenceStatus.CONFIRMED,
        "confirmed_at": _START,
        "entered_value": 1.0,
        "entered_unit": "count",
        "value_calc": 1.0,
        "unit_code": "count",
        "display_unit_code": "次",
        "day_cutoff_date": date(2026, 3, 1),
    }
    data.update(overrides)
    return Evidence(**data)


def _review(**overrides: Any) -> Review:
    data: dict[str, Any] = {
        "review_id": "review_1",
        "goal_id": "goal_1",
        "scope": ReviewScope.CYCLE,
        "cycle_id": "cycle_1",
        "cycle_result": CycleResult.MET,
        "summary": "三次跑步全部完成",
    }
    data.update(overrides)
    return Review(**data)


def test_execution_contracts_round_trip_json() -> None:
    for model in (_cycle(), _action(), _evidence(), _review()):
        assert type(model).model_validate_json(model.model_dump_json()) == model


def test_cycle_rejects_reversed_or_empty_window() -> None:
    with pytest.raises(ValidationError, match="end_at"):
        _cycle(end_at=_START)
    with pytest.raises(ValidationError, match="end_at"):
        _cycle(end_at=datetime(2026, 2, 28, 4, tzinfo=UTC))


def test_cycle_closing_requires_deadline() -> None:
    with pytest.raises(ValidationError, match="closing_until"):
        _cycle(state=CycleState.CLOSING)
    closing = _cycle(
        state=CycleState.CLOSING,
        closing_until=datetime(2026, 3, 9, 4, tzinfo=UTC),
    )
    assert closing.closing_until is not None


def test_action_must_bind_observation_item() -> None:
    data = _action().model_dump()
    del data["metric_key"]
    with pytest.raises(ValidationError, match="metric_key"):
        Action(**data)


def test_action_completion_time_matches_status() -> None:
    with pytest.raises(ValidationError, match="completed_at"):
        _action(status=ActionStatus.DONE)
    with pytest.raises(ValidationError, match="completed_at"):
        _action(completed_at=_START)
    done = _action(status=ActionStatus.DONE, completed_at=_START)
    assert done.status is ActionStatus.DONE


def test_evidence_requires_captured_at() -> None:
    data = _evidence().model_dump()
    del data["captured_at"]
    with pytest.raises(ValidationError, match="captured_at"):
        Evidence(**data)


def test_evidence_status_shape_matches_database_rules() -> None:
    with pytest.raises(ValidationError, match="confirmed_at"):
        _evidence(confirmed_at=None)
    with pytest.raises(ValidationError, match="expires_at"):
        _evidence(status=EvidenceStatus.CANDIDATE, confirmed_at=None)
    with pytest.raises(ValidationError, match="设备"):
        _evidence(status=EvidenceStatus.CANDIDATE, confirmed_at=None, expires_at=_START)
    candidate = _evidence(
        status=EvidenceStatus.CANDIDATE,
        confirmed_at=None,
        expires_at=_START,
        source_channel=EvidenceChannel.DEVICE,
        source_device="garmin:watch",
        external_id="garmin-activity-1",
    )
    assert candidate.status is EvidenceStatus.CANDIDATE


def test_candidate_evidence_requires_external_id() -> None:
    with pytest.raises(ValidationError, match="外部"):
        _evidence(
            status=EvidenceStatus.CANDIDATE,
            confirmed_at=None,
            expires_at=_START,
            source_channel=EvidenceChannel.DEVICE,
            source_device="garmin:watch",
        )


def test_evidence_device_channel_requires_device_identity() -> None:
    with pytest.raises(ValidationError, match="设备"):
        _evidence(source_channel=EvidenceChannel.DEVICE)


def test_evidence_numeric_shape_is_all_or_nothing() -> None:
    with pytest.raises(ValidationError, match="数值"):
        _evidence(entered_unit=None)
    textual = _evidence(
        entered_value=None,
        value_calc=None,
        entered_unit=None,
        unit_code=None,
        display_unit_code=None,
    )
    assert textual.value_calc is None


def test_review_requires_cycle_binding() -> None:
    with pytest.raises(ValidationError, match="cycle_id"):
        _review(cycle_id=None)
    with pytest.raises(ValidationError, match="cycle_result"):
        _review(cycle_result=None)


def test_cycle_review_must_not_carry_stage_fields() -> None:
    with pytest.raises(ValidationError):
        _review(decision=StageDecision.CONTINUE)
    with pytest.raises(ValidationError):
        _review(is_deferred=True)


def test_stage_review_shape_and_deferred_rules() -> None:
    stage = _review(
        scope=ReviewScope.STAGE,
        cycle_id=None,
        cycle_result=None,
        stage_index=1,
        window_start=date(2026, 3, 1),
        window_end=date(2026, 3, 28),
        decision=StageDecision.ADJUST_PLAN,
    )
    assert stage.decision is StageDecision.ADJUST_PLAN
    with pytest.raises(ValidationError):
        _review(
            scope=ReviewScope.STAGE,
            cycle_id=None,
            cycle_result=None,
            stage_index=1,
            window_start=date(2026, 3, 1),
            window_end=date(2026, 3, 28),
        )
    with pytest.raises(ValidationError):
        _review(
            scope=ReviewScope.STAGE,
            cycle_id=None,
            cycle_result=None,
            stage_index=1,
            window_start=date(2026, 3, 1),
            window_end=date(2026, 3, 28),
            is_deferred=True,
            decision=StageDecision.CONTINUE,
        )


def test_create_dtos_expose_only_input_fields() -> None:
    assert "cycle_id" not in CycleCreate.model_fields
    assert "action_id" not in ActionCreate.model_fields
    assert "evidence_id" not in EvidenceCreate.model_fields
    assert "review_id" not in ReviewCreate.model_fields
    assert EvidenceMode.NORMAL.value == "normal"
