"""置信度规则表：把「工具来源 → 置信度」的判定规则化，替代模型自评（M2）。

设计要点（架构 v2.0 §2.6，E2-2 落地；B4-3 增补卡片明细推导）：
- 模型自评置信度不可控，规则必须在代码里确定性地推导；
- 规则分层：
  1. 确定性来源（天文/曝光纯计算，结果不依赖外部数据）→ high；
  2. 天气预报（外部网口数据）→ 时效敏感：覆盖当日 → high（≤6h 语义近似），跨天 → medium；
  3. 启发式组合（火烧云评分 / 机位匹配，经验模型叠加）→ medium；
  4. 未知工具：带数据来源字段 → medium，否则 → low；
- 规则随数据字段细化时在此表追加（如：「云图外推 → medium」），供 E8 评估回归；
- `confidence_detail()` 把卡片等级 + 依据列表推导为「主值 + 区间 + 依据构成」，
  前端只渲染不下判断（B4-3 根治 `D3Page` 硬编码常量表 78/58/34）。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from lighttrail.contracts.models import ConfidenceDetail, Source
from lighttrail.contracts.tool import ToolSpec

# 确定性来源：纯计算 / 天文历法，不依赖外部数据与预测
DETERMINISTIC_TOOLS = frozenset(
    {
        "get_current_time",
        "equivalent_exposure",
        "star_shutter_rule",
        "nd_long_exposure",
        "sun_times",
        "sun_position",
        "moon_phase",
        "moon_events",
        "galaxy_visibility",
    }
)
# 启发式组合：经验模型/多源叠加，置信度中等
HEURISTIC_TOOLS = frozenset({"sunset_glow_score", "match_sites"})
# 时效敏感：预报越接近当下越可靠
FORECAST_TOOLS = frozenset({"weather_forecast"})

_CONF_HIGH = "high"
_CONF_MEDIUM = "medium"
_CONF_LOW = "low"

# ------ 卡片置信度明细推导（B4-3） ------
# 来源级别权重：决定置信度主值（规则推导，不做模型自评）
_LEVEL_WEIGHT = {"high": 0.85, "medium": 0.6, "low": 0.35}
# 无依据时的等级兜底权重（依据缺失就诚实退回等级带，不假装精确）
_LEVEL_FALLBACK = {"high": 0.8, "medium": 0.55, "low": 0.3}
# 区间半宽：依据越多区间越窄（体现「依据越充分越确定」）
_MIN_SPREAD = 6
_MAX_SPREAD = 26
_SPREAD_PER_EVIDENCE = 2


def resolve_confidence(spec: ToolSpec, result_data: dict[str, Any], *, now: datetime | None = None) -> str:
    """按 ToolSpec 声明解析置信度（动态规则优先，静态声明兜底）。

    Args:
        spec: 工具自描述（confidence / confidence_rule）。
        result_data: 工具返回的结构化结果（动态规则用）。
        now: 当前时刻（测试可注入）。

    Returns:
        high / medium / low 之一。
    """
    if spec.confidence_rule == "forecast":
        return _forecast_confidence(result_data, now)
    return spec.confidence.value


def confidence_for_tool(name: str, result_data: dict[str, Any], *, now: datetime | None = None) -> str:
    """按规则派生某次工具调用的置信度。

    Args:
        name: 工具名。
        result_data: 工具返回的 dict（JSON 已解析）。
        now: 当前时刻（测试可注入；缺省取系统当前时间）。

    Returns:
        high / medium / low 之一。
    """
    if name in DETERMINISTIC_TOOLS:
        return _CONF_HIGH
    if name in FORECAST_TOOLS:
        return _forecast_confidence(result_data, now)
    if name in HEURISTIC_TOOLS:
        return _CONF_MEDIUM
    if _has_source(result_data):
        return _CONF_MEDIUM
    return _CONF_LOW


def confidence_detail(level: str, evidence: Sequence[Source]) -> ConfidenceDetail:
    """把卡片等级 + 依据列表推导为置信度明细（主值 / 区间 / 构成）。

    规则（确定性、可测、非概率估计）：
    - 主值 = 各条依据「来源级别权重」的加权平均（high 0.85 / medium 0.6 / low 0.35）；
    - 区间半宽随依据条数收窄（26 − 2×条数，夹在 6–26），依据越充分越确定；
    - 无依据时退回等级兜底带，并在 basis 里如实说明，不假装精确。

    Args:
        level: 卡片整体等级（high / medium / low；非法值按 low 处理）。
        evidence: 卡片依据列表（每条的 confidence 为来源级别）。

    Returns:
        ConfidenceDetail：前端铁律①（主值 + 区间条 + 依据）直接渲染的结构化字段。
    """
    counts = {_CONF_HIGH: 0, _CONF_MEDIUM: 0, _CONF_LOW: 0}
    for item in evidence:
        key = (item.confidence or "").strip().lower()
        counts[key if key in counts else _CONF_LOW] += 1
    total = sum(counts.values())
    normalized = (level or "").strip().lower()
    if normalized not in _LEVEL_WEIGHT:
        normalized = _CONF_LOW
    if total:
        score = round(100 * sum(_LEVEL_WEIGHT[key] * count for key, count in counts.items()) / total)
        spread = max(_MIN_SPREAD, _MAX_SPREAD - _SPREAD_PER_EVIDENCE * total)
        basis = (
            f"按 {total} 条依据的来源级别加权（确定性 high {counts[_CONF_HIGH]} 条 / "
            f"外部或启发式 {counts[_CONF_MEDIUM] + counts[_CONF_LOW]} 条）"
        )
    else:
        score = round(100 * _LEVEL_FALLBACK[normalized])
        spread = _MIN_SPREAD
        basis = f"无可溯源依据，退回「{normalized}」等级带（{score}%）"
    return ConfidenceDetail(
        level=normalized,
        score=score,
        low=max(0, score - spread),
        high=min(100, score + spread),
        basis=basis,
        high_count=counts[_CONF_HIGH],
        medium_count=counts[_CONF_MEDIUM],
        low_count=counts[_CONF_LOW],
    )


def _forecast_confidence(result_data: dict[str, Any], now: datetime | None) -> str:
    """天气预报置信度：覆盖当日 → high（时效 ≤6h 的日期级近似），跨天 → medium。"""
    entries = result_data.get("每日预报")
    if not isinstance(entries, list) or not entries:
        # 无有效预报条目：退化为「有外部数据 → medium」
        return _CONF_MEDIUM
    today = (now.date() if now is not None else datetime.now(timezone.utc).date()).isoformat()
    for entry in entries:
        day = str(entry.get("日期", ""))
        if day.startswith(today):
            return _CONF_HIGH
    return _CONF_MEDIUM


def _has_source(result_data: dict[str, Any]) -> bool:
    """结果中是否带数据来源标注（数据来源 / 来源 / data_source）。"""
    return any(key in result_data for key in ("数据来源", "来源", "data_source"))


__all__ = [
    "DETERMINISTIC_TOOLS",
    "FORECAST_TOOLS",
    "HEURISTIC_TOOLS",
    "confidence_detail",
    "confidence_for_tool",
    "resolve_confidence",
]