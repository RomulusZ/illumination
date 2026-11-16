"""Goal, version and metric contract tests."""

from datetime import UTC, date, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from illumination.types import (
    BaselineState,
    Goal,
    GoalChangeKind,
    GoalIntent,
    GoalRole,
    GoalStatus,
    GoalVersion,
    Intent,
    IntentCreate,
    IntervalSource,
    Metric,
    MetricDirection,
    MetricKey,
    MetricKind,
    ThresholdSource,
    ThresholdWriteWindow,
)


def _metric(**overrides: Any) -> Metric:
    data: dict[str, Any] = {
        "metric_key": MetricKey.PRIMARY,
        "name": "每周跑步次数",
        "kind": MetricKind.NUMERIC,
        "unit_code": "count",
        "display_unit_code": "次",
        "direction": MetricDirection.UP,
        "baseline_state": BaselineState.MEASURED,
        "baseline_value": 2.0,
        "baseline_unit_code": "count",
        "baseline_captured_at": datetime(2026, 3, 1, tzinfo=UTC),
        "target_value": 4.0,
        "threshold_source": ThresholdSource.USER_INPUT,
        "written_in": ThresholdWriteWindow.PLAN,
    }
    data.update(overrides)
    return Metric(**data)


def _version(**overrides: Any) -> GoalVersion:
    data: dict[str, Any] = {
        "goal_id": "goal_1",
        "version_no": 1,
        "statement": "每周跑步 4 次",
        "intent": GoalIntent.ADVANCE,
        "role": GoalRole.PRIMARY,
        "feedback_interval_days": 7,
        "interval_source": IntervalSource.USER_INPUT,
        "deadline_end": date(2026, 6, 1),
        "constraints": ("膝盖旧伤，单次不超过 30 分钟",),
        "has_no_constraints": False,
        "change_kind": GoalChangeKind.INITIAL,
        "effective_at": datetime(2026, 3, 1, tzinfo=UTC),
        "metrics": (_metric(),),
    }
    data.update(overrides)
    return GoalVersion(**data)


def test_valid_goal_version_round_trips_json() -> None:
    version = _version()
    assert GoalVersion.model_validate_json(version.model_dump_json()) == version


def test_goal_version_rejects_duplicate_primary_metrics() -> None:
    with pytest.raises(ValidationError, match="主指标"):
        _version(metrics=(_metric(), _metric(name="另一项")))


def test_goal_version_requires_exactly_one_primary_metric() -> None:
    secondary = _metric(metric_key=MetricKey.SECONDARY_1)
    with pytest.raises(ValidationError, match="主指标"):
        _version(metrics=(secondary,))


def test_goal_version_rejects_duplicate_secondary_slot() -> None:
    with pytest.raises(ValidationError, match="槽位"):
        _version(
            metrics=(
                _metric(),
                _metric(metric_key=MetricKey.SECONDARY_1),
                _metric(metric_key=MetricKey.SECONDARY_2),
                _metric(metric_key=MetricKey.SECONDARY_2),
            )
        )


def test_metric_slots_match_one_primary_and_two_secondary() -> None:
    assert {key.value for key in MetricKey} == {"primary", "secondary_1", "secondary_2"}


def test_goal_version_requires_deadline_only_for_advance() -> None:
    with pytest.raises(ValidationError, match="deadline_end"):
        _version(deadline_end=None)
    with pytest.raises(ValidationError, match="deadline_end"):
        _version(intent=GoalIntent.MAINTAIN)
    maintain = _version(intent=GoalIntent.MAINTAIN, role=GoalRole.SECONDARY, deadline_end=None)
    assert maintain.deadline_end is None


def test_goal_version_requires_explicit_empty_constraints() -> None:
    with pytest.raises(ValidationError, match="约束"):
        _version(constraints=())
    with pytest.raises(ValidationError, match="约束"):
        _version(has_no_constraints=True)
    explicit_none = _version(constraints=(), has_no_constraints=True)
    assert explicit_none.has_no_constraints


def test_metric_unknown_baseline_must_not_carry_value() -> None:
    with pytest.raises(ValidationError, match="unknown"):
        _metric(baseline_state=BaselineState.UNKNOWN)
    unknown = _metric(
        baseline_state=BaselineState.UNKNOWN,
        baseline_value=None,
        baseline_unit_code=None,
        baseline_captured_at=None,
    )
    assert unknown.baseline_value is None


def test_metric_measured_baseline_requires_full_triplet() -> None:
    with pytest.raises(ValidationError, match="基线"):
        _metric(baseline_captured_at=None)


def test_metric_target_shape_follows_kind() -> None:
    with pytest.raises(ValidationError, match="目标值"):
        _metric(target_value=None)
    state_metric = _metric(
        kind=MetricKind.STATE,
        target_value=None,
        target_expected_state="连续 4 周按计划完成",
    )
    assert state_metric.target_expected_state == "连续 4 周按计划完成"
    with pytest.raises(ValidationError, match="期望状态"):
        _metric(
            kind=MetricKind.STATE,
            target_value=None,
            threshold_source=None,
            written_in=None,
        )


def test_metric_target_requires_threshold_provenance() -> None:
    with pytest.raises(ValidationError, match="阈值"):
        _metric(threshold_source=None)


def test_goal_rejects_maintain_as_primary() -> None:
    with pytest.raises(ValidationError, match="maintain"):
        Goal(goal_id="goal_1", name_final="保持每周跑步", intent=GoalIntent.MAINTAIN)
    goal = Goal(
        goal_id="goal_1",
        name_final="每周跑步 4 次",
        intent=GoalIntent.ADVANCE,
        role=GoalRole.PRIMARY,
    )
    assert goal.status is GoalStatus.PLANNED


def test_goal_version_is_immutable() -> None:
    version = _version()
    with pytest.raises(ValidationError):
        version.statement = "改过的目标"  # type: ignore[misc]


def test_intent_contracts_validate_and_round_trip() -> None:
    with pytest.raises(ValidationError):
        IntentCreate(statement="")
    created = IntentCreate(statement="想跑得更稳", intended_start_date=date(2026, 3, 1))
    intent = Intent(intent_id="intent_1", **created.model_dump())
    assert Intent.model_validate_json(intent.model_dump_json()) == intent
