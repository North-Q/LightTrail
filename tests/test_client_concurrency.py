"""ChatClient 并发与容错的 pytest 用例（B3-1 重写后的契约）。

B3-1 契约变化：
- 并发是**纯配置**（ADR-004）：`concurrency=1` 等价旧「串行」，默认 4；旧 `serial_llm` 只作兼容别名；
- **单一实现**：真正逻辑在 `acall`（async-first），同步 `chat` 是后台共享事件循环上的门面；
- **单一并发机制**：一个跨 loop 的限流器（`threading.BoundedSemaphore` + `asyncio.to_thread`），
  旧的三套机制（Lock + 自研 FIFO 闸门 + call_soon_threadsafe 桥）已删除；
- 限流只包单次 API 往返，重试在限流之外。

验证方式：用「异步假后端 + 并发峰值探针」，`asyncio.gather` 发车，断言在途峰值。
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any

import pytest

import lighttrail.llm.client as client_mod
from lighttrail.llm.client import ChatClient, LLMError

_DELAY = 0.05


class _AsyncProbe:
    """异步假后端：记录在途峰值、调用次数，可注入前 N 次失败。"""

    def __init__(self, *, fail_times: int = 0) -> None:
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.calls = 0
        self.fail_times = fail_times
        self.last_kwargs: dict[str, Any] | None = None

    async def create(self, **kwargs: Any):
        """模拟一次 API 往返（并发观测点）。"""
        self.last_kwargs = dict(kwargs)
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls += 1
            should_fail = self.calls <= self.fail_times
        try:
            await asyncio.sleep(_DELAY)
            if should_fail:
                raise ConnectionError("flaky network")  # 连接层异常 → 可重试
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))]
            )
        finally:
            with self._lock:
                self.active -= 1


def _probed_client(*, concurrency: int, fail_times: int = 0) -> tuple[ChatClient, _AsyncProbe]:
    """构造指向异步假后端的 ChatClient（不触网）。"""
    client = ChatClient(api_key="sk-test", base_url="http://127.0.0.1:1", concurrency=concurrency)
    probe = _AsyncProbe(fail_times=fail_times)
    client._async_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=probe.create))
    )
    return client, probe


async def _fire(client: ChatClient, n: int = 4) -> list[dict[str, Any]]:
    """并发发起 n 次 acall。"""
    return await asyncio.gather(*(client.acall([{"role": "user", "content": "hi"}]) for _ in range(n)))


async def test_concurrency_one_serializes() -> None:
    """concurrency=1：严格串行（在途峰值 = 1）。"""
    client, probe = _probed_client(concurrency=1)
    results = await _fire(client, n=4)
    assert all(r["content"] == "ok" for r in results)
    assert probe.calls == 4
    assert probe.max_active == 1


async def test_concurrency_allows_overlap() -> None:
    """concurrency=4：允许并行且不超过上限。"""
    client, probe = _probed_client(concurrency=4)
    await _fire(client, n=4)
    assert 1 < probe.max_active <= 4


async def test_queue_position_tracks_in_flight() -> None:
    """queue_position 反映在途请求数（空闲 0；执行中 > 0）。"""
    client, _ = _probed_client(concurrency=2)
    assert client.queue_position() == 0
    task = asyncio.create_task(client.acall([{"role": "user", "content": "hi"}]))
    await asyncio.sleep(_DELAY / 2)
    assert client.queue_position() >= 1
    await task
    assert client.queue_position() == 0


async def test_retry_outside_limiter() -> None:
    """重试在限流之外：瞬时错误后成功（调用次数 = 失败 + 成功）。"""
    client, probe = _probed_client(concurrency=2, fail_times=1)
    resp = await client.acall([{"role": "user", "content": "hi"}])
    assert resp["content"] == "ok"
    assert probe.calls == 2


async def test_exhaust_retries_raises() -> None:
    """重试耗尽抛 LLMError，且次数恰为 _MAX_RETRIES。"""
    client, probe = _probed_client(concurrency=2, fail_times=10_000)
    with pytest.raises(LLMError):
        await client.acall([{"role": "user", "content": "hi"}])
    assert probe.calls == client_mod._MAX_RETRIES


def test_sync_facade_delegates_to_async() -> None:
    """同步门面在后台共享事件循环上执行同一实现（不另写一份）。"""
    client, probe = _probed_client(concurrency=4)
    reply = client.chat([{"role": "user", "content": "hi"}])
    assert reply["content"] == "ok"
    assert probe.calls == 1


async def test_sync_facade_rejects_running_loop() -> None:
    """事件循环内调用同步门面 → 明确报错（提示改用 acall），不静默死锁。"""
    client, _ = _probed_client(concurrency=4)
    with pytest.raises(LLMError):
        client.chat([{"role": "user", "content": "hi"}])


def test_serial_llm_alias_maps_to_concurrency_one() -> None:
    """兼容别名：serial_llm=True → concurrency=1（B5 批次删除该别名）。"""
    client = ChatClient(api_key="sk-test", base_url="http://127.0.0.1:1", serial_llm=True)
    assert client.concurrency == 1


# ------ reason 扩展参数：SDK 原生 vs extra_body（平台/SDK 版本中立，ADR-003）------
def test_reason_params_via_extra_body_when_sdk_lacks_native(monkeypatch) -> None:
    """SDK 无原生 thinking 参数时：扩展参数经 extra_body 送达请求体。"""
    monkeypatch.setattr(client_mod, "_NATIVE_REASON_PARAMS", False)
    client, probe = _probed_client(concurrency=1)
    client.chat(
        [{"role": "user", "content": "hi"}],
        model="ecnu-max",
        thinking={"type": "enabled"},
        reasoning_effort="high",
    )
    assert probe.last_kwargs["extra_body"]["thinking"] == {"type": "enabled"}
    assert probe.last_kwargs["extra_body"]["reasoning_effort"] == "high"
    assert "thinking" not in probe.last_kwargs


def test_reason_params_native_when_sdk_supports(monkeypatch) -> None:
    """SDK 原生支持时：用命名参数，不引入 extra_body。"""
    monkeypatch.setattr(client_mod, "_NATIVE_REASON_PARAMS", True)
    client, probe = _probed_client(concurrency=1)
    client.chat([{"role": "user", "content": "hi"}], model="ecnu-max", thinking={"type": "enabled"})
    assert probe.last_kwargs["thinking"] == {"type": "enabled"}
    assert "extra_body" not in probe.last_kwargs


def test_reason_params_absent_do_not_add_extra_body(monkeypatch) -> None:
    """无扩展参数时零污染（不产生 extra_body）。"""
    monkeypatch.setattr(client_mod, "_NATIVE_REASON_PARAMS", False)
    client, probe = _probed_client(concurrency=1)
    client.chat([{"role": "user", "content": "hi"}], model="ecnu-plus")
    assert "extra_body" not in probe.last_kwargs