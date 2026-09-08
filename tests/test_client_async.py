"""ChatClient async 通道（acall）的 pytest 用例（E7-1）。

验证（roadmap E7-1 验收）：
- serial_llm=True：并发 acall 被严格串行化（并发峰值 = 1，时间戳无重叠）；
- serial_llm=False：允许并行（并发峰值 > 1）；
- 串行只包住单次 API 往返：闸门排队位置可查询（queue_position，供 queued 事件）；
- 重试迁移为 async 版本（asyncio.sleep，不阻塞事件循环）；
- acall 与 chat() 共用扩展参数 / 消息转换 / 配额记账逻辑（结果形态一致）。

使用「带延时 + 并发计数」的假 AsyncOpenAI 后端，用 asyncio.gather 发车，
以并发峰值断言重叠情况——不依赖精确时序，只统计最大同时 in-flight 数。
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

import lighttrail.llm.client as client_mod
from lighttrail.infra.quota import QuotaLedger
from lighttrail.llm.client import ChatClient, LLMError

_RESPONSE_DELAY = 0.08


def _plain_response() -> SimpleNamespace:
    """普通文本响应（无 tool_calls、无 usage）。"""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))]
    )


def _tool_call_response() -> SimpleNamespace:
    """带工具调用消息的响应。"""
    tc = SimpleNamespace(
        id="call_1",
        type="function",
        function=SimpleNamespace(name="sun_times", arguments='{"date": "2026-09-12"}'),
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tc]))]
    )


class _AsyncConcurrencyProbe:
    """假异步后端：并发观测 + 可选前 N 次失败 + 可选带工具调用响应。"""

    def __init__(
        self,
        *,
        fail_times: int = 0,
        delay: float = _RESPONSE_DELAY,
        with_tool_calls: bool = False,
    ) -> None:
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.calls = 0
        self.fail_times = fail_times
        self.delay = delay
        self.with_tool_calls = with_tool_calls
        self.last_kwargs: dict | None = None

    async def create(self, **kwargs):
        self.last_kwargs = dict(kwargs)
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls += 1
            should_fail = self.calls <= self.fail_times
        if should_fail:
            await asyncio.sleep(self.delay / 2)
            with self._lock:
                self.active -= 1
            raise ConnectionError("flaky network")  # 连接层异常 → 应触发重试
        await asyncio.sleep(self.delay)
        with self._lock:
            self.active -= 1
        if self.with_tool_calls:
            return _tool_call_response()
        return _plain_response()


def _make_async_probed_client(
    *,
    serial: bool,
    fail_times: int = 0,
    delay: float = _RESPONSE_DELAY,
    with_tool_calls: bool = False,
) -> tuple[ChatClient, _AsyncConcurrencyProbe]:
    """构造一个指向假异步后端的 ChatClient，返回 (client, probe)。"""
    client = ChatClient(api_key="sk-test", base_url="http://127.0.0.1:1", serial_llm=serial)
    probe = _AsyncConcurrencyProbe(fail_times=fail_times, delay=delay, with_tool_calls=with_tool_calls)
    # 动态引用 probe.create：测试中替换 probe.create 后 fake 立即生效
    fake_completions = SimpleNamespace(create=lambda **kwargs: probe.create(**kwargs))
    fake_chat = SimpleNamespace(completions=fake_completions)
    client._async_client = SimpleNamespace(chat=fake_chat)  # 替换底层 AsyncOpenAI
    return client, probe


async def test_acall_serial_true_strictly_serial() -> None:
    """serial=True：并发 3 个 acall 严格串行（并发峰值 = 1）。"""
    client, probe = _make_async_probed_client(serial=True)
    results = await asyncio.gather(
        *[client.acall([{"role": "user", "content": "hi"}]) for _ in range(3)]
    )
    assert [r["content"] for r in results] == ["ok"] * 3
    assert probe.calls == 3
    assert probe.max_active == 1, "串行开启时不应出现并发 LLM 调用"


async def test_acall_serial_false_allows_parallel() -> None:
    """serial=False：并发 3 个 acall 可重叠（并发峰值 > 1）。"""
    client, probe = _make_async_probed_client(serial=False)
    results = await asyncio.gather(
        *[client.acall([{"role": "user", "content": "hi"}]) for _ in range(3)]
    )
    assert [r["content"] for r in results] == ["ok"] * 3
    assert probe.max_active > 1, "串行关闭时应允许并发 LLM 调用"


async def test_queue_position_reports_waiters() -> None:
    """闸门排队位置可查询：执行中 1 个时新请求排第 2、3 位，完成后归零。"""
    client, probe = _make_async_probed_client(serial=True)
    started = asyncio.Event()
    release = asyncio.Event()
    original_create = probe.create

    async def held_create(**kwargs):  # 第一个调用持闸直到 release
        started.set()
        await release.wait()
        return await original_create(**kwargs)

    probe.create = held_create  # type: ignore[method-assign]
    task1 = asyncio.create_task(client.acall([{"role": "user", "content": "hi"}]))
    await started.wait()
    assert client.queue_position() == 1  # 第一个请求正在执行

    task2 = asyncio.create_task(client.acall([{"role": "user", "content": "hi"}]))
    task3 = asyncio.create_task(client.acall([{"role": "user", "content": "hi"}]))
    await asyncio.sleep(0.01)  # 让后两个请求进入闸门队列
    assert client.queue_position() == 3

    release.set()
    await asyncio.gather(task1, task2, task3)
    assert probe.calls == 3
    assert client.queue_position() == 0  # 全部完成后空闲


async def test_queue_position_zero_when_not_serial() -> None:
    """serial=False 无闸门：queue_position 恒为 0（无需排队）。"""
    client, _ = _make_async_probed_client(serial=False)
    assert client.queue_position() == 0


async def test_acall_retries_then_succeeds(monkeypatch) -> None:
    """瞬时错误后自愈：async 重试生效（asyncio.sleep 退避，不阻塞事件循环）。"""
    monkeypatch.setattr(client_mod, "_BASE_BACKOFF_SEC", 0.001)
    monkeypatch.setattr(client_mod.random, "uniform", lambda a, b: 0.0)
    client, probe = _make_async_probed_client(serial=True, fail_times=1)
    resp = await client.acall([{"role": "user", "content": "hi"}])
    assert resp["content"] == "ok"
    assert probe.calls == 2  # 1 次失败 + 1 次成功


async def test_acall_exhaust_retries_raises(monkeypatch) -> None:
    """重试耗尽抛 LLMError（async 通道）。"""
    monkeypatch.setattr(client_mod, "_BASE_BACKOFF_SEC", 0.001)
    monkeypatch.setattr(client_mod.random, "uniform", lambda a, b: 0.0)
    client, probe = _make_async_probed_client(serial=True, fail_times=10_000)
    with pytest.raises(LLMError):
        await client.acall([{"role": "user", "content": "hi"}])
    assert probe.calls == client_mod._MAX_RETRIES  # 不多不少


async def test_acall_converts_tool_calls() -> None:
    """acall 结果与 chat() 同一转换逻辑：tool_calls 字段结构一致。"""
    client, _probe = _make_async_probed_client(serial=True, with_tool_calls=True)
    resp = await client.acall([{"role": "user", "content": "hi"}])
    assert resp["role"] == "assistant"
    assert resp["tool_calls"][0]["function"]["name"] == "sun_times"


async def test_acall_reason_params_via_extra_body(monkeypatch) -> None:
    """SDK 无原生 thinking 参数时：扩展参数经 extra_body 送达 async 请求体。"""
    monkeypatch.setattr(client_mod, "_NATIVE_REASON_PARAMS", False)
    client, probe = _make_async_probed_client(serial=True)
    await client.acall(
        [{"role": "user", "content": "hi"}],
        model="ecnu-max",
        thinking={"type": "enabled"},
        reasoning_effort="high",
    )
    assert probe.last_kwargs["extra_body"]["thinking"] == {"type": "enabled"}
    assert probe.last_kwargs["extra_body"]["reasoning_effort"] == "high"
    assert "thinking" not in probe.last_kwargs  # 未占用 SDK 命名参数


async def test_acall_records_usage_to_quota() -> None:
    """acall 成功响应带 usage 时顺带配额记账（E4-2 集成）。"""
    quota = QuotaLedger()

    def with_usage_response() -> SimpleNamespace:
        usage = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=50,
            prompt_tokens_details=SimpleNamespace(cached_tokens=10),
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))],
            usage=usage,
        )

    client = ChatClient(
        api_key="sk-test", base_url="http://127.0.0.1:1", serial_llm=True, quota=quota
    )
    probe = _AsyncConcurrencyProbe()

    async def create_with_usage(**kwargs):
        await asyncio.sleep(0)
        return with_usage_response()

    probe.create = create_with_usage  # type: ignore[method-assign]
    client._async_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: probe.create(**kw)))
    )
    resp = await client.acall([{"role": "user", "content": "hi"}], model="ecnu-plus")
    assert resp["content"] == "ok"
    assert quota.usage().hours_ratio > 0  # 已记账
