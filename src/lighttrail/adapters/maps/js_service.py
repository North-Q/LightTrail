"""高德地图适配：JS 样式安全代理与 Web 服务坐标转换分开使用凭据。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from math import isfinite

import httpx

_ALLOWED_TARGETS = {
    "v4/map/styles": "https://webapi.amap.com/v4/map/styles",
}
_CONVERT_URL = "https://restapi.amap.com/v3/assistant/coordinate/convert"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class MapProxyError(Exception):
    """地图代理拒绝请求或上游不可用；错误不包含 Key、密钥或坐标。"""


async def forward_js_service(
    path: str,
    query: Sequence[tuple[str, str]],
    *,
    public_key: str,
    security_code: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[int, bytes, str]:
    """向固定高德域名转发一次 GET，不缓存响应也不记录查询参数。

    Args:
        path: ``_AMapService`` 后的相对路径，必须位于固定白名单。
        query: 浏览器原始查询参数；其中 Key 与 jscode 会被服务端重写。
        public_key: 当前应用的 JS API Web Key。
        security_code: 只保存在服务端的 JS API 安全密钥。
        transport: 离线测试可注入的 HTTP 传输。

    Returns:
        上游状态码、响应字节与 Content-Type。

    Raises:
        MapProxyError: 配置缺失、路径不允许、上游失败或响应过大。
    """
    target = _ALLOWED_TARGETS.get(path)
    if target is None:
        raise MapProxyError("该地图服务路径未开放")
    if not public_key or not security_code:
        raise MapProxyError("地图服务未配置")
    params = [(name, value) for name, value in query if name not in {"key", "jscode"}]
    params.extend((("key", public_key), ("jscode", security_code)))
    try:
        async with httpx.AsyncClient(
            transport=transport, timeout=10.0, follow_redirects=False
        ) as client:
            response = await client.get(target, params=params)
    except httpx.HTTPError:
        raise MapProxyError("地图上游服务不可用") from None
    if len(response.content) > _MAX_RESPONSE_BYTES:
        raise MapProxyError("地图上游响应超过大小限制")
    return (
        response.status_code,
        response.content,
        response.headers.get("content-type", "application/json"),
    )


async def convert_wgs84_points(
    points: Sequence[tuple[float, float]],
    *,
    web_key: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[tuple[float, float]]:
    """经后端将 WGS84 点转换为 GCJ-02；浏览器请求体和本地访问日志不含坐标查询串。

    Args:
        points: 经度、纬度元组，最多 40 个，输入精度截为六位小数。
        web_key: 仅保存在服务端的 Web 服务 Key。
        transport: 离线测试传输替身。

    Returns:
        与输入顺序一一对应的 GCJ-02 经度、纬度元组。

    Raises:
        MapProxyError: 上游拒绝、响应无效或结果数量不一致。
    """
    if not 1 <= len(points) <= 40 or any(
        not isfinite(lng) or not isfinite(lat) or not -180 <= lng <= 180 or not -90 <= lat <= 90
        for lng, lat in points
    ):
        raise MapProxyError("坐标转换输入无效")
    if not web_key:
        raise MapProxyError("高德 Web 服务未配置")
    locations = "|".join(f"{lng:.6f},{lat:.6f}" for lng, lat in points)
    try:
        async with httpx.AsyncClient(
            transport=transport, timeout=10.0, follow_redirects=False
        ) as client:
            response = await client.get(
                _CONVERT_URL,
                params={"key": web_key, "locations": locations, "coordsys": "gps"},
            )
    except httpx.HTTPError:
        raise MapProxyError("高德坐标转换服务不可用") from None
    if response.status_code != 200 or len(response.content) > _MAX_RESPONSE_BYTES:
        raise MapProxyError("高德坐标转换服务不可用")
    try:
        body = json.loads(response.content)
        if body["status"] != "1":
            raise ValueError("upstream rejected")
        raw = body["locations"].split(";")
        converted = [tuple(float(value) for value in item.split(",")) for item in raw]
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise MapProxyError("高德坐标转换失败或无权限") from exc
    if len(converted) != len(points) or any(
        len(item) != 2
        or not all(isfinite(value) for value in item)
        or not -180 <= item[0] <= 180
        or not -90 <= item[1] <= 90
        for item in converted
    ):
        raise MapProxyError("高德坐标转换结果无效")
    return [(item[0], item[1]) for item in converted]


__all__ = ["MapProxyError", "convert_wgs84_points", "forward_js_service"]
