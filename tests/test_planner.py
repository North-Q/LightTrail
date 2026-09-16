"""B7-3 受控 Planner 回归：计划校验、执行和降级。"""

from __future__ import annotations

import json

from lighttrail.contracts.plan import Plan, PlanStep
from lighttrail.infra.trace import TraceRecorder
from lighttrail.runtime.planner import Planner
from lighttrail.runtime.registry import ToolRegistry

_CARD = json.dumps(
    {"conclusion": "可以执行", "evidence": [], "confidence": "medium", "verdict": "go"},
    ensure_ascii=False,
)


def _registry() -> ToolRegistry:
    """构造最小注册表，验证执行必须经过注册表。"""
    registry = ToolRegistry()
    registry.register(lambda value: {"value": value}, name="lookup")
    registry.register(lambda: {"error": "数据源不可用"}, name="broken")
    return registry


def test_planner_makes_and_executes_valid_plan() -> None:
    """合法计划生成后逐步分发，计划和执行步骤均进入 trace。"""
    plan_json = json.dumps(
        {"steps": [{"tool": "lookup", "args": {"value": 7}, "purpose": "查询样本"}]},
        ensure_ascii=False,
    )
    recorder = TraceRecorder()
    planner = Planner(_registry(), lambda prompt, system: plan_json, lambda prompt, system: _CARD, recorder=recorder)

    plan = planner.make_plan("查询拍摄条件")
    card = planner.execute(plan)

    assert plan.steps == (PlanStep(tool="lookup", args={"value": 7}, purpose="查询样本"),)
    assert card.conclusion == "可以执行"
    names = [step.name for step in recorder.to_report().steps]
    assert "规划_产出" in names
    assert "规划_执行" in names


def test_planner_rejects_plan_over_step_limit() -> None:
    """模型产出的超限计划由 Plan 契约前置拒绝。"""
    plan_json = json.dumps({"steps": [{"tool": "lookup"}] * 2}, ensure_ascii=False)
    planner = Planner(_registry(), lambda prompt, system: plan_json, lambda prompt, system: _CARD, max_steps=1)

    try:
        planner.make_plan("超限计划")
    except ValueError as exc:
        assert "超过上限" in str(exc)
    else:
        raise AssertionError("超限计划未被拒绝")


def test_planner_marks_failed_step_as_degraded() -> None:
    """单步返回错误时停止后续执行，并在卡片标注可审计降级原因。"""
    plan = Plan(goal="失败场景", steps=(PlanStep(tool="broken"), PlanStep(tool="lookup", args={"value": 1})))
    planner = Planner(_registry(), lambda prompt, system: "{}", lambda prompt, system: _CARD)

    card = planner.execute(plan)

    assert card.degraded == "plan_step_failed:broken"
