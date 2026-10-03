"""C1-1 赶场领域算法与高德适配器的离线测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from lighttrail.adapters.maps import (
    AMapRoutingAdapter,
    BaiduRoutingAdapter,
    FallbackTravelTimeProvider,
)
from lighttrail.domain.chase import (
    ChasePlanner,
    ChaseSite,
    GeoPoint,
    ManualTravelTimeProvider,
    TravelProviderError,
)

_NOW = datetime(2026, 9, 18, 10, 0, tzinfo=timezone(timedelta(hours=8)))


def _site(
    name: str, latitude: float, longitude: float, start_minutes: int, end_minutes: int
) -> ChaseSite:
    """构造带相对窗口的测试机位。"""
    return ChaseSite(
        name=name,
        point=GeoPoint(latitude, longitude),
        window_start=_NOW + timedelta(minutes=start_minutes),
        window_end=_NOW + timedelta(minutes=end_minutes),
    )


def test_manual_provider_is_conservative_and_offline() -> None:
    """无地图 Key 时，直线估算仍返回可解释低置信度路线。"""
    route = ManualTravelTimeProvider().route(
        GeoPoint(31.2304, 121.4737),
        GeoPoint(31.2243, 121.4768),
        "walking",
        _NOW,
    )
    assert route.provider == "manual"
    assert route.confidence == "low"
    assert route.distance_m > 0 and route.duration_s > 0
    assert "直线距离" in route.degraded_reason


def test_chase_planner_prefers_feasible_order() -> None:
    """枚举顺序后应选择能覆盖两个窗口的赶场方案。"""
    first = _site("先拍点", 31.2304, 121.4737, 0, 50)
    second = _site("后拍点", 31.2310, 121.4740, 70, 130)
    plan = ChasePlanner(ManualTravelTimeProvider()).plan(
        [second, first],
        "walking",
        _NOW,
        transition_buffer_s=5 * 60,
        min_shooting_s=15 * 60,
    )
    assert plan.feasible is True
    assert plan.ordered_site_names == ("先拍点", "后拍点")
    assert len(plan.legs) == 1
    assert plan.legs[0].margin_s >= 0


def test_chase_planner_explains_when_all_orders_fail() -> None:
    """所有顺序无法覆盖窗口时不伪造计划。"""
    first = _site("远点 A", 31.2304, 121.4737, 0, 20)
    second = _site("远点 B", 31.7000, 121.9000, 25, 45)
    plan = ChasePlanner(ManualTravelTimeProvider()).plan(
        [first, second],
        "walking",
        _NOW,
        transition_buffer_s=5 * 60,
        min_shooting_s=15 * 60,
    )
    assert plan.feasible is False
    assert plan.risk_level == "unavailable"
    assert plan.rejected_options


def test_amap_driving_maps_response_and_coordinate_order() -> None:
    """高德驾车响应映射统一字段，且请求坐标必须经度在前。"""
    captured: dict = {}

    def fake_get(url: str, *, params: dict) -> dict:
        captured["url"] = url
        captured["params"] = params
        return {
            "status": "1",
            "route": {
                "paths": [
                    {
                        "distance": "12000",
                        "cost": {"duration": "1500"},
                        "steps": [{"tmcs": [{"tmc_status": "畅通"}, {"tmc_status": "缓行"}]}],
                    }
                ]
            },
        }

    route = AMapRoutingAdapter("test-key", json_getter=fake_get).route(
        GeoPoint(31.2304, 121.4737),
        GeoPoint(31.2243, 121.4768),
        "driving",
        _NOW,
    )
    assert captured["url"].endswith("/v5/direction/driving")
    assert captured["params"]["origin"] == "121.473700,31.230400"
    assert captured["params"]["strategy"] == 33
    assert route.distance_m == 12000 and route.duration_s == 1500
    assert route.traffic_level == "缓行"
    assert route.provider == "amap"


def test_amap_transit_requires_city() -> None:
    """公共交通接口缺少城市时，必须在联网前报出明确错误。"""
    adapter = AMapRoutingAdapter("test-key", json_getter=lambda *args, **kwargs: {})
    with pytest.raises(TravelProviderError, match="城市名称"):
        adapter.route(GeoPoint(31.2, 121.4), GeoPoint(31.3, 121.5), "transit", _NOW)


def test_fallback_provider_marks_source_switch() -> None:
    """主服务失败时，备用路线必须保留可解释的切换原因。"""

    class BrokenProvider:
        def route(self, *args, **kwargs):
            raise TravelProviderError("主服务限流")

    route = FallbackTravelTimeProvider(BrokenProvider(), ManualTravelTimeProvider()).route(
        GeoPoint(31.2, 121.4), GeoPoint(31.3, 121.5), "driving", _NOW
    )
    assert route.provider == "manual"
    assert "已切换备用服务" in route.degraded_reason


def test_baidu_driving_maps_response_and_coordinate_order() -> None:
    """百度 adapter 应归一化路线字段，并使用纬度在前的坐标。"""
    captured: dict = {}

    def fake_get(url: str, *, params: dict) -> dict:
        captured["url"] = url
        captured["params"] = params
        return {"status": 0, "result": {"routes": [{"distance": 8000, "duration": 900}]}}

    route = BaiduRoutingAdapter("test-ak", json_getter=fake_get).route(
        GeoPoint(31.2304, 121.4737), GeoPoint(31.2243, 121.4768), "driving", _NOW
    )
    assert captured["url"].endswith("/driving")
    assert captured["params"]["origin"] == "31.230400,121.473700"
    assert route.provider == "baidu"
    assert route.distance_m == 8000 and route.duration_s == 900


def test_fallback_switches_from_broken_primary_to_baidu() -> None:
    """高德类主服务失败时，应能切到百度 adapter 的统一结果。"""

    class BrokenProvider:
        def route(self, *args, **kwargs):
            raise TravelProviderError("高德超时")

    baidu = BaiduRoutingAdapter(
        "test-ak",
        json_getter=lambda *args, **kwargs: {
            "status": 0,
            "result": {"routes": [{"distance": 4000, "duration": 600}]},
        },
    )
    route = FallbackTravelTimeProvider(BrokenProvider(), baidu).route(
        GeoPoint(31.2, 121.4), GeoPoint(31.3, 121.5), "driving", _NOW
    )
    assert route.provider == "baidu"
    assert "已切换备用服务" in route.degraded_reason
