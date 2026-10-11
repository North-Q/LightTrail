"""U2-1 离线反例：取消许可、真实 SDK 尝试次数与配置接线。"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

import lighttrail.adapters.llm.client as client_mod
from lighttrail.adapters.llm.client import ChatClient, LLMError, _ConcurrencyLimiter
from lighttrail.api.events import pump
from lighttrail.composition import build_application, build_client
from lighttrail.config import Settings


class _ObservedSemaphore:
    """为真实信号量记录排队与成功获取时刻，不改变许可规则。"""

    def __init__(self, limit: int) -> None:
        self.inner = threading.BoundedSemaphore(limit)
        self.entered = threading.Event()
        self.obtained = threading.Event()

    def acquire(self, blocking: bool = True) -> bool:
        """转发获取，供测试精确安排取消时刻。"""
        self.entered.set()
        result = self.inner.acquire(blocking=blocking)
        if result:
            self.obtained.set()
        return result

    def release(self) -> None:
        """归还一个许可。"""
        self.inner.release()


async def test_cancel_queued_request_preserves_permit() -> None:
    """取消排队者后许可仍完整，下一请求可完成；失败时也回收测试线程。"""
    limiter = _ConcurrencyLimiter(1)
    observed = _ObservedSemaphore(1)
    limiter._semaphore = observed

    async def queued() -> None:
        """尝试进入临界区，正确行为是取消前始终没有许可。"""
        async with limiter.acquire():
            pytest.fail("排队者不应进入临界区")

    with limiter.sync():
        observed.entered.clear()
        observed.obtained.clear()
        task = asyncio.create_task(queued())
        while not observed.entered.is_set():
            await asyncio.sleep(0.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    # 原实现遗留的 acquire 线程会在持有者退出后拿走许可。
    await asyncio.sleep(0.03)
    available = observed.inner.acquire(blocking=False)
    if available:
        observed.inner.release()
    elif observed.obtained.is_set():
        observed.inner.release()  # 反例失败也不让线程池退出挂起。
    assert limiter.in_flight() == 0
    assert available, "取消排队者泄漏了唯一许可"
    async with asyncio.timeout(0.5):
        async with limiter.acquire():
            assert limiter.in_flight() == 1
    assert limiter.in_flight() == 0


def test_production_timeout_is_forwarded() -> None:
    """生产装配必须把自定义连接/读取超时交给真实 SDK。"""
    client = build_client(Settings(_env_file=None, api_key="offline", llm_timeout=(0.12, 0.34)))
    timeout = client._client.timeout
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 0.12
    assert timeout.read == 0.34
    assert client._client.max_retries == 0
    client._client.close()


@pytest.mark.parametrize("status, expected_calls", [(429, 3), (503, 3), (400, 1), (401, 1)])
async def test_real_sdk_has_one_counted_retry_budget(
    monkeypatch: pytest.MonkeyPatch, status: int, expected_calls: int
) -> None:
    """HTTP 离线传输统计底层次数，不能只统计外层 SDK create 次数。"""
    monkeypatch.setattr(client_mod, "_BASE_BACKOFF_SEC", 0)
    monkeypatch.setattr(
        client_mod, "wait_random", lambda *_: client_mod.wait_exponential(multiplier=0)
    )
    calls = 0

    def transport(request: httpx.Request) -> httpx.Response:
        """始终返回指定 HTTP 状态，不发起网络连接。"""
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={"error": {"message": "offline"}})

    client = ChatClient("offline", "http://offline", concurrency=1)
    sdk = client._get_async_client()
    await sdk._client.aclose()
    sdk._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    try:
        with pytest.raises(Exception) as caught:
            await client.acall([{"role": "user", "content": "synthetic"}], model="offline")
        assert not isinstance(caught.value, asyncio.CancelledError)
        assert calls == expected_calls
        assert client.queue_position() == 0
    finally:
        await sdk.close()
        client._client.close()


@pytest.fixture
def immediate_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """关闭测试退避，不改变总尝试上限或可重试判定。"""
    monkeypatch.setattr(client_mod, "_BASE_BACKOFF_SEC", 0)
    monkeypatch.setattr(
        client_mod, "wait_random", lambda *_: client_mod.wait_exponential(multiplier=0)
    )


class _ControlledBackend:
    """跨 loop 的纯离线替身：可挂起、失败，记录并发和取消。"""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls: list[str] = []
        self.active = 0
        self.peak = 0

    async def create(self, **payload: Any) -> Any:
        """仅本地等待或抛异常，不发送网络请求。"""
        mode = payload["messages"][0]["content"]
        with self.lock:
            self.calls.append(mode)
            self.active += 1
            self.peak = max(self.peak, self.active)
        self.entered.set()
        try:
            if mode == "hold":
                while not self.release.is_set():
                    await asyncio.sleep(0.001)
            elif mode == "timeout":
                raise httpx.ReadTimeout("offline timeout")
            elif mode == "exception":
                raise ValueError("offline nonretry error")
            await asyncio.sleep(0.001)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))]
            )
        finally:
            with self.lock:
                self.active -= 1


def _controlled() -> tuple[ChatClient, _ControlledBackend]:
    """给真实客户端注入可控异步替身。"""
    client = ChatClient("offline", "http://offline", concurrency=1)
    backend = _ControlledBackend()
    client._async_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=backend.create))
    )
    return client, backend


async def _call(client: ChatClient, mode: str) -> dict[str, Any]:
    """统一发送合成消息，替身用其内容控制行为。"""
    return await client.acall([{"role": "user", "content": mode}], model="offline")


async def test_repeated_queue_and_active_cancellation(immediate_retry: None) -> None:
    """反复取消排队者、执行者及排队超时后，许可均可完整重用。"""
    client, backend = _controlled()
    try:
        for _ in range(8):
            backend.entered.clear()
            holder = asyncio.create_task(_call(client, "hold"))
            while not backend.entered.is_set():
                await asyncio.sleep(0.001)
            queued = asyncio.create_task(_call(client, "queued"))
            await asyncio.sleep(0.015)
            queued.cancel()
            with pytest.raises(asyncio.CancelledError):
                await queued
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(_call(client, "queued-timeout"), timeout=0.015)
            holder.cancel()
            with pytest.raises(asyncio.CancelledError):
                await holder
            assert client.queue_position() == 0
            assert (await asyncio.wait_for(_call(client, "ok"), 0.5))["content"] == "ok"
        assert "queued" not in backend.calls and "queued-timeout" not in backend.calls
        assert backend.peak == 1 and backend.active == 0
        assert client._limiter._semaphore.acquire(blocking=False)
        assert not client._limiter._semaphore.acquire(blocking=False)
        client._limiter._semaphore.release()
    finally:
        await client.aclose()


async def test_timeout_and_nonretry_exception_release_permits(immediate_retry: None) -> None:
    """超时仅尝试三次，非重试错误一次；之后同步/异步请求继续成功。"""
    client, backend = _controlled()
    try:
        for mode, error, count in [("timeout", LLMError, 3), ("exception", ValueError, 1)]:
            with pytest.raises(error):
                await _call(client, mode)
            assert backend.calls.count(mode) == count
            assert client.queue_position() == 0
            assert (await asyncio.to_thread(client.chat, [{"role": "user", "content": "ok"}]))[
                "content"
            ] == "ok"
            assert (await _call(client, "ok"))["content"] == "ok"
        assert backend.peak == 1
    finally:
        await client.aclose()


async def test_cancel_during_retry_backoff_stops_further_attempts() -> None:
    """退避期没有占用许可，取消后不继续发送下一次尝试。"""
    client, backend = _controlled()
    task = asyncio.create_task(_call(client, "timeout"))
    try:
        while not backend.entered.is_set():
            await asyncio.sleep(0.001)
        await asyncio.sleep(0.02)
        assert client.queue_position() == 0 and backend.calls == ["timeout"]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (await _call(client, "ok"))["content"] == "ok"
        assert backend.calls == ["timeout", "ok"]
    finally:
        task.cancel()
        await client.aclose()


@pytest.mark.parametrize(
    "timeout, expected_calls", [((0.01, 0.04), 3), ((0.04, 0.01), 3), ((0.04, 0.04), 1)]
)
async def test_connect_and_read_configuration_reaches_transport(
    immediate_retry: None, timeout: tuple[float, float], expected_calls: int
) -> None:
    """可控传输按真实 SDK 请求的阶段超时模拟连接/读取，无外部连接。"""
    client = build_client(Settings(_env_file=None, api_key="offline", llm_timeout=timeout))
    sdk = client._get_async_client()
    calls = 0

    async def transport(request: httpx.Request) -> httpx.Response:
        """从实际 HTTP 请求读取阶段预算，短预算超时，长预算成功。"""
        nonlocal calls
        calls += 1
        phases = request.extensions["timeout"]
        assert phases["connect"] == timeout[0] and phases["read"] == timeout[1]
        assert phases["write"] == phases["pool"] == timeout[0]
        for phase, error in (("connect", httpx.ConnectTimeout), ("read", httpx.ReadTimeout)):
            try:
                await asyncio.wait_for(asyncio.sleep(0.02), phases[phase])
            except asyncio.TimeoutError as exc:
                raise error("offline phase timeout", request=request) from exc
        return httpx.Response(
            200,
            json={
                "id": "offline",
                "object": "chat.completion",
                "created": 0,
                "model": "offline",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "ok"},
                    }
                ],
            },
        )

    await sdk._client.aclose()
    sdk._client = httpx.AsyncClient(transport=httpx.MockTransport(transport), timeout=sdk.timeout)
    try:
        if expected_calls == 3:
            with pytest.raises(LLMError):
                await _call(client, "synthetic")
        else:
            assert (await _call(client, "synthetic"))["content"] == "ok"
        assert calls == expected_calls and client.queue_position() == 0
    finally:
        await client.aclose()


async def test_sync_and_async_facades_share_limit() -> None:
    """同步后台 loop 持有许可时，异步排队者取消不能影响后续两种入口。"""
    client, backend = _controlled()
    holder = asyncio.create_task(
        asyncio.to_thread(client.chat, [{"role": "user", "content": "hold"}])
    )
    try:
        while not backend.entered.is_set():
            await asyncio.sleep(0.001)
        queued = asyncio.create_task(_call(client, "queued"))
        await asyncio.sleep(0.015)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        backend.release.set()
        assert (await asyncio.wait_for(holder, 1))["content"] == "ok"
        results = await asyncio.gather(
            _call(client, "ok"),
            asyncio.to_thread(client.chat, [{"role": "user", "content": "ok"}]),
        )
        assert all(result["content"] == "ok" for result in results)
        assert backend.peak == 1 and client.queue_position() == 0
    finally:
        backend.release.set()
        await holder
        await client.aclose()


@pytest.mark.parametrize("value", [(0, 1), (-1, 2), (float("inf"), 1), (1, float("nan"))])
def test_invalid_timeout_rejected(value: tuple[float, float]) -> None:
    """超时上限不能因零、负数或非有限值被静默禁用。"""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_timeout=value)


async def test_real_httpx_read_timeout_takes_effect(immediate_retry: None) -> None:
    """本地迟延 HTTP 替身验证真实传输读取超时，不依赖 Mock 的超时实现。"""
    received = 0
    handlers: set[asyncio.Task[Any]] = set()
    release = asyncio.Event()

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """只接收合成请求后挂起，不向任何外部服务转发。"""
        nonlocal received
        task = asyncio.current_task()
        handlers.add(task)
        try:
            await reader.readuntil(b"\r\n\r\n")
            received += 1
            await release.wait()
        finally:
            writer.close()
            await writer.wait_closed()
            handlers.discard(task)

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = build_client(
        Settings(
            _env_file=None,
            api_key="offline",
            base_url=f"http://127.0.0.1:{port}/v1",
            llm_timeout=(0.1, 0.02),
        )
    )
    try:
        with pytest.raises(LLMError):
            await asyncio.wait_for(_call(client, "synthetic"), 2)
        assert received == 3
        assert client.queue_position() == 0
        sdk = client._get_async_client()
        assert sdk.timeout.connect == 0.1 and sdk.timeout.read == 0.02
        assert sdk.max_retries == 0
    finally:
        release.set()
        server.close()
        await server.wait_closed()
        await asyncio.gather(*handlers)
        await client.aclose()


async def test_loop_owned_sdk_pools_close_without_cross_loop_reuse() -> None:
    """真实 SDK 连接池按 loop 复用，同步与异步池共享限流且在宿主退出前关闭。"""
    client = ChatClient("offline", "http://offline")
    current_sdk = client._get_async_client()
    assert client._get_async_client() is current_sdk

    async def other_sdk() -> Any:
        """从同步门面的后台事件循环创建另一个连接池。"""
        return client._get_async_client()

    background_sdk = await asyncio.to_thread(client_mod._run_sync, other_sdk())
    assert background_sdk is not current_sdk
    await client.aclose()
    await client.aclose()
    assert current_sdk.is_closed() and background_sdk.is_closed() and client._client.is_closed()
    with pytest.raises(LLMError, match="已关闭"):
        await _call(client, "ok")


async def test_application_closes_owned_client_only(tmp_path: Any) -> None:
    """应用关闭自建客户端，外部注入的客户端仍由调用者管理。"""
    settings = Settings(_env_file=None, api_key="offline", data_dir=str(tmp_path))
    owned = build_application(settings)
    sdk = owned.client._get_async_client()
    await owned.aclose()
    assert sdk.is_closed()
    external = ChatClient("offline", "http://offline")
    injected = build_application(settings, client=external)
    await injected.aclose()
    assert not external._client.is_closed()
    await external.aclose()


async def test_sse_disconnect_does_not_claim_sync_worker_cancelled() -> None:
    """取消 SSE 等待协程不会杀死已运行线程；替身释放后线程才结束。"""
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    def run() -> None:
        """可控同步工作，明确展示浏览器停止的实际边界。"""
        started.set()
        release.wait(timeout=2)
        finished.set()

    async def work() -> None:
        """把同步工作交给线程池。"""
        await asyncio.to_thread(run)

    worker = asyncio.create_task(work())
    stream = pump(queue, worker)
    queue.put_nowait({"type": "queued"})
    try:
        await anext(stream)
        while not started.is_set():
            await asyncio.sleep(0.001)
        await stream.aclose()
        assert worker.cancelled() and not finished.is_set()
    finally:
        release.set()
        await asyncio.to_thread(finished.wait, 1)
    assert finished.is_set()
