"""置信度规则表：把「工具来源 → 置信度」的判定规则化，替代模型自评（M2）。

设计要点（架构 v2.0 §2.6，E2-2 落地）：
- 模型自评置信度不可控，规则必须在代码里确定性地推导；
- 规则分层：
  1. 确定性来源（天文/曝光纯计算，结果不依赖外部数据）→ high；
  2. 天气预报（外部网口数据）→ 时效敏感：覆盖当日 → high（≤6h 语义近似），跨天 → medium；
  3. 启发式组合（火烧云评分 / 机位匹配，经验模型叠加）→ medium；
  4. 未知工具：带数据来源字段 → medium，否则 → low；
- 规则随数据字段细化时在此表追加（如：「云图外推 → medium」），供 E8 评估回归。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

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


__all__ = ["DETERMINISTIC_TOOLS", "FORECAST_TOOLS", "HEURISTIC_TOOLS", "confidence_for_tool"]
