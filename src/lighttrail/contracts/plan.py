"""受控计划契约（B1-2，D3 修订 / §2.3）。

设计要点：
- `Plan` 是受控 Plan-Execute 通道的一等对象：LLM 产出后先过 pydantic 校验，
  再按步执行，每步过 dispatch / trace / 配额（B7-3 落地执行器）；
- 步数上限是护栏（settings.PLAN_MAX_STEPS，默认 8）：`Planner` 不得构造超限计划，
  契约层直接拒绝——护栏写进类型而不是靠调用方自觉；
- frozen：计划产出后不可变，便于落 trace、比对与评估回放。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 计划步数上限默认值（settings.PLAN_MAX_STEPS 的契约侧默认，B1-5 起与配置对齐）
DEFAULT_PLAN_MAX_STEPS = 8


@dataclass(frozen=True)
class PlanStep:
    """计划中的一步：调哪个工具、传什么参数、为什么。

    Attributes:
        tool: 工具名（须在 ToolRegistry 中已注册）。
        args: 工具参数（JSON 可序列化，落 trace 用）。
        purpose: 这一步的意图说明（供可解释性与评估使用）。
    """

    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    purpose: str = ""


@dataclass(frozen=True)
class Plan:
    """受控计划：一等对象，可落 trace、可测、有步数上限。

    Attributes:
        goal: 计划目标（通常来自用户请求或意图）。
        steps: 计划步骤序列。
        max_steps: 允许的最大步数（默认 8；由 settings.PLAN_MAX_STEPS 注入）。

    Raises:
        ValueError: 步数超过 max_steps（护栏前置拒绝，不做静默截断）。
    """

    goal: str
    steps: tuple[PlanStep, ...] = ()
    max_steps: int = DEFAULT_PLAN_MAX_STEPS

    def __post_init__(self) -> None:
        """校验步数护栏（超限直接拒绝，避免执行期失控）。"""
        if self.max_steps < 1:
            raise ValueError("max_steps 必须 >= 1")
        if len(self.steps) > self.max_steps:
            raise ValueError(f"计划步数 {len(self.steps)} 超过上限 {self.max_steps}")


__all__ = ["DEFAULT_PLAN_MAX_STEPS", "Plan", "PlanStep"]