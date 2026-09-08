"""FastAPI SSE 路由（E7-3）：五端点 + 事件协议（架构 v2.0 §2.8）。

设计要点：
- 薄服务层：只做协议转换与会话管理，业务逻辑在 Agent/Orchestrator（§1 边界原则）；
- 线程模型（§2.9）：同步 ReAct/管线在 worker 线程执行（anyio.to_thread），
  TraceRecorder 事件经 asyncio.Queue 桥接回事件循环（call_soon_threadsafe），
  事件循环不被同步 LLM/工具调用阻塞；顺带把「trace 即 UI」的基础管道打通；
- 会话恢复：Agent 历史从 SessionManager 载入（load_history），多会话互不串线；
- SSE 协议事件：queued / step / tool_call / tool_result / token / card /
  error / done；说明：当前 ChatClient 为同步通道（无流式 SDK），token 事件按
  「每轮完整文本」发送（协议形态满足，逐 token 流式留待后续 streaming 通道）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from lighttrail.agent import Agent
from lighttrail.infra.trace import TraceEvent, TraceRecorder
from lighttrail.orchestrator import Orchestrator
from lighttrail.orchestrator.orchestrator import _render_card
from lighttrail.orchestrator.schemas import DecisionCard

logger = logging.getLogger("lighttrail.api.routes")

# ------ 请求模型（复用 schemas.py 契约，白拿 OpenAPI）------
class ChatRequest(BaseModel):
    """自由对话请求（ReAct）：message + 可选 session_id。"""

    message: str = Field(..., min_length=1, max_length=2000, description="用户消息文本")
    session_id: str = Field(default="", description="会话标识；缺省自动新建")


class DecideRequest(BaseModel):
    """决策管线请求（D1–D3）：一句话 + 可选 session_id。"""

    request: str = Field(..., min_length=1, max_length=2000, description="决策请求（一句话）")
    session_id: str = Field(default="", description="会话标识；缺省自动新建")


class ProfilePayload(BaseModel):
    """档案编辑（M1.1-01）：全部字段可选，PUT 只更新出现的字段。"""

    camera_body: str | None = None
    lenses: list[str] | None = None
    preferences: list[str] | None = None
    common_locations: list[str] | None = None
    skill_level: str | None = None
    favorite_spots: list[dict[str, Any]] | None = None


# ------ SSE 协议（§2.8）------
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
_MAX_PHOTO_BYTES = 10 * 1024 * 1024
_ALLOWED_PHOTO_TYPES = {"image/jpeg", "image/png"}


def _sse_text(event: dict[str, Any]) -> str:
    """把事件字典编码为 SSE 帧（event: type + data: JSON）。"""
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


def _map_trace_event(event: TraceEvent) -> list[dict[str, Any]] | None:
    """把 TraceEvent 映射为 SSE 协议事件（llm 事件不入协议，token 由端点补发）。"""
    if event.kind == "step":
        return [
            {
                "type": "step",
                "name": event.name,
                "input_summary": event.payload.get("输入摘要", ""),
                "output_summary": event.payload.get("输出摘要", ""),
                "ts": event.ts,
            }
        ]
    if event.kind == "tool":
        payload = event.payload
        return [
            {"type": "tool_call", "name": event.name, "arguments": payload.get("参数摘要", ""), "ts": event.ts},
            {
                "type": "tool_result",
                "name": event.name,
                "result": payload.get("结果摘要", ""),
                "data_source": payload.get("数据来源", ""),
                "confidence": payload.get("置信度", "low"),
                "field": payload.get("来源字段", ""),
                "elapsed_ms": payload.get("耗时_ms", 0.0),
                "ts": event.ts,
            },
        ]
    return None


def _report_to_dict(report: Any) -> dict[str, Any]:
    """把 TraceReport 转为 JSON 安全字典（会话回放用）。"""
    return {
        "llm_calls": [dict(item) for item in report.llm_calls],
        "tool_calls": [
            {
                "name": item.name,
                "arguments_summary": item.arguments_summary,
                "result_summary": item.result_summary,
                "data_source": item.data_source,
                "confidence": item.confidence,
                "field": item.field,
                "elapsed_ms": item.elapsed_ms,
            }
            for item in report.tool_calls
        ],
        "steps": [
            {"name": item.name, "input_summary": item.input_summary, "output_summary": item.output_summary}
            for item in report.steps
        ],
        "sources": [
            {"tool": item.tool, "field": item.field, "confidence": item.confidence, "data_source": item.data_source}
            for item in report.sources
        ],
    }


class _TraceSink:
    """TraceRecorder 订阅适配器：TraceEvent → SSE 事件，线程安全入队。

    回调在 worker 线程执行（同步 ReAct/管线），经 call_soon_threadsafe
    把事件投递回事件循环内的 asyncio.Queue——即 SSE 桥接的核心管道。
    """

    def __init__(self, queue: asyncio.Queue[dict[str, Any]], loop: asyncio.AbstractEventLoop) -> None:
        self._queue = queue
        self._loop = loop

    def __call__(self, event: TraceEvent) -> None:
        for mapped in _map_trace_event(event) or []:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, mapped)


def _queue_position(client: Any) -> int:
    """当前 LLM 队列排队位置（供 queued 事件）；无闸门/伪客户端时按 1 计。"""
    fn = getattr(client, "queue_position", None)
    if callable(fn):
        try:
            return int(fn()) + 1
        except Exception:  # noqa: BLE001 - 排队信息是体验增强，失败不阻塞
            return 1
    return 1


def _stream_response(stream: AsyncIterator[str]) -> StreamingResponse:
    """以 SSE 帧输出事件流。"""
    return StreamingResponse(stream, media_type="text/event-stream", headers=_SSE_HEADERS)


async def _pump(queue: asyncio.Queue[dict[str, Any] | None], worker: asyncio.Task) -> AsyncIterator[str]:
    """从队列取事件编码为 SSE 帧，直到 done；结束时取消未完成的 worker。"""
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            yield _sse_text(item)
            if item.get("type") == "done":
                break
    finally:
        if not worker.done():
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker


# ------ 路由实现 ------
router = APIRouter()


@router.post("/api/chat", summary="自由对话（ReAct），SSE 流式事件")
async def chat(payload: ChatRequest, request: Request) -> StreamingResponse:
    """接收消息 → queued → ReAct 过程事件 → token → done。"""
    deps = request.app.state.deps
    session = _load_or_create(deps, payload.session_id)
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    recorder = TraceRecorder()
    unsub = recorder.subscribe(_TraceSink(queue, loop))

    def run() -> tuple[Agent, str, Any]:
        agent = Agent(
            deps.client,
            deps.registry,
            model=deps.model,
            memory=deps.memory,
            reason_thinking=deps.reason_thinking,
            recorder=recorder,
        )
        agent.load_history(session.history)
        text, report = agent.run_with_trace(payload.message)
        return agent, text, report

    async def worker() -> None:
        try:
            agent, text, report = await asyncio.to_thread(run)
        except Exception as exc:
            logger.exception("会话 %s 对话失败", session.session_id)
            queue.put_nowait({"type": "error", "message": str(exc), "session_id": session.session_id})
            queue.put_nowait({"type": "done", "session_id": session.session_id, "text": ""})
            return
        session.history = agent.history
        session.workspace["last_trace"] = _report_to_dict(report)
        deps.sessions.save(session)
        queue.put_nowait({"type": "token", "content": text, "session_id": session.session_id})
        queue.put_nowait({"type": "done", "session_id": session.session_id, "text": text})

    async def stream() -> AsyncIterator[str]:
        queue.put_nowait({"type": "queued", "position": _queue_position(deps.client), "session_id": session.session_id})
        worker_task = asyncio.create_task(worker())
        try:
            async for frame in _pump(queue, worker_task):
                yield frame
        finally:
            unsub()

    return _stream_response(stream())


@router.post("/api/decide", summary="决策管线（D1–D3），SSE 事件直至决策卡片")
async def decide(payload: DecideRequest, request: Request) -> StreamingResponse:
    """一句话 → 管线步骤事件逐条推 → 末端 card + done。"""
    deps = request.app.state.deps
    session = _load_or_create(deps, payload.session_id)
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    recorder = TraceRecorder()
    unsub = recorder.subscribe(_TraceSink(queue, loop))

    def run() -> tuple[DecisionCard, str, Any]:
        agent = Agent(
            deps.client,
            deps.registry,
            model=deps.model,
            memory=deps.memory,
            reason_thinking=deps.reason_thinking,
            recorder=recorder,
        )
        orchestrator = Orchestrator(
            deps.client,
            deps.registry,
            agent,
            memory=deps.memory,
            recorder=recorder,
            dispatch=deps.dispatch,
            photo_analyze=deps.photo_analyze,
        )
        card = orchestrator.run_pipeline(payload.request)
        return card, _render_card(card), recorder.to_report()

    async def worker() -> None:
        try:
            card, text, report = await asyncio.to_thread(run)
        except Exception as exc:
            logger.exception("会话 %s 决策失败", session.session_id)
            queue.put_nowait({"type": "error", "message": str(exc), "session_id": session.session_id})
            queue.put_nowait({"type": "done", "session_id": session.session_id, "text": ""})
            return
        session.history.append({"role": "user", "content": payload.request})
        session.history.append({"role": "assistant", "content": text})
        session.workspace["last_card"] = card.model_dump(mode="json")
        session.workspace["last_trace"] = _report_to_dict(report)
        deps.sessions.save(session)
        queue.put_nowait(
            {"type": "card", "card": card.model_dump(mode="json"), "text": text, "session_id": session.session_id}
        )
        queue.put_nowait({"type": "done", "session_id": session.session_id, "text": text})

    async def stream() -> AsyncIterator[str]:
        queue.put_nowait({"type": "queued", "position": _queue_position(deps.client), "session_id": session.session_id})
        worker_task = asyncio.create_task(worker())
        try:
            async for frame in _pump(queue, worker_task):
                yield frame
        finally:
            unsub()

    return _stream_response(stream())


@router.post("/api/photos/review", summary="照片复盘（D4），上传图片 → SSE 事件至复盘卡片")
async def photos_review(
    request: Request,
    file: UploadFile = File(..., description="照片（jpg/png，≤10MB）"),  # noqa: B008 - FastAPI 依赖注入惯例
    focus: str = Form(default="", description="分析重点，如「构图」"),
    plan_reference: str = Form(default="", description="历史计划（DecisionCard JSON 或文本，可空）"),
    session_id: str = Form(default="", description="会话标识；缺省自动新建"),
) -> StreamingResponse:
    """上传照片 → 临时文件 → analyze_photo（多模态）→ 复盘卡 → done。"""
    deps = request.app.state.deps
    if file.content_type not in _ALLOWED_PHOTO_TYPES:
        raise HTTPException(status_code=400, detail="仅支持 jpg/png 照片")

    content = await file.read()
    if len(content) > _MAX_PHOTO_BYTES:
        raise HTTPException(status_code=413, detail="照片超过 10MB 上限")
    if not content:
        raise HTTPException(status_code=400, detail="照片内容为空")

    suffix = Path(file.filename or "").suffix.lower() or (".png" if file.content_type == "image/png" else ".jpg")
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        return await _review_stream(deps, tmp_path, focus, plan_reference, session_id)
    finally:
        if tmp_path is not None:
            Path(tmp_path).unlink(missing_ok=True)


async def _review_stream(
    deps: Any, image_path: str, focus: str, plan_reference: str, session_id: str
) -> StreamingResponse:
    """复盘管线 SSE 流（上传文件已落临时盘，这里只管事件流）。"""
    session = _load_or_create(deps, session_id)
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    recorder = TraceRecorder()
    unsub = recorder.subscribe(_TraceSink(queue, loop))

    def run() -> tuple[DecisionCard, str, Any]:
        agent = Agent(
            deps.client,
            deps.registry,
            model=deps.model,
            memory=deps.memory,
            reason_thinking=deps.reason_thinking,
            recorder=recorder,
        )
        orchestrator = Orchestrator(
            deps.client,
            deps.registry,
            agent,
            memory=deps.memory,
            recorder=recorder,
            dispatch=deps.dispatch,
            photo_analyze=deps.photo_analyze,
        )
        card = orchestrator.run_review(image_path, focus=focus, plan_reference=plan_reference)
        return card, _render_card(card), recorder.to_report()

    async def worker() -> None:
        try:
            card, text, report = await asyncio.to_thread(run)
        except Exception as exc:
            logger.exception("会话 %s 复盘失败", session.session_id)
            queue.put_nowait({"type": "error", "message": str(exc), "session_id": session.session_id})
            queue.put_nowait({"type": "done", "session_id": session.session_id, "text": ""})
            return
        session.history.append({"role": "user", "content": f"复盘照片：{image_path}（{focus}）"})
        session.history.append({"role": "assistant", "content": text})
        session.workspace["last_card"] = card.model_dump(mode="json")
        session.workspace["last_trace"] = _report_to_dict(report)
        deps.sessions.save(session)
        queue.put_nowait(
            {"type": "card", "card": card.model_dump(mode="json"), "text": text, "session_id": session.session_id}
        )
        queue.put_nowait({"type": "done", "session_id": session.session_id, "text": text})

    async def stream() -> AsyncIterator[str]:
        queue.put_nowait({"type": "queued", "position": _queue_position(deps.client), "session_id": session.session_id})
        worker_task = asyncio.create_task(worker())
        try:
            async for frame in _pump(queue, worker_task):
                yield frame
        finally:
            unsub()

    return _stream_response(stream())


@router.get("/api/profile", summary="读取用户档案（M1.1-01）")
async def get_profile(request: Request) -> dict[str, Any]:
    """返回当前档案（camera_body/lenses/preferences/…）。"""
    return asdict(request.app.state.deps.memory.profile)


@router.put("/api/profile", summary="更新用户档案（M1.1-01，显式写入）")
async def put_profile(payload: ProfilePayload, request: Request) -> dict[str, Any]:
    """部分更新档案：只更新出现字段，未知字段忽略。"""
    deps = request.app.state.deps
    fields = payload.model_dump(exclude_none=True)
    deps.memory.update_profile(fields)
    return asdict(deps.memory.profile)


@router.get("/api/sessions/{session_id}", summary="会话历史与 TraceReport 回放")
async def get_session(session_id: str, request: Request) -> dict[str, Any]:
    """返回会话记录（历史 + 工作区）与最近一次运行的 TraceReport。"""
    deps = request.app.state.deps
    session = deps.sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"session": session.to_dict(), "trace": session.workspace.get("last_trace")}


# ------ 内部实现 ------
def _load_or_create(deps: Any, session_id: str):
    """会话恢复或新建：提供 session_id 且存在则载入，否则新建。"""
    if session_id:
        existing = deps.sessions.get(session_id)
        if existing is not None:
            return existing
    return deps.sessions.create()


__all__ = ["ChatRequest", "DecideRequest", "ProfilePayload", "_sse_text", "router"]
