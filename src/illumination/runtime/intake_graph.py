"""Intake 子图：intake → readiness → recap → lock，正文只落 artifact。"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from illumination.repo.errors import RepoError
from illumination.service.errors import IlluminationError
from illumination.service.goal_lifecycle import lock_goal
from illumination.service.intake import IntakeConversation, IntakeOutput
from illumination.service.readiness import ReadinessAnswers, ReadinessResult, assess_readiness
from illumination.types.base import IlluminationModel
from illumination.types.common import ArtifactRef
from illumination.types.enums import ReadinessLevel
from illumination.types.goal import Goal, GoalVersion
from illumination.types.ports import ArtifactStorePort, ClockPort, LLMPort, UnitOfWorkPort


class IntakeGraphStatus(StrEnum):
    """入口子图终态；running 仅用于图内路由。"""

    RUNNING = "running"
    LOCKED = "locked"
    STOPPED = "stopped"
    FAILED = "failed"


class IntakeGraphRequest(IlluminationModel):
    """一次入口推进与锁定所需的完整类型化输入。"""

    user_id: str
    user_input: str
    goal: Goal
    version: GoalVersion
    answers: ReadinessAnswers


class IntakeGraphResult(IlluminationModel):
    """对外返回摘要、终态和指针；不携带用户或模型原始正文。"""

    thread_id: str
    goal_id: str
    status: IntakeGraphStatus
    summary: str | None = None
    error_code: str | None = None
    readiness: ReadinessResult | None = None
    intake_ref: ArtifactRef | None = None
    readiness_ref: ArtifactRef | None = None
    recap_ref: ArtifactRef | None = None
    version_id: str | None = None
    lock_created: bool = False


class IntakeState(TypedDict, total=False):
    """Checkpoint 状态；禁止放入请求正文、Goal 快照或 IntakeOutput。"""

    goal_id: str
    input_ref: ArtifactRef
    intake_ref: ArtifactRef
    readiness_ref: ArtifactRef
    recap_ref: ArtifactRef
    summary: str
    status: IntakeGraphStatus
    error_code: str
    version_id: str
    lock_created: bool


def _build_intake_graph(
    *,
    llm: LLMPort,
    clock: ClockPort,
    store: ArtifactStorePort,
    uow: UnitOfWorkPort,
    checkpointer: Any,
) -> Any:
    async def intake_node(state: IntakeState) -> dict[str, Any]:
        if state.get("intake_ref") is not None:
            return {}
        request = _load_request(state, store)
        try:
            output = await IntakeConversation().advance(llm, request.user_input)
        except IlluminationError as exc:
            return {"status": IntakeGraphStatus.FAILED, "error_code": exc.code}
        try:
            ref = store.put(name="intake-output.json", content=output.model_dump_json().encode())
        except RepoError:
            return {
                "status": IntakeGraphStatus.FAILED,
                "error_code": "artifact_store_unavailable",
            }
        return {
            "intake_ref": ref,
            "summary": output.one_line_summary,
            "status": IntakeGraphStatus.RUNNING,
        }

    def readiness_node(state: IntakeState) -> dict[str, Any]:
        if state.get("readiness_ref") is not None:
            return {}
        request = _load_request(state, store)
        readiness = assess_readiness(request.answers)
        try:
            ref = store.put(
                name="intake-readiness.json", content=readiness.model_dump_json().encode()
            )
        except RepoError:
            return {
                "status": IntakeGraphStatus.FAILED,
                "error_code": "artifact_store_unavailable",
            }
        stopped = readiness.level is not ReadinessLevel.L3 or readiness.stop_lock
        return {
            "readiness_ref": ref,
            "status": IntakeGraphStatus.STOPPED if stopped else IntakeGraphStatus.RUNNING,
            "error_code": "goal_not_ready" if stopped else None,
        }

    def recap_node(state: IntakeState) -> dict[str, Any]:
        if state.get("recap_ref") is not None:
            return {}
        intake_ref = state["intake_ref"]
        output = IntakeOutput.model_validate_json(store.read(intake_ref.artifact_id))
        try:
            ref = store.put(
                name="intake-recap.json", content=output.recap.model_dump_json().encode()
            )
        except RepoError:
            return {
                "status": IntakeGraphStatus.FAILED,
                "error_code": "artifact_store_unavailable",
            }
        return {"recap_ref": ref}

    def lock_node(state: IntakeState) -> dict[str, Any]:
        request = _load_request(state, store)
        readiness = _load_readiness(state, store)
        try:
            locked = lock_goal(
                uow,
                user_id=request.user_id,
                goal=request.goal,
                version=request.version,
                readiness=readiness,
                locked_on=clock.now().date(),
            )
        except IlluminationError as exc:
            return {"status": IntakeGraphStatus.FAILED, "error_code": exc.code}
        return {
            "status": IntakeGraphStatus.LOCKED,
            "version_id": locked.version.version_id,
            "lock_created": locked.created,
        }

    graph = StateGraph(IntakeState)
    graph.add_node("intake", intake_node)
    graph.add_node("readiness", readiness_node)
    graph.add_node("recap", recap_node)
    graph.add_node("lock", lock_node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges(
        "intake",
        lambda state: "readiness" if _running(state) else END,
        {"readiness": "readiness", END: END},
    )
    graph.add_edge("readiness", "recap")
    graph.add_conditional_edges(
        "recap",
        lambda state: "lock" if _running(state) else END,
        {"lock": "lock", END: END},
    )
    graph.add_edge("lock", END)
    return graph.compile(checkpointer=checkpointer)


async def run_intake_graph(
    *,
    request: IntakeGraphRequest,
    llm: LLMPort,
    clock: ClockPort,
    store: ArtifactStorePort,
    uow: UnitOfWorkPort,
    checkpointer: Any,
    thread_id: str,
) -> IntakeGraphResult:
    """启动一个入口线程；输入正文在进图前写入 artifact。"""
    input_ref = store.put(name="intake-input.json", content=request.model_dump_json().encode())
    initial: IntakeState = {
        "goal_id": request.goal.goal_id,
        "input_ref": input_ref,
        "status": IntakeGraphStatus.RUNNING,
    }
    graph = _build_intake_graph(
        llm=llm, clock=clock, store=store, uow=uow, checkpointer=checkpointer
    )
    final = await graph.ainvoke(initial, config=_config(thread_id))
    return _to_result(thread_id, final, store)


async def resume_intake_graph(
    *,
    llm: LLMPort,
    clock: ClockPort,
    store: ArtifactStorePort,
    uow: UnitOfWorkPort,
    checkpointer: Any,
    thread_id: str,
) -> IntakeGraphResult:
    """从既有 checkpoint 恢复，不重新注入输入，也不新增对话轮。"""
    graph = _build_intake_graph(
        llm=llm, clock=clock, store=store, uow=uow, checkpointer=checkpointer
    )
    final = await graph.ainvoke(None, config=_config(thread_id))
    return _to_result(thread_id, final, store)


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _running(state: IntakeState) -> bool:
    return state.get("status") is IntakeGraphStatus.RUNNING


def _load_request(state: IntakeState, store: ArtifactStorePort) -> IntakeGraphRequest:
    return IntakeGraphRequest.model_validate_json(store.read(state["input_ref"].artifact_id))


def _load_readiness(state: IntakeState, store: ArtifactStorePort) -> ReadinessResult:
    ref = state["readiness_ref"]
    return ReadinessResult.model_validate_json(store.read(ref.artifact_id))


def _to_result(
    thread_id: str, state: dict[str, Any], store: ArtifactStorePort
) -> IntakeGraphResult:
    readiness_ref = state.get("readiness_ref")
    readiness = (
        ReadinessResult.model_validate_json(store.read(readiness_ref.artifact_id))
        if readiness_ref is not None
        else None
    )
    return IntakeGraphResult(
        thread_id=thread_id,
        goal_id=state["goal_id"],
        status=state.get("status", IntakeGraphStatus.FAILED),
        summary=state.get("summary"),
        error_code=state.get("error_code"),
        readiness=readiness,
        intake_ref=state.get("intake_ref"),
        readiness_ref=readiness_ref,
        recap_ref=state.get("recap_ref"),
        version_id=state.get("version_id"),
        lock_created=state.get("lock_created", False),
    )
