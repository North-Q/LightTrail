"""用本机 HTTP keepalive 验证生产同步、异步入口的连接池生命周期。"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.exceptions import ModelAPIError

import lighttrail.adapters.llm.client as client_mod
from lighttrail.adapters.llm.client import ChatClient, LLMError
from lighttrail.api.session import SessionManager
from lighttrail.composition import build_application
from lighttrail.config import Settings
from lighttrail.contracts.context import RequestContext

MESSAGES: list[dict[str, Any]] = [{"role": "user", "content": "纯合成"}]


class _KeepaliveServer(ThreadingHTTPServer):
    """只接收本机合成请求，记录连接复用及对端实际关闭。"""

    daemon_threads = True

    def __init__(self) -> None:
        """绑定随机回环端口，状态不含认证头或个人数据。"""
        super().__init__(("127.0.0.1", 0), _KeepaliveHandler)
        self.condition = threading.Condition()
        self.accepted = 0
        self.finished = 0
        self.requests: list[dict[str, Any]] = []
        self.release = threading.Event()
        self.holding = threading.Event()

    @property
    def base_url(self) -> str:
        """返回仅测试使用的本机地址。"""
        return f"http://127.0.0.1:{self.server_port}/v1"

    def wait_closed(self) -> None:
        """有界等待所有连接收到 EOF，不能只检查 SDK 的关闭标志。"""
        with self.condition:
            assert self.condition.wait_for(lambda: self.finished == self.accepted, timeout=3)


class _KeepaliveHandler(BaseHTTPRequestHandler):
    """返回 SDK 可解析的 HTTP/1.1 响应，不主动关闭正常 keepalive。"""

    protocol_version = "HTTP/1.1"
    server: _KeepaliveServer

    def setup(self) -> None:
        """记录接受的真实 TCP 连接。"""
        super().setup()
        with self.server.condition:
            self.server.accepted += 1
            self.server.condition.notify_all()

    def finish(self) -> None:
        """记录客户端 EOF 后处理器退出。"""
        try:
            super().finish()
        finally:
            with self.server.condition:
                self.server.finished += 1
                self.server.condition.notify_all()

    def do_POST(self) -> None:
        """消费合成消息，提供成功、HTTP 错误和可取消等待路径。"""
        request = json.loads(self.rfile.read(int(self.headers["content-length"])))
        with self.server.condition:
            self.server.requests.append(request)
            self.server.condition.notify_all()
        content = request["messages"][-1]["content"]
        if content == "hold":
            self.server.holding.set()
            if not self.server.release.wait(timeout=3):
                self.close_connection = True
                return
        status = 503 if content == "retry" else 400 if content == "error" else 200
        payload = (
            {"error": {"message": "合成 HTTP 错误"}}
            if status != 200
            else {
                "id": "offline-lifecycle",
                "object": "chat.completion",
                "created": 0,
                "model": "offline",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "合成结果"},
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def log_message(self, format: str, *args: Any) -> None:
        """不打印请求或认证信息。"""


@pytest.fixture
def keepalive() -> Iterator[_KeepaliveServer]:
    """运行真实本机连接服务，测试结束后关闭监听端口及服务线程。"""
    server = _KeepaliveServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


def _settings(root: Path, server: _KeepaliveServer) -> Settings:
    """生产装配使用独立目录、假 Key、回环 URL 和显式输出限制。"""
    return Settings(
        _env_file=None,
        api_key="offline-lifecycle-only",
        base_url=server.base_url,
        data_dir=str(root),
        concurrency=1,
        reason_thinking=False,
        max_output_tokens=4096,
    )


def test_sync_runtime_saved_result_closes_real_keepalive(
    tmp_path: Path, keepalive: _KeepaliveServer
) -> None:
    """生产 Runtime 完成并保存后，同步清理不能访问已关闭的临时 loop。"""
    services = build_application(_settings(tmp_path, keepalive))
    manager = SessionManager(tmp_path)
    session = manager.create()
    try:
        text = services.create_scope(RequestContext()).runtime.complete("纯合成")
        result = manager.save_result(session, kind="chat", request={"request": "纯合成"}, text=text)
        assert SessionManager(tmp_path).get(session.session_id).saved_results()[
            0
        ] == result.model_dump(mode="json")
        print("合成结果已保存；随后执行 SDK 清理")
    finally:
        services.close()
    keepalive.wait_closed()
    before = len(keepalive.requests)
    assert SessionManager(tmp_path).get(session.session_id).saved_results()[0]["text"] == "合成结果"
    assert len(keepalive.requests) == before == 1


def test_sync_runtime_reuses_pool_across_calls_and_chat(
    tmp_path: Path, keepalive: _KeepaliveServer
) -> None:
    """多次同步 Runtime 和客户端入口共享存活循环，复用同一 keepalive。"""
    services = build_application(_settings(tmp_path, keepalive))
    try:
        scope = services.create_scope(RequestContext())
        assert scope.runtime.complete("纯合成") == "合成结果"
        assert scope.runtime.reason("纯合成") == "合成结果"
        assert services.client.chat(MESSAGES, model="offline")["content"] == "合成结果"
        assert keepalive.accepted == 1
        assert len(services.client._async_clients) == 1
        assert all(loop.is_running() for loop in services.client._async_clients)
        assert all(request["max_tokens"] == 4096 for request in keepalive.requests)
    finally:
        services.close()
    services.close()
    keepalive.wait_closed()


async def test_async_host_closes_on_owner_loop(
    tmp_path: Path, keepalive: _KeepaliveServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """异步宿主在自身循环结束前关闭连接，重复调用复用真实连接池。"""
    services = build_application(_settings(tmp_path, keepalive))
    owner = asyncio.get_running_loop()
    scope = services.create_scope(RequestContext())
    try:
        assert await scope.runtime.acomplete("纯合成") == "合成结果"
        assert await scope.runtime.acomplete("纯合成") == "合成结果"
        sdk = services.client._get_async_client()
        original_close = sdk.close
        closed_on: list[asyncio.AbstractEventLoop] = []

        async def close() -> None:
            """观察真实 SDK 关闭所在循环，不替代底层关闭。"""
            closed_on.append(asyncio.get_running_loop())
            await original_close()

        monkeypatch.setattr(sdk, "close", close)
        assert keepalive.accepted == 1
    finally:
        await services.aclose()
    await services.aclose()
    assert closed_on == [owner]
    await asyncio.to_thread(keepalive.wait_closed)
    assert sdk.is_closed() and services.client._client.is_closed()


async def test_mixed_pools_close_on_their_live_owners(
    keepalive: _KeepaliveServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同步和异步的独立真实连接池共享许可，均在所属循环关闭。"""
    client = ChatClient("offline-lifecycle-only", keepalive.base_url, concurrency=1)
    try:
        assert (await client.acall(MESSAGES, model="offline"))["content"] == "合成结果"
        assert (await asyncio.to_thread(client.chat, MESSAGES, model="offline"))[
            "content"
        ] == "合成结果"
        assert keepalive.accepted == 2
        owners = list(client._async_clients.items())
        observed: list[asyncio.AbstractEventLoop] = []
        for loop, sdk in owners:
            original_close = sdk.close

            async def close(
                owner: asyncio.AbstractEventLoop = loop, original: Any = original_close
            ) -> None:
                """先检查归属，再调用真实连接池清理。"""
                assert asyncio.get_running_loop() is owner
                observed.append(owner)
                await original()

            monkeypatch.setattr(sdk, "close", close)
    finally:
        await client.aclose()
    assert len(observed) == 2 and client.queue_position() == 0
    await asyncio.to_thread(keepalive.wait_closed)


