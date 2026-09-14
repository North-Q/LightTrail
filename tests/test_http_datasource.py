"""httpx 数据源基件的 pytest 用例（B3-2：统一重试与错误语义）。

验证（全部离线：httpx.MockTransport 假后端）：
- 429/5xx 可重试：重试后成功（调用次数 = 失败 + 成功）；
- 4xx（非 429）参数错误立即失败（不重试），错误里带服务端原因；
- 网络错误重试耗尽后抛 DataSourceError，且提示已重试次数。
"""

from __future__ import annotations

import httpx
import pytest

from lighttrail.infra.http import DEFAULT_MAX_ATTEMPTS, DataSourceError, get_json


def _client(handler) -> httpx.AsyncClient:
    """构造使用假传输层的异步客户端（不触网）。"""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_retryable_status_then_success() -> None:
    """429 先失败、再成功：共 2 次请求，返回解析后的 JSON。"""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, text="rate limited")
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    try:
        data = await get_json("https://example.invalid/api", client=client)
    finally:
        await client.aclose()
    assert data == {"ok": True}
    assert len(calls) == 2


async def test_client_error_fails_immediately() -> None:
    """4xx（非 429）参数错误：立即失败、不重试，错误带服务端原因。"""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, text="bad variable name")

    client = _client(handler)
    try:
        with pytest.raises(DataSourceError) as excinfo:
            await get_json("https://example.invalid/api", client=client)
    finally:
        await client.aclose()
    assert len(calls) == 1
    assert "HTTP 400" in str(excinfo.value)
    assert "bad variable name" in str(excinfo.value)


async def test_transport_error_exhausts_retries() -> None:
    """网络错误：重试到上限后抛 DataSourceError（提示已重试次数）。"""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("connection refused", request=request)

    client = _client(handler)
    try:
        with pytest.raises(DataSourceError) as excinfo:
            await get_json("https://example.invalid/api", client=client, max_attempts=2)
    finally:
        await client.aclose()
    assert len(calls) == 2
    assert "已重试 2 次" in str(excinfo.value)
    assert DEFAULT_MAX_ATTEMPTS == 3  # 默认 3 次（与 llm/client 重试次数一致）