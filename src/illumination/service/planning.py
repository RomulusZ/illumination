"""Turn one objective into a bounded structured plan."""

from __future__ import annotations

from illumination.service.errors import PlanningError
from illumination.service.llm_structured import complete_structured
from illumination.types.llm import LLMMessage, LLMRequest, LLMRole
from illumination.types.plan import RunPlan, RunRequest
from illumination.types.ports import LLMPort

PLANNER_SYSTEM_PROMPT = """你是任务规划器。只输出一个 JSON 对象，不要输出解释或 Markdown。
字段要求：
- objective：字符串，保持与用户目标一致；
- steps：数组，每项含 step_id（唯一，如 s1）、action（可执行动作）、rationale（为什么需要）；
- assumptions：字符串数组，可为空。
步骤数不得超过用户给出的上限。"""


def build_planning_request(request: RunRequest) -> LLMRequest:
    """Assemble a deterministic planning prompt from the request."""
    context = "\n".join(f"- {item}" for item in request.context) or "- 无"
    user_content = f"目标：{request.objective}\n上下文：\n{context}\n步骤上限：{request.max_steps}"
    return LLMRequest(
        messages=(
            LLMMessage(role=LLMRole.SYSTEM, content=PLANNER_SYSTEM_PROMPT),
            LLMMessage(role=LLMRole.USER, content=user_content),
        ),
        metadata={
            "task": "planning",
            "objective": request.objective,
            "max_steps": str(request.max_steps),
        },
    )


async def plan_run(port: LLMPort, request: RunRequest) -> RunPlan:
    """Produce a validated plan, bounded by ``RunRequest.max_steps``."""
    plan = await complete_structured(port, build_planning_request(request), RunPlan)
    if len(plan.steps) > request.max_steps:
        raise PlanningError(f"计划步骤数 {len(plan.steps)} 超过上限 {request.max_steps}")
    return plan
