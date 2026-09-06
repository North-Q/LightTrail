"""ChatClient 并发策略的 pytest 用例。

验证：
- serial_llm=True 时串行锁生效：并发调用被严格串行化（观察并发峰值 = 1）；
- serial_llm=False 时允许并行：并发调用可重叠（观察并发峰值 > 1）；
- 串行只包住单次 API 往返：关闭串行时，多个调用可以同时进入重试循环。

使用「带延时 + 并发计数」的假 OpenAI 后端，通过 ThreadPoolExecutor 发车，
用并发峰值断言重叠情况——不依赖精确时序，只统计最大同时 in-flight 数。
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

import lighttrail.llm.client as client_mod
from lighttrail.llm.client import ChatClient, LLMError

_RESPONSE_DELAY = 0.15

# 假后端的并发观测器：记录同时 in-flight 的最大值
class _ConcurrencyProbe:
    def __init__(self, *, fail_times: int = 0) -> None:
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.calls = 0
        self.delays = 0.0
        self.fail_times = fail_times  # 前 N 次抛错，之后成功

    def create(self, **kwargs):
        delay = _RESPONSE_DELAY / 2  # 内部模拟耗时
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls += 1
            should_fail = self.calls <= self.fail_times
        if should_fail:
            time.sleep(delay)
            raise ConnectionError("flaky network")  # 连接层异常 → 应触发重试
        time.sleep(delay)
        with self._lock:
            self.active -= 1
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))])


def _make_probed_client(*, serial: bool, fail_times: int = 0) -> tuple[ChatClient, _ConcurrencyProbe]:
    """构造一个指向假后端的 ChatClient，返回 (client, probe)。"""
    client = ChatClient(api_key="sk-test", base_url="http://127.0.0.1:1", serial_llm=serial)
    probe = _ConcurrencyProbe(fail_times=fail_times)
    fake_completions = SimpleNamespace(create=probe.create)
    fake_chat = SimpleNamespace(completions=fake_completions)
    client._client = SimpleNamespace(chat=fake_chat)  # 替换底层 OpenAI 客户端
    return client, probe


def _fire_concurrent(client: ChatClient, n: int = 4) -> None:
    """并发发起 n 次 chat 调用，等待全部完成。"""

    def work(_):
        return client.chat([{"role": "user", "content": "hi"}])

    with ThreadPoolExecutor(max_workers=n) as ex:
        list(ex.map(work, range(n)))


@pytest.mark.parametrize("serial", [True, False])
def test_serial_controls_concurrency(serial: bool) -> None:
    """serial=True 严格串行（峰值 1）；serial=False 允许并行（峰值 >1）。"""
    client, probe = _make_probed_client(serial=serial)
    _fire_concurrent(client, n=4)
    assert probe.calls == 4
    if serial:
        assert probe.max_active == 1, "串行开启时不应出现并发调用"
    else:
        assert probe.max_active > 1, "串行关闭时应允许并发调用"


def test_serial_false_retry_not_serialized() -> None:
    """serial=False 时重试在锁外：多个调用可同时重试而不互相阻塞。

    并发 4 个调用、后端前 1 次失败：至少有一个调用触发重试（存在失败
    计数），且所有调用最终成功——证明重试没有被串行锁卡住。
    """
    client, probe = _make_probed_client(serial=False, fail_times=1)
    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(lambda _: client.chat([{"role": "user", "content": "hi"}]), range(4)))
    assert all(r["content"] == "ok" for r in results)
    # 至少发生一次失败（并发下其余调用可能在失败者重试期间已成功）
    assert probe.calls > 4, "应至少有一个调用触发重试（calls > 成功数）"


def test_serial_true_retries_still_work() -> None:
    """serial=True 时重试仍生效：瞬时错误后成功。"""
    client, probe = _make_probed_client(serial=True, fail_times=1)
    resp = client.chat([{"role": "user", "content": "hi"}])
    assert resp["content"] == "ok"
    assert probe.calls == 2  # 1 次失败 + 1 次成功


def test_exhaust_retries_raises() -> None:
    """重试耗尽抛 LLMError。"""
    client, probe = _make_probed_client(serial=True, fail_times=10_000)
    with pytest.raises(LLMError):
        client.chat([{"role": "user", "content": "hi"}])
    # 重试次数 = _MAX_RETRIES（3），不多不少
    assert probe.calls == client_mod._MAX_RETRIES