"""Deterministic planner used by the offline CLI demo."""

from __future__ import annotations

import json

from illumination.types.llm import LLMRequest, LLMResponse, LLMUsage


class StubPlannerLLM:
    """LLMPort implementation that never performs I/O."""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        objective = request.metadata.get("objective", "")
        max_steps = int(request.metadata.get("max_steps", "2"))
        steps = [
            {
                "step_id": f"s{index}",
                "action": f"推进目标：{objective}",
                "rationale": "stub 模式只验证编排链路，不产生真实业务动作。",
            }
            for index in range(1, min(max_steps, 2) + 1)
        ]
        payload = {
            "objective": objective or "未命名目标",
            "steps": steps,
            "assumptions": ["stub 模式不调用外部模型"],
        }
        return LLMResponse(
            text=json.dumps(payload, ensure_ascii=False),
            model="stub-planner",
            usage=LLMUsage(input_tokens=0, output_tokens=0),
        )
