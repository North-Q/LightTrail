"""trace → SSE 事件桥（E7-4，「trace 即 UI」核心落地）。

设计要点（架构 v2.0 §2.8）：
- TraceBridge 挂 TraceRecorder.subscribe：把 TraceEvent 映射为 SSE 协议事件
  （step / tool_call / tool_result），经 call_soon_threadsafe 线程安全放入
  asyncio.Queue——同步 ReAct/管线在 worker 线程运行，事件实时回流事件循环；
- 协议映射收敛在本模块（单一事实源）：llm 事件不入 SSE 协议（token 由端点
  按文本补发），tool 事件拆成「调用」与「结果」两条；
- 会话无关：桥只做转发，session_id 由宿主注入（本地单用户场景一个请求
  一条流，事件不跨会话）；
- 前端类型定义（web/src/api/events.ts）与本站点映射保持一致（E7-5 落地
  前端时同步）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from typing import Any

from lighttrail.infra.trace import Recorder, TraceEvent

logger = logging.getLogger("lighttrail.api.events")

# SSE 协议事件类型（与架构 v2.0 §2.8 一致）
EVENT_QUEUED = "queued"
EVENT_STEP = "step"
EVENT_TOOL_CALL = "tool_call"
EVENT_TOOL_RESULT = "tool_result"
EVENT_TOKEN = "token"
EVENT_CARD = "card"
EVENT_ERROR = "error"
EVENT_DONE = "done"


def map_trace_event(event: TraceEvent, *, session_id: str = "") -> list[dict[str, Any]] | None:
    """把 TraceEvent 映射为 SSE 协议事件列表（llm 事件返回 None）。

    Args:
        event: trace 事件快照。
        session_id: 宿主会话标识（注入到事件负载，供前端按会话归类）。

    Returns:
        SSE 事件字典列表（一个 tool 事件映射为 tool_call + tool_result 两条）；
        不可映射的 llm 事件返回 None。
    """
    if event.kind == "step":
        return [
            {
                "type": EVENT_STEP,
                "name": event.name,
                "input_summary": event.payload.get("输入摘要", ""),
                "output_summary": event.payload.get("输出摘要", ""),
                "session_id": session_id,
                "ts": event.ts,
            }
        ]
    if event.kind == "tool":
        payload = event.payload
        return [
            {
                "type": EVENT_TOOL_CALL,
                "name": event.name,
                "arguments": payload.get("参数摘要", ""),
                "session_id": session_id,
                "ts": event.ts,
            },
            {
                "type": EVENT_TOOL_RESULT,
                "name": event.name,
                "result": payload.get("结果摘要", ""),
                "data_source": payload.get("数据来源", ""),
                "confidence": payload.get("置信度", "low"),
                "field": payload.get("来源字段", ""),
                "elapsed_ms": payload.get("耗时_ms", 0.0),
                "session_id": session_id,
                "ts": event.ts,
            },
        ]
    return None


def sse_text(event: dict[str, Any]) -> str:
    """把事件字典编码为 SSE 帧（event: type + data: JSON）。

    Args:
        event: 事件字典（须含 type 键）。

    Returns:
        完成的一帧 SSE 文本（以空行结尾）。
    """
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


class TraceBridge:
    """TraceRecorder → asyncio.Queue 的事件桥（线程安全）。

    Args:
        recorder: 要订阅的记录器（每请求新建 TraceRecorder + 桥，流结束 detach）。
    """

    def __init__(self, recorder: Recorder) -> None:
        self._recorder = recorder
        self._unsub: Callable[[], None] | None = None

    def attach(self, queue: asyncio.Queue[dict[str, Any] | None], loop: asyncio.AbstractEventLoop, *, session_id: str = "") -> None:
        """订阅记录器，事件映射后线程安全入队。

        Args:
            queue: 目标事件队列（SSE 生成器消费）。
            loop: 宿主事件循环（回调线程用 call_soon_threadsafe 投递）。
            session_id: 会话标识（注入事件负载）。
        """
        self.detach()

        def _forward(event: TraceEvent) -> None:
            for mapped in map_trace_event(event, session_id=session_id) or []:
                loop.call_soon_threadsafe(queue.put_nowait, mapped)

        self._unsub = self._recorder.subscribe(_forward)

    def detach(self) -> None:
        """取消订阅（幂等，流结束后必须调用）。"""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None


async def pump(queue: asyncio.Queue[dict[str, Any] | None], worker: asyncio.Task) -> AsyncIterator[str]:
    """从队列取事件编码为 SSE 帧，直到 done；结束时取消未完成的 worker。

    Args:
        queue: 事件队列。
        worker: 后台执行任务（完成后应已推入 done 事件）。

    Yields:
        SSE 帧文本（以 done 帧结束）。
    """
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            yield sse_text(item)
            if item.get("type") == EVENT_DONE:
                break
    finally:
        if not worker.done():
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker


__all__ = [
    "EVENT_CARD",
    "EVENT_DONE",
    "EVENT_ERROR",
    "EVENT_QUEUED",
    "EVENT_STEP",
    "EVENT_TOKEN",
    "EVENT_TOOL_CALL",
    "EVENT_TOOL_RESULT",
    "TraceBridge",
    "map_trace_event",
    "pump",
    "sse_text",
]
