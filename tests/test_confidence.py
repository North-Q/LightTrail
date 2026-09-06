"""置信度规则表（infra/confidence.py）的 pytest 用例（E2-2）。

验证规则化置信度推导（替代模型自评）：
- 确定性工具（天文/计算）→ high；
- 天气预报按时效：覆盖当日 → high、跨天 → medium、无条目 → medium；
- 启发式组合（火烧云评分 / 机位匹配）→ medium；
- 未知工具：带数据来源 → medium，否则 → low。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lighttrail.infra.confidence import confidence_for_tool

_HIGH_TOOLS = [
    "get_current_time",
    "equivalent_exposure",
    "star_shutter_rule",
    "nd_long_exposure",
    "sun_times",
    "sun_position",
    "moon_phase",
    "moon_events",
    "galaxy_visibility",
]


def test_deterministic_tools_are_high() -> None:
    """天文/曝光纯计算 → high。"""
    for name in _HIGH_TOOLS:
        assert confidence_for_tool(name, {}) == "high"
        assert confidence_for_tool(name, {"error": "x"}) == "high"  # 结果无关


def test_weather_forecast_today_is_high() -> None:
    """预报覆盖当日 → high（时效 ≤6h 的日期级近似）。"""
    today = datetime.now(timezone.utc).date().isoformat()
    result = {"每日预报": [{"日期": today, "平均云量（%）": 40}]}
    assert confidence_for_tool("weather_forecast", result) == "high"


def test_weather_forecast_cross_day_is_medium() -> None:
    """预报跨天（晚于当日）→ medium。"""
    tomorrow = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
    result = {"每日预报": [{"日期": tomorrow, "平均云量（%）": 40}]}
    assert confidence_for_tool("weather_forecast", result) == "medium"


def test_weather_forecast_no_entries_is_medium() -> None:
    """预报无有效条目 → 降级 medium（有外部数据语义）。"""
    assert confidence_for_tool("weather_forecast", {}) == "medium"
    assert confidence_for_tool("weather_forecast", {"error": "接口失败"}) == "medium"


def test_heuristic_tools_are_medium() -> None:
    """启发式组合（火烧云评分 / 机位匹配）→ medium。"""
    assert confidence_for_tool("sunset_glow_score", {"评分": 62}) == "medium"
    assert confidence_for_tool("match_sites", {"结果": []}) == "medium"


def test_unknown_tool_source_based() -> None:
    """未知工具：带数据来源 → medium，否则 → low。"""
    assert confidence_for_tool("custom_tool", {"数据来源": "外部"}) == "medium"
    assert confidence_for_tool("custom_tool", {"值": 1}) == "low"
    assert confidence_for_tool("custom_tool", {}) == "low"
