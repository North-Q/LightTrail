"""配额账本：按 token 记账 + 成本预估 + 水位告警与降级链（Q：ECNU 平台配额）。

设计要点（架构 v2.0 §2.3 增补，E4-2 落地）：
- 三窗口滚动记账（5 小时 / 24 小时 / 30 天），只算 credits（由计价表把 token
  折算为 credits），阈值可配置；
- **平台中立性（ADR-002）**：计价表与「降级链」都是**可注入配置**，默认值对齐
  ECNU 官方计价（plus：输入 100/M、命中 20/M、输出 400/M；max：输入 300/M、输出 400/M；
  每 5h 2000 / 日 5000 / 月 50000 credits），但换 API 时注入新计价/新降级链即可，
  账本逻辑与模型品牌无关；降级链按**能力**表达（如 deep → tools+thinking），
  具体模型名由 ModelRouter 能力矩阵解析，绝不硬编码 ecnu-max → ecnu-plus；
- 置信度/水位的「降级原因」文本由账本产出，随 TraceReport 输出（M2 可解释性）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from lighttrail.llm.router import ModelRouter, RouteIntent, RouterError

# 默认阈值（对齐 ECNU 官方配额；可通过构造参数/环境变量覆盖）
_DEFAULT_WINDOWS = (
    ("hours", 5 * 3600.0, 2000.0),
    ("daily", 24 * 3600.0, 5000.0),
    ("monthly", 30 * 24 * 3600.0, 50000.0),
)
_DEFAULT_WARN_THRESHOLD = 0.9


@dataclass(frozen=True)
class Pricing:
    """模型计价（credits / 百万 token）。"""

    input_per_m: float
    output_per_m: float
    cached_input_per_m: float = 0.0


# 默认计价表（对齐 ECNU 官方）；键为模型名（config 默认值），可整体注入替换
DEFAULT_PRICING: dict[str, Pricing] = {
    "ecnu-plus": Pricing(input_per_m=100.0, output_per_m=400.0, cached_input_per_m=20.0),
    "ecnu-max": Pricing(input_per_m=300.0, output_per_m=400.0),
}


@dataclass(frozen=True)
class CallPlan:
    """一次管线调用的成本预估（供 check/estimate 使用）。

    Attributes:
        steps: 预估步骤列表，每项为 (模型名, 输入token, 输出token, 命中输入token)。
    """

    steps: tuple[tuple[str, int, int, int], ...] = ()


@dataclass(frozen=True)
class UsageSnapshot:
    """三窗口水位快照（比率 = 已用 / 限额）。"""

    hours_ratio: float = 0.0
    daily_ratio: float = 0.0
    monthly_ratio: float = 0.0

    @property
    def peak_ratio(self) -> float:
        """最高窗口水位（判定告警/降级用）。"""
        return max(self.hours_ratio, self.daily_ratio, self.monthly_ratio)


class QuotaError(Exception):
    """配额账本参数非法。"""


class QuotaLedger:
    """配额账本：记账 / 预估 / 放行检查 / 降级建议（全部可通过注入解耦平台）。"""

    def __init__(
        self,
        *,
        pricing: dict[str, Pricing] | None = None,
        windows: tuple[tuple[str, float, float], ...] | None = None,
        warn_threshold: float = _DEFAULT_WARN_THRESHOLD,
        degrade_map: dict[str, str] | None = None,
        now: float | None = None,
    ) -> None:
        """初始化账本。

        Args:
            pricing: 计价表覆盖（缺省对齐 ECNU 官方计价）。
            windows: 滚动窗口 (名称, 秒数, credits 限额) 覆盖。
            warn_threshold: 告警水位（默认 0.9，LIGHTTRAIL_QUOTA_WARN_THRESHOLD 对齐）。
            degrade_map: 降级链（能力 → 降级后的能力步骤），默认 {"deep": "tools"}，
                表示深推理步骤在到顶时降级为工具模型+thinking。键/值都是能力语义，
                模型名由 router 解析。
            now: 当前时间戳（秒，测试可注入；缺省取系统时间）。
        """
        self._pricing: dict[str, Pricing] = dict(pricing or DEFAULT_PRICING)
        self._windows: tuple[tuple[str, float, float], ...] = windows or _DEFAULT_WINDOWS
        if not 0.0 < warn_threshold < 1.0:
            raise QuotaError("warn_threshold 须在 (0, 1) 之间")
        self._warn_threshold = warn_threshold
        self._degrade_map: dict[str, str] = dict(degrade_map or {"deep": "tools"})
        self._now = now
        # 记录：[(timestamp, credits)]，按加入顺序追加，查询时按窗口裁剪
        self._records: list[tuple[float, float]] = []

    # ------ 对外接口：记账 ------
    def record(self, model: str, in_tokens: int, out_tokens: int, *, cached_input_tokens: int = 0) -> float:
        """按计价表把一次调用的 token 用量折算为 credits 并记账。

        Args:
            model: 模型名。
            in_tokens: 输入 token 数。
            out_tokens: 输出 token 数。
            cached_input_tokens: 命中缓存的输入 token 数（按命中价单独计价）。

        Returns:
            本次调用消耗的 credits。
        """
        if in_tokens < 0 or out_tokens < 0 or cached_input_tokens < 0:
            raise QuotaError("token 数量不能为负")
        price = self._pricing.get(model)
        if price is None:
            raise QuotaError(f"缺少模型 {model} 的计价配置（可注入 pricing 补充）")
        cached = min(cached_input_tokens, in_tokens)
        fresh_input = in_tokens - cached
        credits = (
            fresh_input * price.input_per_m
            + cached * price.cached_input_per_m
            + out_tokens * price.output_per_m
        ) / 1_000_000.0
        self._records.append((self._clock(), credits))
        return credits

    # ------ 对外接口：预估与放行 ------
    def estimate(self, plan: CallPlan) -> float:
        """按计价表预估一次调用计划的 credits 开销（不记账）。

        Args:
            plan: 调用计划（每步含模型与 token 预估）。

        Returns:
            预估 credits 合计。
        """
        total = 0.0
        for model, in_tokens, out_tokens, cached_input_tokens in plan.steps:
            price = self._pricing.get(model)
            if price is None:
                raise QuotaError(f"缺少模型 {model} 的计价配置（可注入 pricing 补充）")
            cached = min(cached_input_tokens, in_tokens)
            total += (
                (in_tokens - cached) * price.input_per_m
                + cached * price.cached_input_per_m
                + out_tokens * price.output_per_m
            ) / 1_000_000.0
        return total

    def check(self, estimate_credits: float = 0.0) -> bool:
        """放行检查：估计开销后三窗口都不会超限。

        Args:
            estimate_credits: 本次调用的预估 credits（管线入口先估后调）。

        Returns:
            False 表示任一窗口即将超限，应降级或中止。
        """
        if estimate_credits < 0:
            raise QuotaError("预估 credits 不能为负")
        usage = self.usage()
        ratios = (usage.hours_ratio, usage.daily_ratio, usage.monthly_ratio)
        used = [r * limit for r, (_, _, limit) in zip(ratios, self._windows)]
        return all(u + estimate_credits <= limit for u, (_, _, limit) in zip(used, self._windows))

    def usage(self) -> UsageSnapshot:
        """返回三窗口水位快照（滚动窗口内 credits 合计 / 限额）。"""
        now = self._clock()
        ratios: dict[str, float] = {}
        for name, seconds, limit in self._windows:
            total = sum(credits for ts, credits in self._records if now - ts < seconds)
            ratios[name] = total / limit if limit > 0 else 0.0
        return UsageSnapshot(
            hours_ratio=ratios.get("hours", 0.0),
            daily_ratio=ratios.get("daily", 0.0),
            monthly_ratio=ratios.get("monthly", 0.0),
        )

    # ------ 对外接口：降级建议 ------
    def degrade(self, intent: RouteIntent, router: ModelRouter) -> str | None:
        """水位超阈时给出降级建议（能力级降级链 + 矩阵解析模型名）。

        Args:
            intent: 本次调用的路由意图。
            router: 能力矩阵路由器（解析降级后的实际模型名）。

        Returns:
            降级原因文本（可直接进 TraceReport / DecisionCard 标注）；
            水位正常或意图无需降级时返回 None。
        """
        if self.usage().peak_ratio <= self._warn_threshold:
            return None
        capability = _intent_capability(intent)
        fallback = self._degrade_map.get(capability)
        if fallback is None or fallback == capability:
            return None
        target_model = _resolve_fallback_model(fallback, router)
        return (
            f"配额水位 {self.usage().peak_ratio * 100:.0f}% 超过阈值 "
            f"{self._warn_threshold * 100:.0f}%，{capability} 步骤降级为 {fallback} "
            f"（模型 {target_model}）"
        )

    # ------ 内部实现 ------
    def _clock(self) -> float:
        """当前时间戳（测试注入 now 时用注入值）。"""
        return self._now if self._now is not None else time.time()


def _intent_capability(intent: RouteIntent) -> str:
    """路由意图 → 能力名（降级链键）。"""
    if intent is RouteIntent.DEEP_REASONING:
        return "deep"
    if intent is RouteIntent.TOOLS:
        return "tools"
    if intent is RouteIntent.VISION:
        return "vision"
    return "default"


def _resolve_fallback_model(fallback: str, router: ModelRouter) -> str:
    """把降级链目标（能力语义）解析为模型名（矩阵驱动，品牌无关）。"""
    if fallback == "tools":
        return router.resolve(needs_tools=True)
    if fallback == "vision":
        return router.resolve(needs_vision=True)
    if fallback == "deep":
        return router.resolve(needs_deep_reasoning=True)
    raise RouterError(f"未知的降级链目标能力：{fallback}")
