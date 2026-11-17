"""Intake 子图：锁定、停锁、失败终态与 checkpoint 恢复。"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

from langgraph.checkpoint.memory import InMemorySaver

from illumination.runtime.composition import Runtime
from illumination.runtime.intake_graph import IntakeGraphRequest, IntakeGraphStatus
from illumination.service.readiness import ReadinessAnswers
from illumination.types.enums import (
    BaselineState,
    GoalChangeKind,
    GoalIntent,
    GoalRole,
    GoalStatus,
    IntervalSource,
    MetricKey,
    MetricKind,
    ReadinessLevel,
    ThresholdSource,
    ThresholdWriteWindow,
    ValidityAnswer,
)
from illumination.types.goal import Goal, GoalVersion, Metric
from tests.fakes import FakeClock, FakeLLM, InMemoryArtifactStore

NOW = datetime(2026, 1, 1, tzinfo=UTC)
USER_TEXT = "用户原文：不得进入 checkpoint"
MODEL_BODY = "模型回述正文：不得进入 checkpoint"
SUMMARY = "每周跑步三次"


class _GoalRepository:
    def __init__(self) -> None:
        self._goals: dict[str, Goal] = {}
        self._owners: dict[str, str] = {}
        self._versions: dict[tuple[str, int], GoalVersion] = {}

    def add(self, *, user_id: str, goal: Goal) -> None:
        self._goals[goal.goal_id] = goal
        self._owners[goal.goal_id] = user_id

    def get(self, goal_id: str) -> Goal | None:
        return self._goals.get(goal_id)

    def get_version(self, goal_id: str, version_no: int) -> GoalVersion | None:
        return self._versions.get((goal_id, version_no))

    def append_version(self, version: GoalVersion) -> None:
        self._versions[(version.goal_id, version.version_no)] = version

    def count_active_by_role(self, user_id: str, role: GoalRole) -> int:
        return sum(
            self._owners[goal_id] == user_id
            and goal.role is role
            and goal.status in {GoalStatus.PLANNED, GoalStatus.ACTIVE}
            for goal_id, goal in self._goals.items()
        )


class _UnitOfWork:
    def __init__(self) -> None:
        self.goals = _GoalRepository()
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _runtime(llm: FakeLLM, checkpointer: InMemorySaver) -> Runtime:
    return Runtime(
        llm=llm,
        clock=FakeClock(NOW),
        store=InMemoryArtifactStore(),
        intake_checkpointer=checkpointer,
    )


def _answers(**overrides: object) -> ReadinessAnswers:
    values: dict[str, object] = {
        "statement": "每周跑步三次，每次 30 分钟",
        "metric_key": MetricKey.PRIMARY,
        "baseline_state": BaselineState.UNKNOWN,
        "target_value": 3,
        "feedback_interval_days": 7,
        "constraints_none": True,
    }
    values.update(overrides)
    return ReadinessAnswers(**values)  # type: ignore[arg-type]


def _request(answers: ReadinessAnswers | None = None) -> IntakeGraphRequest:
    goal = Goal(
        goal_id="goal-1",
        name_final="跑步习惯",
        intent=GoalIntent.ADVANCE,
        role=GoalRole.PRIMARY,
        status=GoalStatus.ACTIVE,
        started_at=NOW,
    )
    version = GoalVersion(
        version_id="version-1",
        goal_id=goal.goal_id,
        version_no=1,
        statement="每周跑步三次，每次 30 分钟",
        intent=GoalIntent.ADVANCE,
        role=GoalRole.PRIMARY,
        feedback_interval_days=7,
        interval_source=IntervalSource.USER_INPUT,
        deadline_end=date(2026, 4, 1),
        has_no_constraints=True,
        change_kind=GoalChangeKind.INITIAL,
        effective_at=NOW,
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
    return IntakeGraphRequest(
        user_id="user-1",
        user_input=USER_TEXT,
        goal=goal,
        version=version,
        answers=answers or _answers(),
    )


def _intake_payload() -> str:
    return json.dumps(
        {
            "recognition": {
                "statement": "每周跑步三次，每次 30 分钟",
                "situation": "工作日晚间",
                "intended_start_date": "2026-01-01",
            },
            "questions": {
                "q1_validity": "这个目标值得投入吗？",
                "q2_availability": "每周能安排三次吗？",
                "q3_failure_plan": "错过一次后如何恢复？",
            },
            "recap": {
                "message": MODEL_BODY,
                "missing_fields": [],
                "assumptions": [],
            },
            "one_line_summary": SUMMARY,
        },
        ensure_ascii=False,
    )


async def test_intake_graph_locks_ready_goal() -> None:
    llm = FakeLLM([_intake_payload()])
    runtime = _runtime(llm, InMemorySaver())
    uow = _UnitOfWork()

    result = await runtime.run_intake(_request(), uow=uow, thread_id="thread-lock")

    assert result.status is IntakeGraphStatus.LOCKED
    assert result.error_code is None
    assert result.summary == SUMMARY
    assert result.readiness is not None
    assert result.readiness.level is ReadinessLevel.L3
    assert result.lock_created is True
    assert result.version_id == "version-1"
    assert uow.goals.get("goal-1") is not None
    assert uow.goals.get_version("goal-1", 1) is not None
    assert uow.commits == 1
    assert len(llm.requests) == 1
    assert result.intake_ref is not None
    assert result.readiness_ref is not None
    assert result.recap_ref is not None


async def test_intake_graph_stops_when_validity_answer_is_no() -> None:
    runtime = _runtime(FakeLLM([_intake_payload()]), InMemorySaver())
    uow = _UnitOfWork()

    result = await runtime.run_intake(
        _request(_answers(q1_validity=ValidityAnswer.NO)),
        uow=uow,
        thread_id="thread-stop",
    )

    assert result.status is IntakeGraphStatus.STOPPED
    assert result.error_code == "goal_not_ready"
    assert result.readiness is not None
    assert result.readiness.level is ReadinessLevel.L2
    assert result.readiness.stop_reasons == ("q1_invalid",)
    assert result.recap_ref is not None
    assert result.version_id is None
    assert uow.goals.get("goal-1") is None
    assert uow.commits == 0


async def test_intake_graph_model_failure_is_typed_terminal() -> None:
    runtime = _runtime(FakeLLM(["not json"]), InMemorySaver())
    uow = _UnitOfWork()

    result = await runtime.run_intake(_request(), uow=uow, thread_id="thread-failure")

    assert result.status is IntakeGraphStatus.FAILED
    assert result.error_code == "structured_output_invalid"
    assert result.intake_ref is None
    assert result.readiness_ref is None
    assert result.recap_ref is None
    assert result.version_id is None
    assert uow.goals.get("goal-1") is None


async def test_intake_graph_resume_does_not_add_turn_or_checkpoint_body() -> None:
    llm = FakeLLM([_intake_payload()])
    checkpointer = InMemorySaver()
    runtime = _runtime(llm, checkpointer)
    uow = _UnitOfWork()
    first = await runtime.run_intake(_request(), uow=uow, thread_id="thread-resume")

    resumed = await runtime.resume_intake(uow=uow, thread_id="thread-resume")

    assert resumed == first
    assert len(llm.requests) == 1
    saved = checkpointer.get_tuple({"configurable": {"thread_id": "thread-resume"}})
    assert saved is not None
    checkpoint_text = repr(saved.checkpoint)
    assert USER_TEXT not in checkpoint_text
    assert MODEL_BODY not in checkpoint_text
    channel_values = saved.checkpoint["channel_values"]
    assert "input_ref" in channel_values
    assert "intake_ref" in channel_values
    assert "request" not in channel_values
    assert "intake_output" not in channel_values
