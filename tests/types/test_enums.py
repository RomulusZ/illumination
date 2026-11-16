"""Domain enum and value-object contract tests."""

from datetime import date
from enum import StrEnum

import pytest
from pydantic import TypeAdapter, ValidationError

from illumination.types import (
    BaselineState,
    CycleResult,
    CycleState,
    EvidenceChannel,
    EvidenceStatus,
    GoalChangeKind,
    GoalIntent,
    GoalRole,
    GoalStatus,
    IntervalSource,
    LocalDateRange,
    MetricDirection,
    MetricKey,
    MetricKind,
    ReadinessLevel,
    ReviewScope,
    StageDecision,
    ThresholdSource,
    ThresholdWriteWindow,
)

_ENUMS: tuple[type[StrEnum], ...] = (
    GoalStatus,
    CycleState,
    EvidenceChannel,
    EvidenceStatus,
    ReadinessLevel,
    CycleResult,
    ReviewScope,
    StageDecision,
    GoalIntent,
    GoalRole,
    GoalChangeKind,
    MetricKey,
    MetricKind,
    MetricDirection,
    BaselineState,
    ThresholdSource,
    ThresholdWriteWindow,
    IntervalSource,
)


@pytest.mark.parametrize("enum_type", _ENUMS)
def test_enum_json_round_trip(enum_type: type[StrEnum]) -> None:
    adapter = TypeAdapter(enum_type)
    for member in enum_type:
        assert adapter.validate_json(adapter.dump_json(member)) is member


@pytest.mark.parametrize("enum_type", _ENUMS)
def test_enum_rejects_unknown_value(enum_type: type[StrEnum]) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(enum_type).validate_python("__invalid__")


def test_enum_members_match_design_contract() -> None:
    assert {status.value for status in GoalStatus} == {
        "planned",
        "active",
        "achieved",
        "archived",
        "abandoned",
        "expired",
        "withdrawn",
        "paused",
    }
    assert {state.value for state in CycleState} == {"frozen", "active", "closing", "reviewed"}
    assert {level.value for level in ReadinessLevel} == {"L0", "L1", "L2", "L3"}
    assert {result.value for result in CycleResult} == {
        "met",
        "not_met",
        "insufficient_data",
        "aborted",
    }
    assert {decision.value for decision in StageDecision} == {
        "continue",
        "adjust_plan",
        "renegotiate",
        "replan",
        "close",
        "continue_maintain",
        "adjust_standard",
        "stop_maintain",
    }


def test_local_date_range_includes_both_boundaries() -> None:
    span = LocalDateRange(start_date=date(2026, 3, 1), end_date=date(2026, 3, 31))
    assert span.contains(date(2026, 3, 1))
    assert span.contains(date(2026, 3, 31))
    assert not span.contains(date(2026, 2, 28))
    assert not span.contains(date(2026, 4, 1))
    assert span.days == 31


def test_local_date_range_rejects_reversed_dates() -> None:
    with pytest.raises(ValidationError):
        LocalDateRange(start_date=date(2026, 3, 2), end_date=date(2026, 3, 1))


def test_local_date_range_single_day_and_json_round_trip() -> None:
    span = LocalDateRange.single_day(date(2026, 3, 1))
    assert span.days == 1
    payload = span.model_dump_json()
    assert LocalDateRange.model_validate_json(payload) == span
