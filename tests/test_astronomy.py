"""天文工具（日出日落/蓝调黄金/月相/银心）的 pytest 用例。

采用固定观测点（上海市区 31.23, 121.47）与固定日期，验证时间顺序、
已知量级与窗口结构；银心方位基于自研近似算法，用物理自洽性约束
（中天时刻约等于夜初、高度约 90-|纬度-赤纬|、方位近正南）。
"""

from __future__ import annotations

import json

from lighttrail.agent import registry
from lighttrail.tools import astronomy  # noqa: F401

SHANGHAI = {"latitude": 31.23, "longitude": 121.47, "date": "2026-08-16"}


def _call(name: str, arguments: dict) -> dict:
    return json.loads(registry.dispatch(name, json.dumps(arguments, ensure_ascii=False)))


def _minutes(value: str) -> int:
    hours, minutes = (int(part) for part in value.split(":"))
    return hours * 60 + minutes


def test_sun_times_order_and_values() -> None:
    """日出日落与晨昏蒙影应按时间正序，且与上海 8 月量级一致。"""
    result = _call("sun_times", SHANGHAI)
    assert result["日出"] == "05:20"
    assert result["日落"] == "18:36"
    sequence = [
        result["天文晨光始"],
        result["航海晨光始"],
        result["民用晨光始"],
        result["日出"],
        result["日落"],
        result["民用昏影终"],
        result["航海昏影终"],
        result["天文昏影终"],
    ]
    times = [_minutes(item) for item in sequence]
    assert times == sorted(times)
    assert _minutes(result["日出"]) < _minutes(result["日落"])
    # 蓝调/黄金窗口结构：开始早于结束，且不跨日出时间合理性
    for key in ("黄金时刻（晨）", "黄金时刻（暮）", "蓝调时刻（晨）", "蓝调时刻（暮）"):
        window = result[key]
        assert window is not None and len(window) == 1
        assert _minutes(window[0]["开始"]) < _minutes(window[0]["结束"])


def test_sun_position_evening() -> None:
    """傍晚 18:30 太阳应接近地平线并位于西偏北方向。"""
    result = _call(
        "sun_position", {"latitude": 31.23, "longitude": 121.47, "date_time": "2026-08-16 18:30"}
    )
    assert 0.0 < result["太阳高度角（度）"] < 2.0
    assert 284.0 < result["太阳方位角（度）"] < 288.0
    assert result["方位"] == "西西北"


def test_moon_phase_august_2026() -> None:
    """2026-08-16 应为朔后数日（娥眉月），照亮比例在 10%-25%。"""
    result = _call("moon_phase", {"date": "2026-08-16"})
    assert result["月相名称"] == "娥眉月（朔后）"
    assert 3.0 <= result["月龄（天）"] <= 4.5
    assert 10 <= result["照亮比例（%）"] <= 25


def test_moon_events_shanghai() -> None:
    """月升月落应有值且时间在一天之内。"""
    result = _call("moon_events", SHANGHAI)
    assert result["月升"] == "08:46"
    assert result["月落"] == "20:33"
    assert "月升方位（度）" in result and "月落方位（度）" in result


def test_galaxy_visibility_shanghai() -> None:
    """上海 8 月中旬银心：夜初在正南方约 30 度，午夜前落下。"""
    result = _call("galaxy_visibility", SHANGHAI)
    assert "error" not in result
    windows = result["可见窗口"]
    assert windows, "银心应有可见窗口"
    first = windows[0]
    # 夜初即中天附近：开始时刻贴近天文昏影终（20:03），高度约 29.8°
    assert "20:00" <= first["开始"] <= "21:00"
    assert 28.0 <= first["最高高度角（度）"] <= 31.5
    assert 175.0 <= first["最高时方位（度）"] <= 185.0
    # 银心约在午夜前后落入地平线下：窗口不应延伸到凌晨 3 点后
    assert _minutes(first["结束"]) <= 60
    assert result["整夜最高高度角（度）"] >= 28.0


def test_galaxy_visibility_invalid() -> None:
    """非法参数应返回 error 字段。"""
    assert "error" in _call(
        "galaxy_visibility", {"latitude": 31.23, "longitude": 121.47, "date": "2026-13-99"}
    )
    assert "error" in _call(
        "galaxy_visibility", {"latitude": 95, "longitude": 0, "date": "2026-08-16"}
    )
