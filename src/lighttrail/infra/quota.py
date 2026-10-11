"""本应用已观测用量：内存账本、费用估算与本地水位护栏，不是平台余额。

默认计价依据 ECNU 官方 2026-10-07 配额页面：7 天滚动 20000 credits；谷价
plus 未缓存/缓存/输出为 100/20/400，max 为 300/60/1200 credits/M。
北京时间每日 [08:00,18:00) 乘二，仅明确配置的国庆日期不执行峰价。
来源：https://developer.ecnu.edu.cn/vitepress/llm/limit.html。
计价、峰价时段、时区、例外日期和窗口均可注入（ADR-002）。输出包含思考子集，
不重复相加。缺失 usage、异常与缓存拆分未知另行登记，不伪造确定零费用；
进程重启会清空本账本，历史 trace 的用量摘要继续保留。能力降级仍由 Router 解析。
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from lighttrail.llm.router import ModelRouter, RouteIntent, RouterError

# 默认阈值（对齐 ECNU 官方配额；可通过构造参数/环境变量覆盖）
_DEFAULT_WINDOWS = (("weekly", 7 * 24 * 3600.0, 20000.0),)
DEFAULT_VALLEY_DATES = tuple(f"2026-10-{day:02d}" for day in range(1, 8))
_DEFAULT_WARN_THRESHOLD = 0.9


@dataclass(frozen=True)
class Pricing:
    """模型计价（credits / 百万 token）。"""

    input_per_m: float
    output_per_m: float
    cached_input_per_m: float = 0.0
    peak_multiplier: float = 1.0

    def __post_init__(self) -> None:
        """拒绝负数和非有限计价，避免配置造成确定零费或无效水位。"""
        if (
            any(
                not math.isfinite(value) or value < 0
                for value in (
                    self.input_per_m,
                    self.output_per_m,
                    self.cached_input_per_m,
                    self.peak_multiplier,
                )
            )
            or self.peak_multiplier < 1
        ):
            raise QuotaError("计价须为非负有限值，峰价倍率须 >= 1")


# 默认计价表（对齐 ECNU 官方）；键为模型名（config 默认值），可整体注入替换
DEFAULT_PRICING: dict[str, Pricing] = {
    "ecnu-plus": Pricing(100.0, 400.0, 20.0, 2.0),
    "ecnu-max": Pricing(300.0, 1200.0, 60.0, 2.0),
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
    """本地观测水位快照；旧三窗口字段仅供显式注入的兼容配置使用。"""

    hours_ratio: float = 0.0
    daily_ratio: float = 0.0
    monthly_ratio: float = 0.0
    weekly_ratio: float = 0.0
    window_ratios: dict[str, float] = field(default_factory=dict)
    observed_credits: float = 0.0
    unknown_attempts: int = 0
    scope: str = "本应用已观测用量（内存账本，非平台余额）"

    @property
    def total_credits(self) -> float | None:
        """存在未知扣费时不提供确定总费用。"""
        return None if self.unknown_attempts else self.observed_credits

    @property
    def peak_ratio(self) -> float:
        """最高窗口水位（判定告警/降级用）。"""
        return max(
            self.hours_ratio,
            self.daily_ratio,
            self.monthly_ratio,
            self.weekly_ratio,
            *self.window_ratios.values(),
        )


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
        peak_hours: tuple[int, int] | None = (8, 18),
        pricing_tz_offset: float = 8.0,
        valley_dates: tuple[str, ...] = DEFAULT_VALLEY_DATES,
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
            peak_hours: 供应商本地峰价时段，None 禁用；默认 [8,18)。
            pricing_tz_offset: 计价时区的固定小时偏移，不随拍摄地点改变。
            valley_dates: 明确的全天谷价日期，不推断未来节假日。
        """
        self._pricing: dict[str, Pricing] = dict(pricing or DEFAULT_PRICING)
        self._windows: tuple[tuple[str, float, float], ...] = windows or _DEFAULT_WINDOWS
        if len({name for name, _, _ in self._windows}) != len(self._windows) or any(
            not name
            or not math.isfinite(seconds)
            or seconds <= 0
            or not math.isfinite(limit)
            or limit <= 0
            for name, seconds, limit in self._windows
        ):
            raise QuotaError("窗口名称须唯一，时长与额度须为正有限值")
        if not 0.0 < warn_threshold < 1.0:
            raise QuotaError("warn_threshold 须在 (0, 1) 之间")
        self._warn_threshold = warn_threshold
        self._degrade_map: dict[str, str] = dict(degrade_map or {"deep": "tools"})
        self._now = now
        if peak_hours is not None and not (0 <= peak_hours[0] < peak_hours[1] <= 24):
            raise QuotaError("峰价时段须满足 0 <= 开始 < 结束 <= 24")
        self._peak_hours = peak_hours
        self._pricing_tz = timezone(timedelta(hours=pricing_tz_offset))
        self._valley_dates = frozenset(valley_dates)
        # 记录：[(timestamp, credits)]，按加入顺序追加，查询时按窗口裁剪
        self._records: list[tuple[float, float]] = []
        self._unknown: list[tuple[float, str]] = []
        # 并发记账锁（B3-5 / v4 D5 配套）：LLM 并发（默认 4）下 record 与 usage 必须原子
        self._lock = threading.Lock()

    # ------ 对外接口：记账 ------
    def record(
        self, model: str, in_tokens: int, out_tokens: int, *, cached_input_tokens: int = 0
    ) -> float:
        """按计价表把一次调用的 token 用量折算为 credits 并记账。

        Args:
            model: 模型名。
            in_tokens: 输入 token 数。
            out_tokens: 输出 token 数。
            cached_input_tokens: 命中缓存的输入 token 数（按命中价单独计价）。

        Returns:
            按已返回用量和配置价格估算的 credits，不是供应商结算确认。
        """
        if in_tokens < 0 or out_tokens < 0 or cached_input_tokens < 0:
            raise QuotaError("token 数量不能为负")
        price = self._pricing.get(model)
        if price is None:
            raise QuotaError(f"缺少模型 {model} 的计价配置（可注入 pricing 补充）")
        if cached_input_tokens > in_tokens:
            raise QuotaError("缓存输入不能超过总输入")
        cached = cached_input_tokens
        fresh_input = in_tokens - cached
        credits = (
            (
                fresh_input * price.input_per_m
                + cached * price.cached_input_per_m
                + out_tokens * price.output_per_m
            )
            / 1_000_000.0
            * self._multiplier(price)
        )
        with self._lock:
            self._records.append((self._clock(), credits))
        return credits

    def record_unknown(self, reason: str) -> None:
        """记录已发送请求的未知扣费，不将缺失 usage 或超时视为零费用。"""
        with self._lock:
            self._unknown.append((self._clock(), reason))

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
            if (
                min(in_tokens, out_tokens, cached_input_tokens) < 0
                or cached_input_tokens > in_tokens
            ):
                raise QuotaError("预估 token 非法或缓存超过总输入")
            price = self._pricing.get(model)
            if price is None:
                raise QuotaError(f"缺少模型 {model} 的计价配置（可注入 pricing 补充）")
            cached = min(cached_input_tokens, in_tokens)
            total += (
                (
                    (in_tokens - cached) * price.input_per_m
                    + cached * price.cached_input_per_m
                    + out_tokens * price.output_per_m
                )
                / 1_000_000.0
                * self._multiplier(price)
            )
        return total

    def check(self, estimate_credits: float = 0.0) -> bool:
        """本地放行检查：估计开销后各配置窗口不超限，不证明平台有余额。

        Args:
            estimate_credits: 本次调用的预估 credits（管线入口先估后调）。

        Returns:
            False 表示任一窗口即将超限，应降级或中止。
        """
        if estimate_credits < 0:
            raise QuotaError("预估 credits 不能为负")
        usage = self.usage()
        return all(
            usage.window_ratios[name] * limit + estimate_credits <= limit
            for name, _, limit in self._windows
        )

    def usage(self) -> UsageSnapshot:
        """返回本地窗口水位及未知次数；未知存在时总费用为 None。"""
        now = self._clock()
        ratios: dict[str, float] = {}
        with self._lock:
            records = list(self._records)
            unknown = len(self._unknown)
        for name, seconds, limit in self._windows:
            total = sum(credits for ts, credits in records if 0 <= now - ts < seconds)
            ratios[name] = total / limit if limit > 0 else 0.0
        return UsageSnapshot(
            hours_ratio=ratios.get("hours", 0.0),
            daily_ratio=ratios.get("daily", 0.0),
            monthly_ratio=ratios.get("monthly", 0.0),
            weekly_ratio=ratios.get("weekly", 0.0),
            window_ratios=ratios,
            observed_credits=sum(credits for _, credits in records),
            unknown_attempts=unknown,
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
            f"本应用已观测用量水位 {self.usage().peak_ratio * 100:.0f}% 超过本地阈值 "
            f"{self._warn_threshold * 100:.0f}%，{capability} 步骤降级为 {fallback} "
            f"（模型 {target_model}）"
        )

    # ------ 内部实现 ------
    def _multiplier(self, price: Pricing) -> float:
        """按配置的供应商时区判定峰价；假日例外仅覆盖明确列出的日期。"""
        local = datetime.fromtimestamp(self._clock(), self._pricing_tz)
        if self._peak_hours is None or local.date().isoformat() in self._valley_dates:
            return 1.0
        start, end = self._peak_hours
        return price.peak_multiplier if start <= local.hour < end else 1.0

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
