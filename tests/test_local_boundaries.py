"""U2-3 本地来源、凭据和照片能力边界的离线反例。"""

from __future__ import annotations

import io
import json
import logging
import traceback
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from openai import BadRequestError
from PIL import Image

from evals.fake_data import CassetteChatClient
from lighttrail.adapters.llm.client import ChatClient
from lighttrail.adapters.maps import AMapRoutingAdapter, BaiduRoutingAdapter
from lighttrail.adapters.maps.js_service import forward_js_service
from lighttrail.api.app import ApiDeps, create_app
from lighttrail.api.session import SessionManager
from lighttrail.composition import ApplicationServices, build_application
from lighttrail.config import Settings
from lighttrail.contracts.context import RequestContext
from lighttrail.domain.chase import GeoPoint, TravelProviderError
from lighttrail.infra.http import DataSourceError, get_json
from lighttrail.infra.redact import redact
from lighttrail.infra.trace import TraceRecorder

_KEY = "ECNU_SYNTHETIC_PRIVATE_7_key"
_AK = "BAIDU_SYNTHETIC_PRIVATE_8_ak"
_AMAP_KEY = "AMAP_SYNTHETIC_PRIVATE_9_key"
_AMAP_CODE = "AMAP_SYNTHETIC_PRIVATE_10_code"


@pytest.fixture
def local_app(tmp_path: Path) -> Iterator[tuple[Any, ApplicationServices]]:
    """创建合成档案目录与禁止真实发送的生产应用。"""
    services = build_application(
        Settings(
            _env_file=None,
            data_dir=str(tmp_path),
            api_key=_KEY,
            baidu_map_ak=_AK,
            amap_web_service_key=_AMAP_KEY,
            amap_security_js_code=_AMAP_CODE,
            amap_js_api_key="public-sdk-identifier",
        ),
        client=CassetteChatClient({}),
    )
    try:
        yield create_app(ApiDeps(services, SessionManager(tmp_path))), services
    finally:
        services.close()


@pytest.mark.parametrize(
    "headers,base,peer",
    [
        ({"Origin": "https://untrusted.invalid"}, "http://localhost", "127.0.0.1"),
        ({"Origin": "null"}, "http://localhost", "127.0.0.1"),
        ({"Origin": "http://localhost:9999"}, "http://localhost", "127.0.0.1"),
        ({"Origin": "http://localhost.evil.invalid:5173"}, "http://localhost", "127.0.0.1"),
        ({}, "http://evil.invalid", "127.0.0.1"),
        ({}, "http://localhost", "192.0.2.55"),
        ({"Sec-Fetch-Site": "cross-site"}, "http://localhost", "127.0.0.1"),
    ],
)
async def test_untrusted_cannot_write(
    local_app: tuple[Any, ApplicationServices], headers: dict[str, str], base: str, peer: str
) -> None:
    """无 Origin、伪本地域名及跨站来源均不能绕过来源和 Host 边界。"""
    app, services = local_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(peer, 4567)), base_url=base
    ) as client:
        response = await client.put(
            "/api/profile", json={"camera_body": "不得写入"}, headers=headers
        )
        assert response.status_code == 403
        assert services.memory.profile.camera_body != "不得写入"
        assert (
            await client.post(
                "/api/events", json={"location": "合成地点", "outcome": "success"}, headers=headers
            )
        ).status_code == 403


@pytest.mark.parametrize(
    "origin", [None, "http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8000"]
)
async def test_local_flows_stay_available(
    local_app: tuple[Any, ApplicationServices], origin: str | None
) -> None:
    """真实本地前端、同源请求和不带 Origin 的本机程序可以正常写入。"""
    app, services = local_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000"
    ) as client:
        headers = {"Origin": origin} if origin else {}
        response = await client.put(
            "/api/profile", json={"camera_body": "合成相机"}, headers=headers
        )
        assert response.status_code == 200
        assert services.memory.profile.camera_body == "合成相机"


def test_keys_absent_from_repr_logs_and_trace(caplog: pytest.LogCaptureFixture) -> None:
    """无 sk 前缀的假密钥也必须被登记并从所有诊断出口移除。"""
    settings = Settings(
        _env_file=None,
        api_key=_KEY,
        baidu_map_ak=_AK,
        amap_web_service_key=_AMAP_KEY,
        amap_security_js_code=_AMAP_CODE,
    )
    secrets = (_KEY, _AK, _AMAP_KEY, _AMAP_CODE)
    assert all(secret not in repr(settings) for secret in secrets)
    assert all(secret not in str(settings) for secret in secrets)
    diagnostic = "HTTP 401 request_id=synthetic " + " ".join(secrets)
    assert all(secret not in redact(diagnostic) for secret in secrets)
    with caplog.at_level(logging.WARNING):
        try:
            raise ValueError(diagnostic)
        except ValueError:
            logging.getLogger("lighttrail.boundary.probe").exception("合成诊断")
    assert all(secret not in caplog.text for secret in secrets)
    assert "401" in caplog.text and "synthetic" in caplog.text
    recorder = TraceRecorder()
    recorder.record_step(diagnostic, output_summary=diagnostic)
    recorder.record_tool("synthetic", "{}", json.dumps({secret: secret for secret in secrets}))
    assert all(secret not in repr(recorder.to_report()) for secret in secrets)