@pytest.mark.parametrize("mode, attempts", [("error", 1), ("retry", 3)])
def test_sync_runtime_error_still_closes_connections(
    tmp_path: Path,
    keepalive: _KeepaliveServer,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    attempts: int,
) -> None:
    """HTTP 失败不跳过正常资源释放，重试预算及单连接复用保持。"""
    monkeypatch.setattr(client_mod, "_BASE_BACKOFF_SEC", 0)
    monkeypatch.setattr(
        client_mod, "wait_random", lambda *_: client_mod.wait_exponential(multiplier=0)
    )
    services = build_application(_settings(tmp_path, keepalive))
    try:
        with pytest.raises(ModelAPIError):
            services.create_scope(RequestContext()).runtime.complete(mode)
        assert len(keepalive.requests) == attempts
        assert keepalive.accepted == 1
        assert services.client.queue_position() == 0
    finally:
        services.close()
    keepalive.wait_closed()


async def test_cancelled_http_request_releases_pool_and_permit(keepalive: _KeepaliveServer) -> None:
    """取消真实传输后仍可调用及关闭，许可和 TCP 连接均释放。"""
    client = ChatClient("offline-lifecycle-only", keepalive.base_url, concurrency=1)
    task = asyncio.create_task(client.acall([{"role": "user", "content": "hold"}], model="offline"))
    try:
        assert await asyncio.to_thread(keepalive.holding.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert client.queue_position() == 0
        assert (await client.acall(MESSAGES, model="offline"))["content"] == "合成结果"
    finally:
        keepalive.release.set()
        await client.aclose()
    await asyncio.to_thread(keepalive.wait_closed)


async def test_close_failure_is_visible_and_does_not_forget_pool(
    keepalive: _KeepaliveServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """清理失败必须报告并保留句柄，后续关闭不能因提前 closed 标记假成功。"""
    client = ChatClient("offline-lifecycle-only", keepalive.base_url)
    await client.acall(MESSAGES, model="offline")
    sdk = client._get_async_client()
    original_close = sdk.close

    async def fail() -> None:
        """在实际关闭之前注入错误，不能被吞掉或泄露假凭据。"""
        raise RuntimeError("offline-lifecycle-only 合成清理失败")

    monkeypatch.setattr(sdk, "close", fail)
    try:
        with pytest.raises(LLMError, match="清理") as caught:
            await client.aclose()
        assert "offline-lifecycle-only" not in str(caught.value)
        assert sdk in client._async_clients.values()
        with pytest.raises(LLMError, match="已关闭"):
            await client.acall(MESSAGES, model="offline")
    finally:
        monkeypatch.setattr(sdk, "close", original_close)
        await client.aclose()
    await asyncio.to_thread(keepalive.wait_closed)


async def test_stopped_owner_is_rejected_without_cross_loop_close(
    keepalive: _KeepaliveServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """宿主提前停止必须明确报错；恢复原循环后可以正确关闭，不跨循环兜底。"""
    client = ChatClient("offline-lifecycle-only", keepalive.base_url)
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def serve_loop() -> None:
        """运行测试拥有的异步宿主循环。"""
        asyncio.set_event_loop(loop)
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=serve_loop, daemon=True)
    thread.start()
    await asyncio.to_thread(ready.wait, 3)
    await asyncio.wrap_future(
        asyncio.run_coroutine_threadsafe(client.acall(MESSAGES, model="offline"), loop)
    )
    sdk = client._async_clients[loop]
    original_close = sdk.close
    closed_on: list[asyncio.AbstractEventLoop] = []

    async def close() -> None:
        """观察实际关闭所在的循环。"""
        closed_on.append(asyncio.get_running_loop())
        await original_close()

    monkeypatch.setattr(sdk, "close", close)
    loop.call_soon_threadsafe(loop.stop)
    await asyncio.to_thread(thread.join, 3)
    assert not thread.is_alive()
    try:
        with pytest.raises(LLMError, match="所属事件循环"):
            await client.aclose()
        assert closed_on == [] and not sdk.is_closed()
        assert client._async_clients[loop] is sdk
    finally:
        ready.clear()
        thread = threading.Thread(target=serve_loop, daemon=True)
        thread.start()
        await asyncio.to_thread(ready.wait, 3)
        try:
            await client.aclose()
            assert closed_on == [loop]
        finally:
            loop.call_soon_threadsafe(loop.stop)
            await asyncio.to_thread(thread.join, 3)
            loop.close()
    await asyncio.to_thread(keepalive.wait_closed)
