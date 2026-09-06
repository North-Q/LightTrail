"""天气工具（预报摘要 / 火烧云评分）的 pytest 用例。

通过 monkeypatch 替换 weather 模块的 _fetch_json，以固定样例数据离线
验证解析、评分边界与错误路径，不发起真实网络请求。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from lighttrail.agent import registry
from lighttrail.tools import weather
from lighttrail.tools.weather import WeatherError


def _call(name: str, arguments: dict) -> dict:
    return json.loads(registry.dispatch(name, json.dumps(arguments, ensure_ascii=False)))


_LOCAL_TZ = timezone(timedelta(hours=8))


def _local_today() -> date:
    """当前本地日期（按 +08:00 时区，避免 ruff DTZ011）。"""
    return datetime.now(_LOCAL_TZ).date()


def _canned_hourly(
    days: int,
    *,
    cloud: int = 50,
    high: int = 45,
    visibility: int = 25000,
    precip: int = 10,
    wind: int = 15,
) -> dict:
    """构造覆盖近 7 天的 Open-Meteo 小时级样例数据。"""
    today = _local_today()
    rows = []
    for offset in range(days):
        day = today + timedelta(days=offset)
        for hour in range(24):
            rows.append(f"{day.isoformat()}T{hour:02d}:00")
    return {
        "hourly": {
            "time": rows,
            "cloud_cover": [cloud] * len(rows),
            "cloud_cover_high": [high] * len(rows),
            "cloud_cover_medium": [max(0, cloud - high)] * len(rows),
            "cloud_cover_low": [0] * len(rows),
            "visibility": [visibility] * len(rows),
            "precipitation_probability": [precip] * len(rows),
            "wind_speed_10m": [wind] * len(rows),
        }
    }


def test_weather_forecast_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    """预报摘要应包含逐日云量/能见度/降水/风速与傍晚序列。"""
    monkeypatch.setattr(weather, "_fetch_json", lambda url: _canned_hourly(3))
    result = _call("weather_forecast", {"latitude": 31.23, "longitude": 121.47, "days": 3})
    assert "error" not in result
    assert len(result["每日预报"]) == 3
    first = result["每日预报"][0]
    assert first["平均云量（%）"] == 50
    assert first["最低能见度（km）"] == 25.0
    assert first["最高降水概率（%）"] == 10
    assert len(first["傍晚云量序列"]) >= 3


def test_sunset_glow_score_ideal_clouds(monkeypatch: pytest.MonkeyPatch) -> None:
    """理想条件（中云量+高云+高能见度）应给出高评分。"""
    monkeypatch.setattr(weather, "_fetch_json", lambda url: _canned_hourly(2, cloud=50, high=45))
    target = (_local_today() + timedelta(days=1)).isoformat()
    result = _call("sunset_glow_score", {"latitude": 31.23, "longitude": 121.47, "date": target})
    assert "error" not in result
    assert result["评分（0-100）"] >= 70
    assert result["等级"].startswith("高")
    assert len(result["依据"]) == 5
    assert result["置信度"] in ("高", "中", "低")


def test_sunset_glow_score_overcast(monkeypatch: pytest.MonkeyPatch) -> None:
    """完全阴天+低能见度+高降水应给出低评分。"""
    monkeypatch.setattr(
        weather,
        "_fetch_json",
        lambda url: _canned_hourly(3, cloud=100, high=100, visibility=3000, precip=90, wind=60),
    )
    target = (_local_today() + timedelta(days=1)).isoformat()
    result = _call("sunset_glow_score", {"latitude": 31.23, "longitude": 121.47, "date": target})
    assert result["评分（0-100）"] < 40


def test_sunset_glow_score_rejects_past_date(monkeypatch: pytest.MonkeyPatch) -> None:
    """历史日期与超出 7 天的日期应报错。"""
    monkeypatch.setattr(weather, "_fetch_json", lambda url: _canned_hourly(7))
    past = (_local_today() - timedelta(days=1)).isoformat()
    assert "error" in _call(
        "sunset_glow_score", {"latitude": 31.23, "longitude": 121.47, "date": past}
    )
    far = (_local_today() + timedelta(days=10)).isoformat()
    assert "error" in _call(
        "sunset_glow_score", {"latitude": 31.23, "longitude": 121.47, "date": far}
    )


def test_weather_fetch_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """网络失败应回传 error 而非抛异常。"""

    def _boom(url: str) -> dict:
        raise WeatherError("网络不可达")

    monkeypatch.setattr(weather, "_fetch_json", _boom)
    result = _call("weather_forecast", {"latitude": 31.23, "longitude": 121.47, "days": 2})
    assert "error" in result
