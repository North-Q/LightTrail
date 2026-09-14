"""会话与并发（异步形态）回归用例（B3-5：B0 死锁修复在 async 链路下复验）。

背景：B0-1 修的三颗 bug 是并发/死锁类，B3 把自由对话搬到 async 链路（AgentRuntime + SSE +
session 落盘在事件循环线程、LLM 调用在 worker 线程）。本用例在异步形态下重新压一遍：
- 并发多请求（含共享 session_id）不挂死、不漏 done 事件；
- 会话落盘为完整 JSON（原子写 + 快照隔离仍有效）。

全部离线：伪 ChatClient（chat/acall 双通道）+ 临时 data 目录。
"""

from __future__ import annotations

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from lighttrail.api.app import ApiDeps, create_app
from lighttrail.api.session import SessionManager
from lighttrail.composition import build_memory, build_registry
from lighttrail.config import Settings


class _FakeChatClient:
    """离线伪客户端：固定文本回复（chat/acall 双通道）。"""

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, **kwargs) -> dict:
        self.calls += 1
        return {"role": "assistant", "content": "现在是 12:00。"}

    async def acall(self, messages, **kwargs) -> dict:
        self.calls += 1
        await asyncio.sleep(0.01)  # 制造并发窗口
        return {"role": "assistant", "content": "现在是 12:00。"}

    def queue_position(self) -> int:
        return 0


async def _collect(client: AsyncClient, payload: dict) -> list[dict]:
    """POST /api/chat 并收集 SSE 事件。"""
    events: list[dict] = []
    async with client.stream("POST", "/api/chat", json=payload) as response:
        async for line in response.aiter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:].strip()))
    return events


@pytest.fixture()
def app_with_fake(tmp_path):
    """构造指向临时目录 + 伪客户端的应用（离线、可并发）。"""
    settings = Settings(_env_file=None, api_key="sk-test", data_dir=str(tmp_path))
    memory = build_memory(settings)
    deps = ApiDeps(
        client=_FakeChatClient(),  # type: ignore[arg-type]
        sessions=SessionManager(settings.data_dir, max_sessions=2),  # 上限压小 → 触发淘汰路径
        memory=memory,
        registry=build_registry(),
        model="ecnu-plus",
        reason_thinking=False,
    )
    return create_app(deps), tmp_path


async def test_concurrent_chat_requests_complete_without_deadlock(app_with_fake) -> None:
    """并发 4 个请求（含同 session_id 复用）：全部拿到 done，不挂死。"""
    app, _ = app_with_fake
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        batches = await asyncio.gather(
            _collect(client, {"message": "现在几点？", "session_id": "shared"}),
            _collect(client, {"message": "现在几点？", "session_id": "shared"}),
            _collect(client, {"message": "现在几点？"}),
            _collect(client, {"message": "现在几点？"}),
        )

    for events in batches:
        kinds = [event["type"] for event in events]
        assert kinds[-1] == "done"
        assert "error" not in kinds


async def test_concurrent_sessions_persist_valid_json(app_with_fake) -> None:
    """并发写入后：每个会话文件都是完整可解析的 JSON（快照隔离 + 原子写仍有效）。"""
    app, tmp_path = app_with_fake
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await asyncio.gather(
            *(_collect(client, {"message": f"第 {index} 问"}) for index in range(4))
        )

    files = list((tmp_path / "sessions").glob("*.json"))
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["session_id"] == path.stem