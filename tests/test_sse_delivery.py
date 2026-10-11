"""U2-3 独立 SSE 核查：可控延迟与持久化异常必须保留结束语义。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from lighttrail.api.events import TraceBridge, pump, to_payload
from lighttrail.api.session import SessionManager
from lighttrail.contracts.events import DoneEvent
from lighttrail.infra.trace import TraceRecorder
from tests.test_r0_2_decide_routes import _setup


async def test_delayed_trace_is_delivered_before_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """延迟跨线程投递，直接控制旧实现漏 step 的条件。"""
    recorder = TraceRecorder()
    bridge = TraceBridge(recorder)
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    callbacks: list[tuple[Any, tuple[Any, ...]]] = []
    original = loop.call_soon_threadsafe

    def delay(callback: Any, *args: Any, **kwargs: Any) -> Any:
        """只延迟事件桥投递，不影响线程完成通知。"""
        if getattr(callback, "__self__", None) in (queue, bridge):
            callbacks.append((callback, args))
            return None
        return original(callback, *args, **kwargs)

    bridge.attach(queue, loop, session_id="synthetic")
    monkeypatch.setattr(loop, "call_soon_threadsafe", delay)

    async def worker() -> None:
        """模拟工作线程完成后发结束帧。"""
        await asyncio.to_thread(recorder.record_step, "合成延迟步骤")
        if hasattr(bridge, "flush"):
            bridge.flush()
        queue.put_nowait(to_payload(DoneEvent(session_id="synthetic")))

    task = asyncio.create_task(worker())
    frames = [frame async for frame in pump(queue, task)]
    bridge.detach()
    body = "".join(frames)
    (tmp_path / "sse-delay-response.txt").write_text(body, encoding="utf-8")
    assert "event: step" in body
    assert body.index("event: step") < body.index("event: done")
    assert body.count("event: done") == 1
    for callback, args in callbacks:
        callback(*args)
    assert queue.empty(), "延迟回调不能在结束后重复投递"


@pytest.mark.parametrize("endpoint,field", [("decide", "request"), ("chat", "message")])
async def test_save_error_still_finishes_stream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    field: str,
) -> None:
    """保存失败也必须返回 error/done，不能使最终状态无限等待。"""
    app, services, fake = _setup(tmp_path, ["合成回答"])
    original = SessionManager.save

    def save(self: SessionManager, session: Any) -> None:
        """创建会话可成功，但带有结果的持久化失败。"""
        if session.history:
            raise OSError("synthetic disk unavailable request_id=save-probe")
        original(self, session)

    monkeypatch.setattr(SessionManager, "save", save)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ) as client:
            try:
                response = await asyncio.wait_for(
                    client.post(f"/api/{endpoint}", json={field: "帮我订机票去拍星空"}), timeout=1.0
                )
            except asyncio.TimeoutError:
                (tmp_path / f"sse-{endpoint}-save-timeout.txt").write_text(
                    "合成持久化失败后没有 done，ASGI 响应在 1 秒后被核查超时中止。",
                    encoding="utf-8",
                )
                pytest.fail("保存异常导致 SSE 无法结束")
            (tmp_path / f"sse-{endpoint}-save-response.txt").write_text(
                response.text, encoding="utf-8"
            )
        events = [
            json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")
        ]
        assert events[-1]["type"] == "done"
        assert events[-2]["type"] == "error"
        assert "save-probe" in events[-2]["message"]
        if endpoint == "decide":
            assert fake.calls == [], "保存故障必须独立于模型错误复现"
    finally:
        services.close()
