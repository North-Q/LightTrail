"""U1 天气缺失与绝对时间反例，使用固定预报和时钟。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from lighttrail.tools import weather

NOW = datetime(2026, 10, 6, 8, tzinfo=timezone.utc)


def _data(start: datetime, hours: int = 24, missing: bool = False) -> dict[str, Any]:
    """生成 UTC 小时预报，空值不代表晴天。"""
    return {
        "utc_offset_seconds": 0,
        "hourly": {
            "time": [(start + timedelta(hours=i)).isoformat() for i in range(hours)],
            **{
                key: [None if missing else value] * hours
                for key, value in {
                    "cloud_cover": 50,
                    "cloud_cover_high": 80,
                    "visibility": 25000,
                    "precipitation_probability": 10,
                    "wind_speed_10m": 15,
                }.items()
            },
        },
    }


def _setup(monkeypatch: pytest.MonkeyPatch, data: dict[str, Any], sunset: str = "18:00") -> None:
    """固定时钟、太阳和预报，拦截全部外部请求。"""
    monkeypatch.setattr(weather, "_now", lambda: NOW, raising=False)
    monkeypatch.setattr(weather, "_fetch_json", lambda url: data)
    monkeypatch.setattr(weather, "sun_times", lambda *args: {"日落": sunset})


def test_null_forecast_is_not_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    """所有缺失天气字段须保留 null，不能变成零云零雨零风。"""
    _setup(monkeypatch, _data(NOW.replace(hour=0), missing=True))
    result = weather.weather_forecast(0, 0)
    day = result["每日预报"][0]
    assert day["平均云量（%）"] is None
    assert day["最高降水概率（%）"] is None
    assert day["最大风速（km/h）"] is None


def test_partial_glow_cannot_be_full_score(monkeypatch: pytest.MonkeyPatch) -> None:
    """仅理想云量，缺能见度降水风，不能得到 100 分或高置信。"""
    data = _data(NOW.replace(hour=0))
    for field in ("visibility", "precipitation_probability", "wind_speed_10m"):
        data["hourly"][field] = [None] * 24
    _setup(monkeypatch, data)
    result = weather.sunset_glow_score(0, 0, "2026-10-06")
    assert result["评分（0-100）"] < 100
    assert result["置信度"] == "低"
    assert len(result["缺失字段"]) == 3


def test_six_days_later_is_low(monkeypatch: pytest.MonkeyPatch) -> None:
    """必须比较日落绝对日期，而非只比较钟点。"""
    _setup(monkeypatch, _data(NOW.replace(hour=0) + timedelta(days=6)))
    result = weather.sunset_glow_score(0, 0, "2026-10-12")
    assert result["置信度"] == "低"
    assert result["目标时刻"] == "2026-10-12T18:00:00+08:00"


@pytest.mark.parametrize(
    "offset,day,sunset,start",
    [
        ("-04:00", "2026-10-06", "23:00", "2026-10-07T00:00:00+00:00"),
        ("+08:00", "2026-10-07", "01:00", "2026-10-06T14:00:00+00:00"),
    ],
)
def test_window_crosses_midnight(
    monkeypatch: pytest.MonkeyPatch, offset: str, day: str, sunset: str, start: str
) -> None:
    """日落窗口跨日、跨时区时仍选择正确的六个小时样本。"""
    _setup(monkeypatch, _data(datetime.fromisoformat(start), hours=6), sunset)
    result = weather.sunset_glow_score(0, 0, day, offset)
    assert "error" not in result
    assert result["窗口样本数"] == 6
    assert result["时区"] == offset


def test_provider_offset_is_converted(monkeypatch: pytest.MonkeyPatch) -> None:
    """UTC 时间转换到请求时区后分组，不直接把纽约当地时间贴上北京时间。"""
    _setup(monkeypatch, _data(datetime(2026, 10, 6, 23, tzinfo=timezone.utc), hours=2))
    result = weather.weather_forecast(0, 0, tz_offset="+08:00")
    assert result["每日预报"][0]["日期"] == "2026-10-07"
    assert result["有效开始时间"].startswith("2026-10-07T07:00")


def test_zero_is_available_and_all_missing_is_null(monkeypatch: pytest.MonkeyPatch) -> None:
    """合法零云、零雨、零风不能被判为缺失；全部缺失不提供分数。"""
    data = _data(NOW.replace(hour=0))
    for field in ("cloud_cover", "cloud_cover_high", "precipitation_probability", "wind_speed_10m"):
        data["hourly"][field] = [0] * 24
    _setup(monkeypatch, data)
    result = weather.sunset_glow_score(0, 0, "2026-10-06")
    assert result["缺失字段"] == []
    assert result["评分（0-100）"] == 30
    _setup(monkeypatch, _data(NOW.replace(hour=0), missing=True))
    result = weather.sunset_glow_score(0, 0, "2026-10-06")
    assert result["评分（0-100）"] is None
    assert result["置信度"] == "低"