async def test_sdk_error_preserves_code_without_key() -> None:
    """SDK 400 异常只请求一次，错误链保留状态与定位信息但不暴露 Key。"""
    client = ChatClient(_KEY, "http://localhost:1/v1")
    calls = 0

    async def create(**kwargs: Any) -> None:
        """离线 SDK 错误替身。"""
        nonlocal calls
        calls += 1
        response = httpx.Response(
            400,
            request=httpx.Request("POST", "http://localhost/v1"),
            headers={"x-request-id": "synthetic-400"},
        )
        raise BadRequestError(f"rejected {_KEY}", response=response, body={"key": _KEY})

    client._async_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    try:
        with pytest.raises(Exception) as caught:
            await client.acall([])
        formatted = "".join(traceback.format_exception(caught.type, caught.value, caught.tb))
        assert _KEY not in formatted
        assert "400" in str(caught.value)
        assert "synthetic-400" in str(caught.value)
        assert calls == 1
    finally:
        await client.aclose()


async def test_api_error_response_hides_keys(
    local_app: tuple[Any, ApplicationServices],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """API、SSE 错误响应和日志不得回传错误替身里的凭据。"""
    app, _services = local_app

    def fail(*args: Any, **kwargs: Any) -> Any:
        """模拟作用域构造失败。"""
        raise RuntimeError(f"synthetic request_id=probe {_KEY} {_AK}")

    monkeypatch.setattr(ApplicationServices, "create_scope", fail)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        response = await client.post("/api/chat", json={"message": "合成请求"})
    assert _KEY not in response.text and _AK not in response.text
    assert _KEY not in caplog.text and _AK not in caplog.text
    assert "error" in response.text and "done" in response.text


@pytest.mark.parametrize("tool", ["analyze_photo", "reverse_engineer_photo"])
def test_model_cannot_read_local_photo(
    local_app: tuple[Any, ApplicationServices],
    tmp_path: Path,
    tool: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已存在且可解码的越界文件，必须在读图/模型发送前被拒绝。"""
    _, services = local_app
    image = tmp_path / "outside-authorized.png"
    Image.new("RGB", (16, 16)).save(image)

    def no_read(path: str) -> Any:
        """拒绝访问必须发生于读取之前。"""
        pytest.fail("未授权文件已经进入读图阶段")

    monkeypatch.setattr("lighttrail.tools.photo_analysis._read_exif", no_read)
    scope = services.create_scope(RequestContext())
    result = json.loads(
        services.registry.dispatch(tool, json.dumps({"image_path": str(image)}), scope.tool_context)
    )
    assert "error" in result and "未授权" in result["error"]
    assert services.client.calls == []


async def test_upload_filename_cannot_choose_local_path(
    local_app: tuple[Any, ApplicationServices], monkeypatch: pytest.MonkeyPatch
) -> None:
    """上传内容由服务端自选临时文件承载，文件名中的路径不扩大能力。"""
    app, services = local_app
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16)).save(buffer, format="PNG")
    paths: list[Path] = []

    def analyze(path: str, focus: str) -> dict[str, Any]:
        """仅观察真实上传文件的生命周期。"""
        paths.append(Path(path))
        assert Path(path).is_file()
        return {"总体评语": "合成", "已识别EXIF": {}}

    object.__setattr__(services, "photo_analyze", analyze)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        response = await client.post(
            "/api/photos/review",
            files={"file": ("../../pretend.png", buffer.getvalue(), "image/png")},
        )
    assert response.status_code == 200
    assert paths and all(not path.exists() for path in paths)
    assert all(path.name != "pretend.png" for path in paths)


async def test_public_identifier_is_explicit_exception(
    local_app: tuple[Any, ApplicationServices],
) -> None:
    """经小北确认，公开 SDK 标识可下发；三个服务端地图凭据不可下发。"""
    app, _ = local_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        response = await client.get("/api/map/config")
        metadata = await client.get("/api/data-boundaries")
        validation = await client.post("/api/chat", json={"message": {"value": _KEY}})
    assert response.status_code == 200
    assert "public-sdk-identifier" in response.text
    assert all(
        secret not in response.text + metadata.text + validation.text
        for secret in (_KEY, _AK, _AMAP_KEY, _AMAP_CODE)
    )
    assert validation.status_code == 422
    assert "chat.ecnu.edu.cn" in metadata.text


async def test_upstream_echo_and_http_logs_hide_private_code(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """固定代理路径的上游回显也不能把 jscode 带回 API。"""

    def echo(request: httpx.Request) -> httpx.Response:
        """离线回显假私有凭据，保留定位错误码。"""
        return httpx.Response(403, json={"code": "synthetic-403", "value": _AMAP_CODE})

    with caplog.at_level(logging.INFO):
        status, body, _ = await forward_js_service(
            "v4/map/styles",
            [],
            public_key="public-sdk-identifier",
            security_code=_AMAP_CODE,
            transport=httpx.MockTransport(echo),
        )
    assert status == 403 and "synthetic-403" in body.decode()
    assert _AMAP_CODE not in body.decode() + caplog.text


async def test_http_error_and_chain_hide_key() -> None:
    """通用取数异常保留 HTTP 状态，不回显已登记的 Key。"""
    Settings(_env_file=None, api_key=_KEY)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(401, text=f"synthetic-401 {_KEY}")
        )
    ) as client:
        with pytest.raises(DataSourceError) as caught:
            await get_json("http://synthetic.invalid", client=client)
    formatted = "".join(traceback.format_exception(caught.type, caught.value, caught.tb))
    assert _KEY not in formatted and "401" in str(caught.value)


@pytest.mark.parametrize(
    "adapter,key", [(AMapRoutingAdapter, _AMAP_KEY), (BaiduRoutingAdapter, _AK)]
)
def test_map_error_and_chain_hide_key(adapter: Any, key: str) -> None:
    """独立构造的地图适配器也必须登记凭据及过滤非 HTTP 异常链。"""

    def fail(*args: Any, **kwargs: Any) -> Any:
        """模拟取数错误，禁止网络访问。"""
        raise DataSourceError(f"HTTP 403 synthetic-map {key}")

    instance = adapter(key, json_getter=fail)
    with pytest.raises(TravelProviderError) as caught:
        instance.route(GeoPoint(0, 0), GeoPoint(1, 1), "driving", datetime.now(timezone.utc))
    assert key not in "".join(traceback.format_exception(caught.type, caught.value, caught.tb))
    assert "403" in str(caught.value)


@pytest.mark.parametrize("candidate", ["sibling.png", "../outside.png", ".", "missing.png"])
def test_exact_file_grant_does_not_authorize_sibling_or_parent(
    local_app: tuple[Any, ApplicationServices],
    tmp_path: Path,
    candidate: str,
) -> None:
    """授权单文件不能通过相邻、父目录或缺失路径扩张。"""
    _, services = local_app
    directory = tmp_path / "photos"
    directory.mkdir()
    allowed = directory / "allowed.png"
    Image.new("RGB", (16, 16)).save(allowed)
    Image.new("RGB", (16, 16)).save(directory / "sibling.png")
    Image.new("RGB", (16, 16)).save(tmp_path / "outside.png")
    scope = services.create_scope(RequestContext(), authorized_photo_paths=(str(allowed),))
    result = json.loads(
        services.registry.dispatch(
            "analyze_photo",
            json.dumps({"image_path": str(directory / candidate)}),
            scope.tool_context,
        )
    )
    assert "未授权" in result["error"]
    assert services.client.calls == []


def test_production_photo_grant_reads_only_explicit_file(
    local_app: tuple[Any, ApplicationServices],
    tmp_path: Path,
) -> None:
    """生产装配的注册工具可读取入口授权的合成图，而非只测试独立路径规则。"""
    from tests.test_photo_analysis import _OK_JSON

    _, services = local_app
    allowed = tmp_path / "allowed.jpg"
    exif = Image.Exif()
    exif[271] = "SyntheticCamera"
    exif[272] = "SyntheticModel"
    exif[36867] = "2026:10:07 00:00:00"
    Image.new("RGB", (1500, 800)).save(allowed, exif=exif)
    services.client._cassettes["synthetic"] = {"card": {"content": _OK_JSON}}
    services.client.set_group("synthetic")
    scope = services.create_scope(RequestContext(), authorized_photo_paths=(str(allowed),))
    result = json.loads(
        services.registry.dispatch(
            "analyze_photo", json.dumps({"image_path": str(allowed)}), scope.tool_context
        )
    )
    assert "error" not in result
    assert result["已识别EXIF"]["相机型号"] == "SyntheticModel"
    assert len(services.client.calls) == 1
    content = services.client.calls[0]["messages"][-1]["content"]
    assert "data:image/jpeg;base64," in content[1]["image_url"]["url"]
    assert "GPS" not in content[0]["text"]


def test_changed_canonical_target_is_not_authorized(
    local_app: tuple[Any, ApplicationServices],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """离线模拟符号链接目标变化，不依赖 Windows 创建 symlink 的额外权限。"""
    _, services = local_app
    allowed, outside = tmp_path / "allowed.png", tmp_path / "outside.png"
    Image.new("RGB", (16, 16)).save(allowed)
    Image.new("RGB", (16, 16)).save(outside)
    scope = services.create_scope(RequestContext(), authorized_photo_paths=(str(allowed),))
    resolve = Path.resolve

    def changed(path: Path, *args: Any, **kwargs: Any) -> Path:
        """模拟系统将原路径解析到另一文件。"""
        return resolve(outside) if str(path) == str(allowed) else resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", changed)
    result = json.loads(
        services.registry.dispatch(
            "analyze_photo", json.dumps({"image_path": str(allowed)}), scope.tool_context
        )
    )
    assert "未授权" in result["error"] and services.client.calls == []
