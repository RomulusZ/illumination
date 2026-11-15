"""LangGraph pipeline: plan → finalize."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from illumination.repo.errors import RepoError
from illumination.service.errors import IlluminationError
from illumination.service.planning import plan_run
from illumination.types.common import ArtifactRef, RunStatus, StepStatus
from illumination.types.plan import RunPlan, RunRequest, RunResult, StepOutcome
from illumination.types.ports import ArtifactStorePort, ClockPort, LLMPort


class RunState(TypedDict, total=False):
    """Mutable graph state; only pointers and summaries live here."""

    run_id: str
    request: RunRequest
    started_at: datetime
    plan: RunPlan
    outcomes: list[StepOutcome]
    artifacts: list[ArtifactRef]
    status: RunStatus
    error_code: str
    finished_at: datetime


def _build_run_graph(
    *,
    llm: LLMPort,
    clock: ClockPort,
    store: ArtifactStorePort,
) -> Any:
    """Build the two-node graph. Execution nodes are the next extension seam."""

    async def plan_node(state: RunState) -> dict[str, Any]:
        try:
            plan = await plan_run(llm, state["request"])
        except IlluminationError as exc:
            return {"status": RunStatus.FAILED, "error_code": exc.code}

        outcomes = [
            StepOutcome(
                step_id=step.step_id,
                status=StepStatus.SKIPPED,
                summary="骨架阶段：执行器未接入，步骤仅进入计划",
            )
            for step in plan.steps
        ]
        return {"plan": plan, "outcomes": outcomes, "status": RunStatus.PARTIAL}

    async def finalize_node(state: RunState) -> dict[str, Any]:
        artifacts = list(state.get("artifacts", []))
        status = state.get("status", RunStatus.FAILED)
        error_code = state.get("error_code")
        plan = state.get("plan")
        if plan is not None:
            try:
                artifacts.append(
                    store.put(
                        name="plan.json",
                        content=plan.model_dump_json(indent=2).encode("utf-8"),
                    )
                )
            except RepoError:
                status = RunStatus.FAILED
                error_code = "artifact_store_unavailable"
        return {
            "artifacts": artifacts,
            "status": status,
            "error_code": error_code,
            "finished_at": clock.now(),
        }

    graph = StateGraph(RunState)
    graph.add_node("plan", plan_node)
    graph.add_node("finalize", finalize_node)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()


async def run_once(
    *,
    request: RunRequest,
    llm: LLMPort,
    clock: ClockPort,
    store: ArtifactStorePort,
    run_id_prefix: str = "ill",
) -> RunResult:
    """Execute one run and return its terminal result."""
    started_at = clock.now()
    run_id = build_run_id(run_id_prefix, request.objective, started_at)
    initial_state: RunState = {
        "run_id": run_id,
        "request": request,
        "started_at": started_at,
        "outcomes": [],
        "artifacts": [],
        "status": RunStatus.RUNNING,
    }
    graph = _build_run_graph(llm=llm, clock=clock, store=store)
    final_state: dict[str, Any] = await graph.ainvoke(initial_state)

    return RunResult(
        run_id=run_id,
        status=final_state.get("status", RunStatus.FAILED),
        plan=final_state.get("plan"),
        outcomes=tuple(final_state.get("outcomes", [])),
        artifacts=tuple(final_state.get("artifacts", [])),
        started_at=started_at,
        finished_at=final_state.get("finished_at") or clock.now(),
        error_code=final_state.get("error_code"),
    )


def build_run_id(prefix: str, objective: str, started_at: datetime) -> str:
    """Build a stable run id from the objective and start instant."""
    digest = hashlib.sha256(f"{objective}|{started_at.isoformat()}".encode()).hexdigest()
    return f"{prefix}_{digest[:16]}"
