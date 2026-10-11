"""S1-4 地图安全代理的固定路径、密钥隔离与离线响应回归。"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from lighttrail.adapters.maps.js_service import (
    MapProxyError,
    convert_wgs84_points,
    forward_js_service,
)
from lighttrail.api.app import ApiDeps, create_app
from lighttrail.api.routes import get_request_context
from lighttrail.api.session import SessionManager
from lighttrail.composition import build_application
from lighttrail.config import Settings
from lighttrail.contracts.context import RequestContext
from lighttrail.contracts.favorite_spot import FavoriteSpotInput
from lighttrail.memory.manager import MemoryManager


@pytest.mark.asyncio
async def test_proxy_only_forwards_allowlisted_path_and_server_secret() -> None:
    """JS 样式代理覆盖调用者凭据，坐标转换路径不能由浏览器直接访问。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """只在内存中检查上游请求。"""
        seen.append(request)
        return httpx.Response(200, json={"status": "1"})

    transport = httpx.MockTransport(handler)
    status, content, content_type = await forward_js_service(
        "v4/map/styles",
        [("key", "attacker"), ("jscode", "attacker")],
        public_key="public-browser-identifier",
        security_code="private-test-code",
        transport=transport,
    )
    assert status == 200 and b"status" in content and "application/json" in content_type
    assert len(seen) == 1
    assert seen[0].url.host == "webapi.amap.com"
    assert seen[0].url.params["key"] == "public-browser-identifier"
    assert seen[0].url.params["jscode"] == "private-test-code"
    with pytest.raises(MapProxyError, match="未开放"):
        await forward_js_service(
            "v3/assistant/coordinate/convert",
            [],
            public_key="public-browser-identifier",
            security_code="private-test-code",
            transport=transport,
        )
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_convert_uses_six_digits_and_rejects_bad_upstream() -> None:
    """坐标转换按顺序返回，参数精度与异常响应均受控。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """离线返回两个 GCJ-02 点。"""
        seen.append(request)
        return httpx.Response(200, json={"status": "1", "locations": "121.1,31.2;121.3,31.4"})

    converted = await convert_wgs84_points(
        [(121.0000009, 31.0000009), (121.2, 31.3)],
        web_key="web-test-key",
        transport=httpx.MockTransport(handler),
    )
    assert converted == [(121.1, 31.2), (121.3, 31.4)]
    assert seen[0].url.host == "restapi.amap.com"
    assert seen[0].url.params["key"] == "web-test-key"
    assert "jscode" not in seen[0].url.params
    assert seen[0].url.params["locations"] == "121.000001,31.000001|121.200000,31.300000"
    assert seen[0].url.params["coordsys"] == "gps"
    with pytest.raises(MapProxyError, match="失败或无权限"):
        await convert_wgs84_points(
            [(121.0, 31.0)],
            web_key="web-test-key",
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"status": "0"})),
        )
    with pytest.raises(MapProxyError, match="未配置"):
        await convert_wgs84_points(
            [(121.0, 31.0)], web_key="", transport=httpx.MockTransport(handler)
        )
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_map_upstream_error_traceback_hides_credentials_and_coordinates() -> None:
    """上游连接异常不得把请求参数带入应用日志堆栈。"""

    def fail(_: httpx.Request) -> httpx.Response:
        """模拟包含敏感请求参数的连接异常。"""
        raise httpx.ConnectError("web-test-key locations=121.4,31.1 private-test-code")

    transport = httpx.MockTransport(fail)
    web_key = "web-test-key"
    points = [(121.4, 31.1)]
    with pytest.raises(MapProxyError) as caught:
        await convert_wgs84_points(points, web_key=web_key, transport=transport)
    trace = "".join(traceback.format_exception(caught.value))
    assert "web-test-key" not in trace
    assert "121.4,31.1" not in trace

    public_key = "public-browser-identifier"
    security_code = "private-test-code"
    with pytest.raises(MapProxyError) as caught:
        await forward_js_service(
            "v4/map/styles",
            [],
            public_key=public_key,
            security_code=security_code,
            transport=transport,
        )
    trace = "".join(traceback.format_exception(caught.value))
    assert "private-test-code" not in trace


@pytest.mark.asyncio
async def test_map_config_never_returns_security_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """代理只转换当前用户的已确认机位，且不向浏览器泄露密钥。"""
    settings = Settings(
        _env_file=None,
        data_dir=str(tmp_path),
        api_key="offline-test-key",
        amap_web_service_key="web-test-key",
        amap_js_api_key="public-browser-identifier",
        amap_security_js_code="private-test-code",
    )
    assert "web-test-key" not in repr(settings)
    memory = MemoryManager(tmp_path)
    owner = RequestContext(user_id="user-a")
    other = RequestContext(user_id="user-b")
    ready_input = FavoriteSpotInput(
        name="合成机位",
        latitude=31.1,
        longitude=121.4,
        coordinate_system="WGS84",
        tz_offset="+08:00",
        source="离线测试",
    )
    ready = memory.create_favorite_spot(owner, ready_input)
    unsupported = memory.create_favorite_spot(
        owner,
        ready_input.model_copy(update={"coordinate_system": "GCJ-02"}),
    )
    foreign_spot = memory.create_favorite_spot(other, ready_input)
    services = build_application(settings, memory=memory)
    app = create_app(ApiDeps(services=services, sessions=SessionManager(tmp_path)))
    app.dependency_overrides[get_request_context] = lambda: owner
    converted_calls: list[list[tuple[float, float]]] = []

    async def fake_forward(*args: Any, **kwargs: Any) -> tuple[int, bytes, str]:
        """避免在路由回归中访问外部服务。"""
        assert kwargs["security_code"] == "private-test-code"
        return 200, b'{"status":"1"}', "application/json"

    async def fake_convert(*args: Any, **kwargs: Any) -> list[tuple[float, float]]:
        """验证后端按当前用户档案解析坐标，并只传 Web 服务 Key。"""
        assert args[0] == [(121.4, 31.1)]
        assert kwargs == {"web_key": "web-test-key"}
        converted_calls.append(args[0])
        return [(121.405, 31.105)]

    monkeypatch.setattr("lighttrail.api.routes.forward_js_service", fake_forward)
    monkeypatch.setattr("lighttrail.api.routes.convert_wgs84_points", fake_convert)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://localhost"
        ) as client:
            config = await client.get("/api/map/config")
            assert config.status_code == 200
            assert config.json() == {"available": True, "js_key": "public-browser-identifier"}
            assert "private-test-code" not in config.text
            assert config.headers["cache-control"] == "private, no-store"
            cross_site = await client.get(
                "/api/map/config", headers={"Sec-Fetch-Site": "cross-site"}
            )
            assert cross_site.status_code == 403
            foreign_origin = await client.post(
                "/api/map/convert",
                json={"spot_ids": [ready.id]},
                headers={"Origin": "https://example.org"},
            )
            assert foreign_origin.status_code == 403
            proxied = await client.get("/_AMapService/v4/map/styles")
            assert proxied.status_code == 200
            assert proxied.headers["cache-control"] == "private, no-store"
            assert "private-test-code" not in proxied.text
            blocked = await client.get(
                "/_AMapService/v3/assistant/coordinate/convert?locations=121.4,31.1"
            )
            assert blocked.status_code == 404
            converted = await client.post(
                "/api/map/convert",
                json={"spot_ids": [ready.id]},
            )
            assert converted.status_code == 200
            assert converted.json() == {"positions": [{"longitude": 121.405, "latitude": 31.105}]}
            assert converted.headers["cache-control"] == "private, no-store"
            assert len(converted_calls) == 1
            for rejected_ids in ([unsupported.id], [foreign_spot.id], [ready.id, ready.id]):
                rejected = await client.post("/api/map/convert", json={"spot_ids": rejected_ids})
                assert rejected.status_code == 422
            assert len(converted_calls) == 1
            app.dependency_overrides[get_request_context] = lambda: other
            denied = await client.post("/api/map/convert", json={"spot_ids": [ready.id]})
            assert denied.status_code == 422
            assert len(converted_calls) == 1
    finally:
        await services.aclose()
