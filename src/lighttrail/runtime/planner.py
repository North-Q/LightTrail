"""受控 Plan-Execute 通道：计划可枚举、可审计，执行不绕过注册表。"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from lighttrail.contracts.models import DecisionCard
from lighttrail.contracts.plan import Plan, PlanStep
from lighttrail.contracts.tool import ToolContext
from lighttrail.infra.quota import QuotaLedger
from lighttrail.infra.trace import Recorder, null_trace
from lighttrail.infra.validation import parse_with_retry
from lighttrail.runtime.registry import ToolRegistry


class _PlanStepPayload(BaseModel):
    """LLM 生成计划的校验载荷。"""

    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    purpose: str = ""


class _PlanPayload(BaseModel):
    """LLM 生成计划的校验载荷。"""

    steps: list[_PlanStepPayload] = Field(default_factory=list)


_PLAN_SYSTEM = "你是 LightTrail 的受控拍摄任务规划器，只能使用给定工具。"
_CARD_SYSTEM = "你是 LightTrail 拍摄决策引擎，根据已执行工具结果输出决策卡。"
_CARD_INSTRUCTION = """只输出 JSON：
{"conclusion":"结论","evidence":[],"confidence":"low|medium|high","verdict":"go|wait|risk","time_window":"","window_start":"","window_end":"","locations":[],"params":[],"alternatives":[],"degraded":""}"""


class Planner:
    """受控规划器：LLM 只产计划，执行权始终在代码。"""

    def __init__(
        self,
        registry: ToolRegistry,
        complete: Callable[[str, str], str],
        reason: Callable[[str, str], str],
        *,
        recorder: Recorder | None = None,
        quota: QuotaLedger | None = None,
        max_steps: int = 8,
    ) -> None:
        self._registry = registry
        self._complete = complete
        self._reason = reason
        self._recorder = recorder or null_trace
        self._quota = quota
        self._max_steps = max_steps

    def make_plan(self, goal: str) -> Plan:
        """生成并校验受控计划，超限计划由契约拒绝。"""
        prompt = (
            f"目标：{goal}\n可用工具：{', '.join(self._registry.names())}\n"
            "只输出 JSON：{\"steps\":[{\"tool\":\"工具名\",\"args\":{},\"purpose\":\"原因\"}]}"
        )
        payload = parse_with_retry(_PlanPayload, lambda text: self._complete(text, _PLAN_SYSTEM), prompt)
        plan = Plan(
            goal=goal,
            steps=tuple(PlanStep(tool=item.tool, args=item.args, purpose=item.purpose) for item in payload.steps),
            max_steps=self._max_steps,
        )
        self._recorder.record_step("规划_产出", input_summary=goal[:80], output_summary=f"{len(plan.steps)} 步")
        return plan

    def execute(self, plan: Plan, ctx: ToolContext | None = None) -> DecisionCard:
        """逐步执行计划；任一步失败或配额拒绝即生成带降级标记的决策卡。"""
        results: list[dict[str, Any]] = []
        degraded = ""
        for index, step in enumerate(plan.steps, start=1):
            if self._quota is not None and not self._quota.check():
                degraded = "quota_exhausted"
                self._recorder.record_step("规划_配额拒绝", input_summary=step.tool, output_summary=degraded)
                break
            raw = self._registry.dispatch(step.tool, json.dumps(step.args, ensure_ascii=False), ctx, recorder=self._recorder)
            try:
                data = json.loads(raw)
            except ValueError:
                data = {"raw": raw}
            results.append({"step": index, "tool": step.tool, "purpose": step.purpose, "result": data})
            self._recorder.record_step("规划_执行", input_summary=step.tool, output_summary=str(data)[:120])
            if isinstance(data, dict) and "error" in data:
                degraded = f"plan_step_failed:{step.tool}"
                break
        prompt = f"目标：{plan.goal}\n执行结果：{json.dumps(results, ensure_ascii=False)}\n{_CARD_INSTRUCTION}"
        card = parse_with_retry(DecisionCard, lambda text: self._reason(text, _CARD_SYSTEM), prompt)
        if degraded:
            card.degraded = degraded
        return card


__all__ = ["Planner"]
