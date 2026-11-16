"""就绪度 L0–L3 判定与 Q1/Q2 停锁规则。"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from illumination.service.readiness import ReadinessAnswers, assess_readiness
from illumination.types.enums import (
    AvailabilityAnswer,
    BaselineState,
    MetricKey,
    ReadinessLevel,
    ValidityAnswer,
)


def _complete(**overrides: object) -> ReadinessAnswers:
    values: dict[str, object] = {
        "direction": "想变精神一点",
        "statement": "体重从 75kg 降到 65kg",
        "metric_key": MetricKey.PRIMARY,
        "baseline_state": BaselineState.MEASURED,
        "baseline_value": 75,
        "target_value": 65,
        "feedback_interval_days": 7,
        "constraints_none": True,
        "q1_validity": ValidityAnswer.YES,
        "q2_availability": AvailabilityAnswer.NOW,
    }
    values.update(overrides)
    return ReadinessAnswers(**values)  # type: ignore[arg-type]


def test_wish_without_direction_is_l0() -> None:
    result = assess_readiness(ReadinessAnswers())

    assert result.level is ReadinessLevel.L0
    assert result.stop_lock is False
    assert result.missing_fields[0] == "statement"


def test_direction_without_observable_is_l1() -> None:
    result = assess_readiness(ReadinessAnswers(direction="想变精神一点", statement="变精神"))

    assert result.level is ReadinessLevel.L1
    assert "metric" in result.missing_fields


def test_metric_baseline_and_target_reach_l2() -> None:
    result = assess_readiness(_complete(feedback_interval_days=None, constraints_none=False))

    assert result.level is ReadinessLevel.L2
    assert "feedback_interval" in result.missing_fields
    assert "constraints" in result.missing_fields


def test_all_five_fields_and_declared_constraints_reach_l3() -> None:
    result = assess_readiness(_complete())

    assert result.level is ReadinessLevel.L3
    assert result.missing_fields == ()
    assert result.stop_lock is False


def test_unknown_baseline_is_allowed_but_missing_baseline_is_not() -> None:
    unknown = assess_readiness(
        _complete(baseline_state=BaselineState.UNKNOWN, baseline_value=None)
    )
    missing = assess_readiness(_complete(baseline_state=None, baseline_value=None))

    assert unknown.level is ReadinessLevel.L3
    assert missing.level is ReadinessLevel.L1
    assert "baseline" in missing.missing_fields


def test_q1_no_stops_lock_and_downgrades_to_l2() -> None:
    result = assess_readiness(_complete(q1_validity=ValidityAnswer.NO))

    assert result.level is ReadinessLevel.L2
    assert result.stop_lock is True
    assert result.stop_reasons == ("q1_invalid",)


def test_q2_no_stops_lock_and_downgrades_to_l2() -> None:
    result = assess_readiness(_complete(q2_availability=AvailabilityAnswer.NO))

    assert result.level is ReadinessLevel.L2
    assert result.stop_lock is True
    assert result.stop_reasons == ("q2_unavailable",)


def test_q1_partial_and_q2_needs_setup_do_not_stop_lock() -> None:
    result = assess_readiness(
        _complete(
            q1_validity=ValidityAnswer.PARTIAL,
            q2_availability=AvailabilityAnswer.NEEDS_SETUP,
        )
    )

    assert result.level is ReadinessLevel.L3
    assert result.stop_lock is False


def test_constraints_must_be_declared_or_explicitly_none() -> None:
    result = assess_readiness(_complete(constraints_none=False, constraints=()))

    assert result.level is ReadinessLevel.L2
    assert "constraints" in result.missing_fields


def test_measured_baseline_requires_value() -> None:
    with pytest.raises(ValidationError, match="实测值"):
        _complete(baseline_value=None)


def test_unknown_baseline_rejects_value() -> None:
    with pytest.raises(ValidationError, match="unknown"):
        _complete(baseline_state=BaselineState.UNKNOWN, baseline_value=75)


def test_deadline_is_optional_and_does_not_change_level() -> None:
    without_deadline = assess_readiness(_complete())
    with_deadline = assess_readiness(_complete(deadline=date(2027, 2, 16)))

    assert with_deadline.level is ReadinessLevel.L3
    assert with_deadline.missing_fields == without_deadline.missing_fields
