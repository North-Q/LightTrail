"""置信度规则表（infra/confidence.py）的 pytest 用例（E2-2）。

验证规则化置信度推导（替代模型自评）：
- 确定性工具（天文/计算）→ high；
- 天气预报按时效：覆盖当日 → high、跨天 → medium、无条目 → medium；
- 启发式组合（火烧云评分 / 机位匹配）→ medium；
- 未知工具：带数据来源 → medium，否则 → low。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lighttrail.contracts.models import Source
from lighttrail.infra.confidence import confidence_detail, confidence_for_tool

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


# ------ 卡片置信度明细（B4-3：前端不再自算区间/常量表）------
def test_confidence_detail_weighted_by_source_levels() -> None:
    """主值 = 依据级别加权平均（high 0.85 / medium 0.6 / low 0.35），区间随条数收窄。"""
    evidence = [
        Source(tool="sun_times", field="太阳时刻", confidence="high"),
        Source(tool="weather_forecast", field="每日预报", confidence="medium"),
        Source(tool="sunset_glow_score", field="评分", confidence="low"),
    ]
    detail = confidence_detail("high", evidence)
    assert detail.score == 60  # (0.85 + 0.6 + 0.35) / 3 → 60
    assert (detail.low, detail.high) == (40, 80)  # 半宽 26 - 2×3 = 20
    assert (detail.high_count, detail.medium_count, detail.low_count) == (1, 1, 1)
    assert detail.basis.startswith("按 3 条依据")


def test_confidence_detail_narrows_with_more_evidence() -> None:
    """依据越充分区间越窄（同级别、条数不同 → 半宽不同）。"""
    one = confidence_detail("high", [Source(tool="a", confidence="high")])
    three = confidence_detail("high", [Source(tool="a", confidence="high")] * 3)
    assert one.score == three.score == 85
    assert (one.high - one.low) > (three.high - three.low)


def test_confidence_detail_falls_back_without_evidence() -> None:
    """无依据时退回等级兜底带并如实说明（不假装精确）。"""
    detail = confidence_detail("medium", [])
    assert (detail.score, detail.low, detail.high) == (55, 49, 61)
    assert "无可溯源依据" in detail.basis


def test_confidence_detail_treats_unknown_level_as_low() -> None:
    """非法等级按 low 处理；未知来源级别也计入 low。"""
    assert confidence_detail("bogus", []).level == "low"
    detail = confidence_detail("low", [Source(tool="x", confidence="weird")])
    assert (detail.low_count, detail.score) == (1, 35)
