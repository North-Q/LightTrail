"""高德 Web 服务路线适配器（C1-1b）。

仅负责高德请求/响应、坐标顺序和错误语义的转换；不参与机位排序或拍摄窗口判断。
后续百度、腾讯等供应商实现同一 ``TravelTimeProvider`` 即可接入。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from lighttrail.adapters.datasources import DataSourceError, get_json_sync
from lighttrail.domain.chase import (
    GeoPoint,
    TrafficLevel,
    TransportMode,
    TravelProviderError,
    TravelRoute,
)

_BASE_URL = "https://restapi.amap.com"
_ENDPOINTS: dict[TransportMode, str] = {
    "driving": "/v5/direction/driving",
    "cycling": "/v4/direction/bicycling",
    "walking": "/v5/direction/walking",
    "transit": "/v3/direction/transit/integrated",
}
_JsonGetter = Callable[..., dict[str, Any]]


class AMapRoutingAdapter:
    """高德路线服务实现。

    Args:
        api_key: 高德 Web 服务 Key；为空时立即失败，避免无意义网络请求。
        json_getter: JSON GET 函数注入点，仅供离线测试替换。
    """

    def __init__(self, api_key: str, *, json_getter: _JsonGetter = get_json_sync) -> None:
        """保存 Key 和可测试的 HTTP 调用口。"""
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
        """查询高德路线并映射为项目统一结果。

        Args:
            origin: 起点坐标。
            destination: 终点坐标。
            transport_mode: 驾车、骑行、步行或公共交通。
            depart_at: 计划出发时间，仅记录为请求生成时间；高德基础路线接口不把它伪装成未来预测。
            transit_city: 公共交通所需城市名称；其他交通方式忽略。

        Returns:
            统一的路线结果。

        Raises:
            TravelProviderError: Key 缺失、服务失败、公共交通缺少城市或响应不合法。
        """
        if not self._api_key:
            raise TravelProviderError("未配置高德 Web 服务 Key")
        if transport_mode == "transit" and not transit_city.strip():
            raise TravelProviderError("公共交通路线需要提供城市名称")

        params: dict[str, Any] = {
            "key": self._api_key,
            "origin": _format_point(origin),
            "destination": _format_point(destination),
            "output": "json",
        }
        if transport_mode == "driving":
            params.update({"strategy": 33, "show_fields": "cost,tmcs"})
        elif transport_mode == "walking":
            params["show_fields"] = "cost"
        elif transport_mode == "transit":
            params["city"] = transit_city.strip()

        try:
            payload = self._json_getter(_BASE_URL + _ENDPOINTS[transport_mode], params=params)
        except DataSourceError as exc:
            raise TravelProviderError(f"高德路线服务不可用：{exc}") from exc
        except Exception as exc:
            raise TravelProviderError(f"高德路线请求失败：{type(exc).__name__}") from exc

        if transport_mode == "cycling":
            if int(payload.get("errcode", -1)) != 0:
                raise TravelProviderError(f"高德路线服务返回失败：{payload.get('errmsg', '未知错误')}")
        elif str(payload.get("status", "")) != "1":
            info = str(payload.get("info", "未知错误"))
            raise TravelProviderError(f"高德路线服务返回失败：{info}")
        path = _first_path(payload, transport_mode)
        distance_m = _as_positive_int(path.get("distance"), "路线距离")
        duration_s = _duration_of(path)
        traffic_level, traffic_summary = _traffic_of(path, transport_mode)
        return TravelRoute(
            distance_m=distance_m,
            duration_s=duration_s,
            transport_mode=transport_mode,
            traffic_level=traffic_level,
            traffic_summary=traffic_summary,
            queried_at=datetime.now(timezone.utc),
            is_future_prediction=False,
            provider="amap",
            confidence="high" if transport_mode == "driving" else "medium",
        )


def _format_point(point: GeoPoint) -> str:
    """高德 Web 服务统一使用经度在前、纬度在后。"""
    return f"{point.longitude:.6f},{point.latitude:.6f}"


def _first_path(payload: dict[str, Any], transport_mode: TransportMode) -> dict[str, Any]:
    """兼容高德各交通方式的首条路线响应结构。"""
    if transport_mode == "cycling":
        data = payload.get("data")
        entries = data.get("paths") if isinstance(data, dict) else None
        if not isinstance(entries, list) or not entries or not isinstance(entries[0], dict):
            raise TravelProviderError("高德未返回可用骑行路线")
        return entries[0]
    route = payload.get("route")
    if not isinstance(route, dict):
        raise TravelProviderError("高德路线响应缺少 route")
    key = "transits" if transport_mode == "transit" else "paths"
    entries = route.get(key)
    if not isinstance(entries, list) or not entries or not isinstance(entries[0], dict):
        raise TravelProviderError("高德未返回可用路线")
    return entries[0]


def _duration_of(path: dict[str, Any]) -> int:
    """读取 v5 cost.duration 或旧接口顶层 duration。"""
    cost = path.get("cost")
    raw = cost.get("duration") if isinstance(cost, dict) else path.get("duration")
    return _as_positive_int(raw, "路线耗时")


def _as_positive_int(value: Any, field: str) -> int:
    """解析高德字符串数值，并拒绝零值和非法值。"""
    try:
        number = int(float(str(value)))
    except (TypeError, ValueError) as exc:
        raise TravelProviderError(f"高德响应中的{field}非法") from exc
    if number <= 0:
        raise TravelProviderError(f"高德响应中的{field}必须大于零")
    return number


def _traffic_of(path: dict[str, Any], transport_mode: TransportMode) -> tuple[TrafficLevel, str]:
    """提取驾车交通状态；其他方式明确标记未知而不伪造路况。"""
    if transport_mode != "driving":
        return "未知", "该交通方式未返回道路拥堵信息"
    statuses: list[str] = []
    for step in path.get("steps", []):
        if not isinstance(step, dict):
            continue
        for item in step.get("tmcs", []):
            if isinstance(item, dict) and isinstance(item.get("tmc_status"), str):
                statuses.append(item["tmc_status"])
    if not statuses:
        return "未知", "高德未返回分路段交通状态"
    level: TrafficLevel
    if any(status in {"拥堵", "严重拥堵"} for status in statuses):
        level = "拥堵"
    elif any(status == "缓行" for status in statuses):
        level = "缓行"
    else:
        level = "畅通"
    return level, "；".join(dict.fromkeys(statuses))


__all__ = ["AMapRoutingAdapter"]
