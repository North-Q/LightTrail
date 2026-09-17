"""百度 Web 服务路线适配器（C1-1c）。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from lighttrail.adapters.datasources import DataSourceError, get_json_sync
from lighttrail.domain.chase import GeoPoint, TransportMode, TravelProviderError, TravelRoute

_BASE_URL = "https://api.map.baidu.com/directionlite/v1"
_ENDPOINTS: dict[TransportMode, str] = {
    "driving": "/driving",
    "cycling": "/riding",
    "walking": "/walking",
    "transit": "/transit",
}
_JsonGetter = Callable[..., dict[str, Any]]


class BaiduRoutingAdapter:
    """百度轻量路线规划的统一适配。"""

    def __init__(self, api_key: str, *, json_getter: _JsonGetter = get_json_sync) -> None:
        """保存服务端 AK 和可替换 HTTP 调用口。"""
        self._api_key = api_key.strip()
        self._json_getter = json_getter

    def route(
        self,
        origin: GeoPoint,
        destination: GeoPoint,
        transport_mode: TransportMode,
        depart_at: datetime,
        *,
        transit_city: str = "",
    ) -> TravelRoute:
        """查询路线并转为项目统一字段。"""
        if not self._api_key:
            raise TravelProviderError("未配置百度地图 AK")
        if transport_mode == "transit" and not transit_city.strip():
            raise TravelProviderError("公共交通路线需要提供城市名称")
        params: dict[str, Any] = {
            "ak": self._api_key,
            "origin": _format_point(origin),
            "destination": _format_point(destination),
            "coord_type": "wgs84",
            "output": "json",
        }
        if transport_mode == "driving":
            params["tactics"] = 11
        if transport_mode == "transit":
            params["city"] = transit_city.strip()
        try:
            payload = self._json_getter(_BASE_URL + _ENDPOINTS[transport_mode], params=params)
        except DataSourceError as exc:
            raise TravelProviderError(f"百度路线服务不可用：{exc}") from exc
        except Exception as exc:
            raise TravelProviderError(f"百度路线请求失败：{type(exc).__name__}") from exc
        if int(payload.get("status", -1)) != 0:
            raise TravelProviderError(f"百度路线服务返回失败：{payload.get('message', '未知错误')}")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise TravelProviderError("百度路线响应缺少 result")
        routes = result.get("routes")
        if not isinstance(routes, list) or not routes or not isinstance(routes[0], dict):
            raise TravelProviderError("百度未返回可用路线")
        path = routes[0]
        distance_m = _number(path.get("distance"), "路线距离")
        duration_s = _number(path.get("duration"), "路线耗时")
        return TravelRoute(
            distance_m=distance_m,
            duration_s=duration_s,
            transport_mode=transport_mode,
            traffic_level="未知",
            traffic_summary="百度轻量路线未返回可用分段拥堵信息",
            queried_at=datetime.now(timezone.utc),
            is_future_prediction=False,
            provider="baidu",
            confidence="medium",
        )


def _format_point(point: GeoPoint) -> str:
    """百度路线接口使用纬度在前、经度在后。"""
    return f"{point.latitude:.6f},{point.longitude:.6f}"


def _number(value: Any, field: str) -> int:
    """校验并读取百度数值字段。"""
    try:
        result = int(float(str(value)))
    except (TypeError, ValueError) as exc:
        raise TravelProviderError(f"百度响应中的{field}非法") from exc
    if result <= 0:
        raise TravelProviderError(f"百度响应中的{field}必须大于零")
    return result


__all__ = ["BaiduRoutingAdapter"]
