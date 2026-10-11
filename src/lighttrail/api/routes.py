"""FastAPI SSE、用户档案与收藏机位路由（架构 v2.0 §2.8）。

设计要点：
- 薄服务层：只做协议转换与会话管理，业务逻辑在 Agent/Orchestrator（§1 边界原则）；
- 线程模型（§2.9）：同步 ReAct/管线在 worker 线程执行（asyncio.to_thread），
  TraceBridge（api/events.py）把 trace 事件经 asyncio.Queue 桥接回事件循环
  （call_soon_threadsafe）——「trace 即 UI」：排队 → 步骤 → 工具 → 卡片 实时推送；
- 会话恢复：Agent 历史从 SessionManager 载入（load_history），多会话互不串线；
- 说明：当前 ChatClient 为同步通道（无流式 SDK），token 事件按「每轮完整文本」
  发送（协议形态满足，逐 token 流式留待后续 streaming 通道）。
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from collections.abc import AsyncIterator
from dataclasses import asdict, replace
from datetime import datetime, timezone
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from lighttrail.adapters.llm.pydantic_bridge import (
    from_openai_history,
    to_openai_history,
)
from lighttrail.adapters.maps.js_service import (
    MapProxyError,
    convert_wgs84_points,
    forward_js_service,
)
from lighttrail.api.events import (
    TraceBridge,
    pump,
    to_payload,
)
from lighttrail.api.session import SavedResult, SessionRecord, snapshot_context
from lighttrail.application.plan_archive import archive_spot_plan
from lighttrail.application.spot_planner import SpotPlanInputError, build_spot_plan
from lighttrail.composition import RequestScope, build_recorder
from lighttrail.contracts.context import LOCAL_USER_ID, RequestContext
from lighttrail.contracts.events import (
    CardEvent,
    DoneEvent,
    ErrorEvent,
    QueuedEvent,
    SSEEventPayload,
    TokenEvent,
)
from lighttrail.contracts.favorite_spot import FavoriteSpotInput, FavoriteSpotRecord
from lighttrail.contracts.models import DecisionCard
from lighttrail.contracts.plan_archive import PlanArchive
from lighttrail.contracts.spot_plan import PlanningRequest, SpotPlan
from lighttrail.domain.chase import ChaseSite, GeoPoint
from lighttrail.infra.trace import TraceRecorder
from lighttrail.memory.manager import FavoriteSpotNotFoundError
from lighttrail.orchestrator.orchestrator import _render_card

logger = logging.getLogger("lighttrail.api.routes")


# ------ 请求模型（复用 schemas.py 契约，白拿 OpenAPI）------
class ChatRequest(BaseModel):
    """自由对话请求（ReAct）：message + 可选 session_id。"""

    message: str = Field(..., min_length=1, max_length=2000, description="用户消息文本")
    session_id: str = Field(default="", description="会话标识；缺省自动新建")
    result_id: str = ""
    requery: bool = False


class DecideRequest(BaseModel):
    """决策管线请求（D1–D3）：一句话 + 可选 session_id。"""

    request: str = Field(..., min_length=1, max_length=2000, description="决策请求（一句话）")
    session_id: str = Field(default="", description="会话标识；缺省自动新建")
    result_id: str = ""
    requery: bool = False


class SavedPlanRequest(BaseModel):
    """复用现有计划请求，附加可选的会话及父版本关联。"""

    request: PlanningRequest
    session_id: str = ""
    result_id: str = ""


class ProfilePayload(BaseModel):
    """档案编辑（M1.1-01）：机位列表只读，其他字段可经 PUT 更新。"""

    camera_body: str | None = None
    lenses: list[str] | None = None
    preferences: list[str] | None = None
    common_locations: list[str] | None = None
    skill_level: str | None = None
    favorite_spots: list[dict[str, Any]] | None = None


class SkippedFavoriteSpotPayload(BaseModel):
    """旧档案中未能投影的条目序号及原因。"""

    index: int
    reason: Literal["policy", "invalid"]


class FavoriteSpotListPayload(BaseModel):
    """机位列表及无法投影的旧档案条目索引。"""

    items: list[FavoriteSpotRecord]
    skipped: list[SkippedFavoriteSpotPayload]


class MapConfigPayload(BaseModel):
    """地图手动加载配置；仅公开 JS API Web Key，不返回安全密钥。"""

    available: bool
    js_key: str | None


class MapConvertPayload(BaseModel):
    """用户主动打开地图后提交的当前用户收藏机位 ID。"""

    spot_ids: list[str] = Field(..., min_length=1, max_length=40)

    @field_validator("spot_ids")
    @classmethod
    def _unique_ids(cls, values: list[str]) -> list[str]:
        """拒绝空白或重复 ID，避免无意义的第三方转换调用。"""
        if any(not value or value.strip() != value for value in values):
            raise ValueError("机位 ID 不能为空或带空白")
        if len(set(values)) != len(values):
            raise ValueError("机位 ID 不得重复")
        return values


class MapConvertedPoint(BaseModel):
    """高德地图标记使用的 GCJ-02 经度、纬度。"""

    longitude: float
    latitude: float


class MapConvertResult(BaseModel):
    """按请求顺序返回转换结果，不持久化用户精确坐标。"""

    positions: list[MapConvertedPoint]


def _require_local_map_request(request: Request) -> None:
    """地图试接线仅供本机使用，阻止跨站页面借本地代理消耗账户额度。"""
    try:
        local_client = request.client is not None and ip_address(request.client.host).is_loopback
    except ValueError:
        local_client = False
    if not local_client or request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(status_code=403, detail="地图代理仅支持本地同站点访问")
    for header in ("origin", "referer"):
        value = request.headers.get(header)
        if value and urlsplit(value).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise HTTPException(status_code=403, detail="地图代理仅支持本地同站点访问")


class EventCreatePayload(BaseModel):
    """用户确认后写入的拍摄事件；服务端不从复盘结果自动推断或落库。"""

    location: str = Field(..., min_length=1, max_length=120, description="拍摄地点名称")
    subject_type: str = Field(default="", max_length=40, description="题材，如火烧云或银河")
    summary: str = Field(default="", max_length=1000, description="用户确认的复盘摘要")
    outcome: str = Field(
        default="", pattern="^(|success|fail)$", description="结果：success、fail 或空"
    )
    timestamp: datetime | None = Field(
        default=None, description="带时区的 ISO 8601 拍摄时间；空则以确认时刻写入"
    )

    @field_validator("timestamp")
    @classmethod
    def _timestamp_requires_timezone(cls, value: datetime | None) -> datetime | None:
        """事件时间必须带时区，避免写入含义不明的本地时间。"""
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamp 必须是包含时区的 ISO 8601 时间")
        return value


class EventCreatedPayload(BaseModel):
    """已确认写入的事件标识与规范时间。"""

    event_id: int
    timestamp: datetime


class DeletedSpotPayload(BaseModel):
    """已删除收藏机位的标识。"""

    deleted_id: str


class ChaseSitePayload(BaseModel):
    """赶场规划中一个已选机位的拍摄窗口。"""

    name: str = Field(..., min_length=1, max_length=120, description="档案中已保存的机位名称")
    window_start: datetime = Field(..., description="拍摄窗口开始，ISO 8601 且必须含时区")
    window_end: datetime = Field(..., description="拍摄窗口结束，ISO 8601 且必须含时区")


class ChasePlanRequest(BaseModel):
    """多机位赶场规划请求（C1-1）。"""

    sites: list[ChaseSitePayload] = Field(
        ..., min_length=2, max_length=4, description="2 到 4 个机位"
    )
    transport_mode: Literal["driving", "cycling", "walking", "transit"] = Field(
        default="driving", description="交通方式：驾车、骑行、步行或公共交通"
    )
    depart_at: datetime = Field(..., description="首次出发时间，ISO 8601 且必须含时区")
    transition_buffer_minutes: int = Field(
        default=18, ge=0, le=180, description="每次换机位附加缓冲分钟数"
    )
    min_shoot_minutes: int = Field(
        default=15, ge=0, le=240, description="每个机位最少有效拍摄分钟数"
    )
    transit_city: str = Field(default="", max_length=80, description="公共交通路线所需城市名称")


class ChaseRoutePayload(BaseModel):
    """一段赶场路线的来源、耗时和降级信息。"""

    distance_m: float | None
    duration_minutes: float
    transport_mode: str
    traffic_level: str
    traffic_summary: str
    provider: str
    confidence: str
    queried_at: datetime
    is_future_prediction: bool
    degraded_reason: str


class ChaseLegPayload(BaseModel):
    """一个机位窗口对应的行程段。"""

    from_site: str
    to_site: str
    leave_at: datetime
    arrive_at: datetime
    latest_leave_at: datetime
    effective_shooting_minutes: int
    margin_minutes: int
    transition_buffer_minutes: int
    route: ChaseRoutePayload


class ChaseRejectedOptionPayload(BaseModel):
    """被舍弃的机位顺序及原因。"""

    ordered_site_names: list[str]
    reason: str


class ChasePlanPayload(BaseModel):
    """多机位赶场规划的完整响应契约。"""

    ordered_site_names: list[str]
    feasible: bool
    risk_level: str
    total_effective_shooting_minutes: int
    explanation: str
    legs: list[ChaseLegPayload]
    rejected_options: list[ChaseRejectedOptionPayload]


class SessionMessagePayload(BaseModel):
    """会话历史中一条 OpenAI 兼容消息。"""

    role: str
    content: str | list[dict[str, Any]] | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class SessionPayload(BaseModel):
    """会话详情中的可持久化记录字段。"""

    session_id: str
    history: list[SessionMessagePayload]
    pipeline: dict[str, Any]
    workspace: dict[str, Any]
    user_id: str
    created_at: str
    updated_at: str
    results: list[SavedResult] = Field(default_factory=list)


class SessionResponsePayload(BaseModel):
    """会话记录与最近一次 trace 回放。"""

    session: SessionPayload
    trace: dict[str, Any] | None


# ------ SSE 常量（事件类型定义见 api/events.py）------
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


class SSEResponse(StreamingResponse):
    """SSE 流式响应类：只为 OpenAPI 标注 text/event-stream（运行期仍由本模块自建响应）。"""

    media_type = "text/event-stream"


# SSE 端点 200 响应契约：事件负载模型进 components，前端类型经 gen:api 生成（B4-1）
_SSE_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "model": SSEEventPayload,
        "description": "SSE 事件流（event: <type> + data: JSON）",
    },
}

# 照片上传护栏（D4 复盘端点）
_MAX_PHOTO_BYTES = 10 * 1024 * 1024
_PHOTO_READ_CHUNK_BYTES = 1024 * 1024
_ALLOWED_PHOTO_TYPES = {"image/jpeg", "image/png"}


async def _read_photo_upload(file: UploadFile) -> bytes:
    """在上传内容进入内存前限制照片大小。

    Args:
        file: FastAPI 接收的照片上传对象。

    Returns:
        不超过 10 MB 的照片内容。

    Raises:
        HTTPException: 文件超过大小上限。
    """
    if file.size is not None and file.size > _MAX_PHOTO_BYTES:
        raise HTTPException(status_code=413, detail="照片超过 10MB 上限")

    content = bytearray()
    while True:
        remaining = _MAX_PHOTO_BYTES - len(content)
        chunk = await file.read(min(_PHOTO_READ_CHUNK_BYTES, remaining + 1))
        if not chunk:
            return bytes(content)
        content.extend(chunk)
        if len(content) > _MAX_PHOTO_BYTES:
            raise HTTPException(status_code=413, detail="照片超过 10MB 上限")


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
            {
                "name": item.name,
                "input_summary": item.input_summary,
                "output_summary": item.output_summary,
            }
            for item in report.steps
        ],
        "sources": [
            {
                "tool": item.tool,
                "field": item.field,
                "confidence": item.confidence,
                "data_source": item.data_source,
            }
            for item in report.sources
        ],
    }


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


# ------ 路由实现 ------
router = APIRouter()


class DataBoundaryInfo(BaseModel):
    """不含凭据的发送接收方说明。"""

    llm_receiver: str


@router.get("/api/data-boundaries", response_model=DataBoundaryInfo)
async def data_boundaries(request: Request) -> DataBoundaryInfo:
    """返回配置模型服务的主机名，不返回路径、查询参数或认证信息。"""
    host = urlsplit(request.app.state.deps.services.settings.base_url).hostname
    return DataBoundaryInfo(llm_receiver=host or "配置的模型 API（主机名不可解析）")


def get_request_context() -> RequestContext:
    """返回本地单用户身份；未来认证可替换此依赖，不从请求体接受用户身份。"""
    return RequestContext()


def _run_setup(
    deps: Any,
    recorder: TraceRecorder,
    context: RequestContext,
    session: SessionRecord,
    *,
    authorized_photo_paths: tuple[str, ...] = (),
) -> RequestScope:
    """将入口身份与已解析会话交给统一装配根，不在路由内构造领域依赖。"""
    return deps.services.create_scope(
        replace(context, session_id=session.session_id),
        recorder=recorder,
        authorized_photo_paths=authorized_photo_paths,
    )


@router.post(
    "/api/chat",
    summary="自由对话（ReAct），SSE 流式事件",
    responses=_SSE_RESPONSES,
    response_class=SSEResponse,
)
async def chat(
    payload: ChatRequest,
    request: Request,
    context: RequestContext = Depends(get_request_context),  # noqa: B008
) -> StreamingResponse:
    """接收消息 → queued → ReAct 过程事件（工具）→ token → done。"""
    deps = request.app.state.deps
    session = _load_or_create(deps, payload.session_id, context)
    _select_history(session, payload.result_id)
    if payload.requery:
        session.history = []
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    recorder = build_recorder()
    bridge = TraceBridge(recorder)
    bridge.attach(queue, loop, session_id=session.session_id)

    def run() -> tuple[list[dict[str, Any]], str, Any]:
        runtime = _run_setup(deps, recorder, context, session).runtime
        runtime.load_messages(from_openai_history(session.history))
        text, report = runtime.run_with_trace(payload.message)
        return to_openai_history(runtime.messages), text, report

    async def worker() -> None:
        done_text = ""
        saved_result: SavedResult | None = None
        try:
            history, text, report = await asyncio.to_thread(run)
            bridge.flush()
            session.history = history
            session.workspace["last_trace"] = _report_to_dict(report)
            saved_result = deps.sessions.save_result(
                session,
                kind="chat",
                request={"message": payload.message},
                text=text,
                parent_result_id=payload.result_id,
            )
            queue.put_nowait(to_payload(TokenEvent(content=text, session_id=session.session_id)))
            done_text = text
        except Exception as exc:
            logger.exception("会话 %s 对话失败", session.session_id)
            queue.put_nowait(
                to_payload(ErrorEvent(message=str(exc), session_id=session.session_id))
            )
        finally:
            bridge.flush()
            queue.put_nowait(
                to_payload(
                    DoneEvent(
                        text=done_text,
                        session_id=session.session_id,
                        saved=saved_result is not None,
                        result_id=saved_result.result_id if saved_result else None,
                    )
                )
            )

    async def stream() -> AsyncIterator[str]:
        queue.put_nowait(
            to_payload(
                QueuedEvent(
                    position=_queue_position(deps.services.client), session_id=session.session_id
                )
            )
        )
        worker_task = asyncio.create_task(worker())
        try:
            async for frame in pump(queue, worker_task):
                yield frame
        finally:
            bridge.detach()

    return _stream_response(stream())


@router.post(
    "/api/decide",
    summary="拍摄决策（D1–D3），SSE 返回卡片或文本边界",
    responses=_SSE_RESPONSES,
    response_class=SSEResponse,
)
async def decide(
    payload: DecideRequest,
    request: Request,
    context: RequestContext = Depends(get_request_context),  # noqa: B008
) -> StreamingResponse:
    """一句话经统一规划入口，推送步骤及卡片或明确的文本边界。"""
    deps = request.app.state.deps
    session = _load_or_create(deps, payload.session_id, context)
    _select_history(session, payload.result_id)
    if payload.requery:
        session.history = []
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    recorder = build_recorder()
    bridge = TraceBridge(recorder)
    bridge.attach(queue, loop, session_id=session.session_id)

    def run() -> tuple[DecisionCard | None, str, Any]:
        orchestrator = _run_setup(deps, recorder, context, session).orchestrator
        orchestrator.load_history(session.history)
        text = orchestrator.plan(payload.request)
        session.pipeline = snapshot_context(orchestrator.last_context)
        return orchestrator.last_card, text, recorder.to_report()

    async def worker() -> None:
        done_text = ""
        saved_result: SavedResult | None = None
        try:
            card, text, report = await asyncio.to_thread(run)
            bridge.flush()
            session.history.append({"role": "user", "content": payload.request})
            session.history.append({"role": "assistant", "content": text})
            session.workspace["last_card"] = card.model_dump(mode="json") if card else None
            session.workspace["last_trace"] = _report_to_dict(report)
            saved_result = deps.sessions.save_result(
                session,
                kind="decision",
                request={"request": payload.request},
                text=text,
                card=card,
                parent_result_id=payload.result_id,
            )
            if card is not None:
                queue.put_nowait(
                    to_payload(CardEvent(card=card, text=text, session_id=session.session_id))
                )
            else:
                queue.put_nowait(
                    to_payload(TokenEvent(content=text, session_id=session.session_id))
                )
            done_text = text
        except Exception as exc:
            logger.exception("会话 %s 决策失败", session.session_id)
            queue.put_nowait(
                to_payload(ErrorEvent(message=str(exc), session_id=session.session_id))
            )
        finally:
            bridge.flush()
            queue.put_nowait(
                to_payload(
                    DoneEvent(
                        text=done_text,
                        session_id=session.session_id,
                        saved=saved_result is not None,
                        result_id=saved_result.result_id if saved_result else None,
                    )
                )
            )

    async def stream() -> AsyncIterator[str]:
        queue.put_nowait(
            to_payload(
                QueuedEvent(
                    position=_queue_position(deps.services.client), session_id=session.session_id
                )
            )
        )
        worker_task = asyncio.create_task(worker())
        try:
            async for frame in pump(queue, worker_task):
                yield frame
        finally:
            bridge.detach()

    return _stream_response(stream())


@router.post(
    "/api/photos/review",
    summary="照片复盘（D4），上传图片 → SSE 事件至复盘卡片",
    responses=_SSE_RESPONSES,
    response_class=SSEResponse,
)
async def photos_review(
    request: Request,
    context: RequestContext = Depends(get_request_context),  # noqa: B008
    file: UploadFile = File(..., description="照片（jpg/png，≤10MB）"),  # noqa: B008 - FastAPI 依赖注入惯例
    focus: str = Form(default="", description="分析重点，如「构图」"),
    plan_reference: str = Form(
        default="", description="历史计划（DecisionCard JSON 或文本，可空）"
    ),
    session_id: str = Form(default="", description="会话标识；缺省自动新建"),
) -> StreamingResponse:
    """上传照片 → 复盘管线（多模态分析）→ 复盘卡 → done。

    临时文件由流内 worker 线程自建自销（**不能**在端点里建删）：SSE 流是惰性的，
    端点在返回响应时流还没被消费，若那时删文件，worker 读图必 FileNotFoundError
    （D4 真实联调实测「图片读取失败」，见 tests/test_api.py 的竞态回归用例）。
    """
    deps = request.app.state.deps
    if file.content_type not in _ALLOWED_PHOTO_TYPES:
        raise HTTPException(status_code=400, detail="仅支持 jpg/png 照片")

    content = await _read_photo_upload(file)
    if not content:
        raise HTTPException(status_code=400, detail="照片内容为空")

    suffix = ".png" if file.content_type == "image/png" else ".jpg"
    display_name = Path(file.filename or "").name or f"photo{suffix}"
    return await _review_stream(
        deps, content, suffix, display_name, focus, plan_reference, session_id, context
    )


@router.post(
    "/api/photos/reverse",
    summary="参考图反推（D1.2），上传图片 → SSE 事件至复刻计划卡片",
    responses=_SSE_RESPONSES,
    response_class=SSEResponse,
)
async def photos_reverse(
    request: Request,
    context: RequestContext = Depends(get_request_context),  # noqa: B008
    file: UploadFile = File(..., description="参考图（jpg/png，≤10MB）"),  # noqa: B008
    note: str = Form(default="", description="补充约束，如「只能周末去」"),
    equipment: str = Form(default="", description="器材覆盖，如「14mm f/2.8」"),
    session_id: str = Form(default="", description="会话标识；缺省自动新建"),
) -> StreamingResponse:
    """上传参考图 → 反推管线 → 复刻计划卡片 → done。"""
    deps = request.app.state.deps
    if file.content_type not in _ALLOWED_PHOTO_TYPES:
        raise HTTPException(status_code=400, detail="仅支持 jpg/png 照片")
    content = await _read_photo_upload(file)
    if not content:
        raise HTTPException(status_code=400, detail="照片内容为空")
    suffix = ".png" if file.content_type == "image/png" else ".jpg"
    display_name = Path(file.filename or "").name or f"reference{suffix}"
    return await _reverse_stream(
        deps, content, suffix, display_name, note, equipment, session_id, context
    )


async def _review_stream(
    deps: Any,
    content: bytes,
    suffix: str,
    display_name: str,
    focus: str,
    plan_reference: str,
    session_id: str,
    context: RequestContext,
) -> StreamingResponse:
    """复盘管线 SSE 流：临时文件在 worker 线程内建销（与图片读取同生命周期，杜绝竞态）。"""
    session = _load_or_create(deps, session_id, context)
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    recorder = build_recorder()
    bridge = TraceBridge(recorder)
    bridge.attach(queue, loop, session_id=session.session_id)

    def run() -> tuple[DecisionCard, str, Any]:
        """worker 线程内落临时文件 → 跑复盘管线 → 无论如何都清理（同线程，无竞态）。"""
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            image_path = tmp.name
        try:
            orchestrator = _run_setup(
                deps, recorder, context, session, authorized_photo_paths=(image_path,)
            ).orchestrator
            card = orchestrator.run_review(image_path, focus=focus, plan_reference=plan_reference)
            return card, _render_card(card), recorder.to_report()
        finally:
            Path(image_path).unlink(missing_ok=True)

    async def worker() -> None:
        done_text = ""
        try:
            card, text, report = await asyncio.to_thread(run)
            bridge.flush()
            session.history.append(
                {"role": "user", "content": f"复盘照片：{display_name}（{focus}）"}
            )
            session.history.append({"role": "assistant", "content": text})
            session.workspace["last_card"] = card.model_dump(mode="json")
            session.workspace["last_trace"] = _report_to_dict(report)
            deps.sessions.save(session)
            queue.put_nowait(
                to_payload(CardEvent(card=card, text=text, session_id=session.session_id))
            )
            done_text = text
        except Exception as exc:
            logger.exception("会话 %s 复盘失败", session.session_id)
            queue.put_nowait(
                to_payload(ErrorEvent(message=str(exc), session_id=session.session_id))
            )
        finally:
            bridge.flush()
            queue.put_nowait(to_payload(DoneEvent(text=done_text, session_id=session.session_id)))

    async def stream() -> AsyncIterator[str]:
        queue.put_nowait(
            to_payload(
                QueuedEvent(
                    position=_queue_position(deps.services.client), session_id=session.session_id
                )
            )
        )
        worker_task = asyncio.create_task(worker())
        try:
            async for frame in pump(queue, worker_task):
                yield frame
        finally:
            bridge.detach()

    return _stream_response(stream())


async def _reverse_stream(
    deps: Any,
    content: bytes,
    suffix: str,
    display_name: str,
    note: str,
    equipment: str,
    session_id: str,
    context: RequestContext,
) -> StreamingResponse:
    """参考图反推 SSE 流：临时文件在 worker 生命周期内创建和销毁。"""
    session = _load_or_create(deps, session_id, context)
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    recorder = build_recorder()
    bridge = TraceBridge(recorder)
    bridge.attach(queue, loop, session_id=session.session_id)

    def run() -> tuple[DecisionCard, str, Any]:
        """运行反推并保证临时文件不泄漏。"""
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            image_path = tmp.name
        try:
            orchestrator = _run_setup(
                deps, recorder, context, session, authorized_photo_paths=(image_path,)
            ).orchestrator
            card = orchestrator.run_reverse(image_path, note=note, equipment=equipment)
            return card, _render_card(card), recorder.to_report()
        finally:
            Path(image_path).unlink(missing_ok=True)

    async def worker() -> None:
        done_text = ""
        try:
            card, text, report = await asyncio.to_thread(run)
            bridge.flush()
            session.history.append(
                {"role": "user", "content": f"参考图反推：{display_name}（{note}）"}
            )
            session.history.append({"role": "assistant", "content": text})
            session.workspace["last_card"] = card.model_dump(mode="json")
            session.workspace["last_trace"] = _report_to_dict(report)
            deps.sessions.save(session)
            queue.put_nowait(
                to_payload(CardEvent(card=card, text=text, session_id=session.session_id))
            )
            done_text = text
        except Exception as exc:
            logger.exception("会话 %s 参考图反推失败", session.session_id)
            queue.put_nowait(
                to_payload(ErrorEvent(message=str(exc), session_id=session.session_id))
            )
        finally:
            bridge.flush()
            queue.put_nowait(to_payload(DoneEvent(text=done_text, session_id=session.session_id)))

    async def stream() -> AsyncIterator[str]:
        queue.put_nowait(
            to_payload(
                QueuedEvent(
                    position=_queue_position(deps.services.client), session_id=session.session_id
                )
            )
        )
        worker_task = asyncio.create_task(worker())
        try:
            async for frame in pump(queue, worker_task):
                yield frame
        finally:
            bridge.detach()

    return _stream_response(stream())


@router.get("/api/profile", summary="读取用户档案（M1.1-01）", response_model=ProfilePayload)
async def get_profile(
    request: Request,
    context: RequestContext = Depends(get_request_context),  # noqa: B008
) -> dict[str, Any]:
    """返回当前档案（camera_body/lenses/preferences/…）。"""
    return asdict(request.app.state.deps.services.memory.profile_for(context))


@router.put(
    "/api/profile", summary="更新用户档案（M1.1-01，显式写入）", response_model=ProfilePayload
)
async def put_profile(
    payload: ProfilePayload,
    request: Request,
    context: RequestContext = Depends(get_request_context),  # noqa: B008
) -> dict[str, Any]:
    """部分更新档案：只更新出现字段，未知字段忽略。"""
    if "favorite_spots" in payload.model_fields_set:
        raise HTTPException(status_code=422, detail="请通过 /api/favorite-spots 管理收藏机位")
    deps = request.app.state.deps
    fields = payload.model_dump(exclude_none=True)
    deps.services.memory.update_profile(context, fields)
    return asdict(deps.services.memory.profile_for(context))


@router.get(
    "/api/favorite-spots", summary="列出当前用户收藏机位", response_model=FavoriteSpotListPayload
)
async def list_favorite_spots(
    request: Request,
    context: RequestContext = Depends(get_request_context),  # noqa: B008
) -> dict[str, Any]:
    """只读列出机位；旧档案未知坐标系或非法记录不会被视为可计算。

    Args:
        request: 当前 HTTP 请求。
        context: 当前用户上下文。

    Returns:
        机位列表及无法投影的旧条目索引。
    """
    preview = request.app.state.deps.services.memory.list_favorite_spots(context)
    return {
        "items": list(preview.records),
        "skipped": [{"index": item.index, "reason": item.reason} for item in preview.skipped],
    }


@router.post(
    "/api/favorite-spots",
    summary="新增收藏机位",
    response_model=FavoriteSpotRecord,
    status_code=201,
)
async def create_favorite_spot(
    payload: FavoriteSpotInput,
    request: Request,
    context: RequestContext = Depends(get_request_context),  # noqa: B008
) -> FavoriteSpotRecord:
    """保存用户明确提交的新机位和坐标来源。

    Args:
        payload: 新机位输入。
        request: 当前 HTTP 请求。
        context: 当前用户上下文。

    Returns:
        已保存的机位记录。
    """
    return request.app.state.deps.services.memory.create_favorite_spot(context, payload)


@router.put(
    "/api/favorite-spots/{spot_id}", summary="确认或更新收藏机位", response_model=FavoriteSpotRecord
)
async def update_favorite_spot(
    spot_id: str,
    payload: FavoriteSpotInput,
    request: Request,
    context: RequestContext = Depends(get_request_context),  # noqa: B008
) -> FavoriteSpotRecord:
    """完整更新机位；旧记录须由用户显式确认坐标系。

    Args:
        spot_id: 机位稳定 ID。
        payload: 完整替换输入。
        request: 当前 HTTP 请求。
        context: 当前用户上下文。

    Returns:
        更新后的机位记录。

    Raises:
        HTTPException: 当前用户没有该机位。
    """
    try:
        return request.app.state.deps.services.memory.update_favorite_spot(
            context, spot_id, payload
        )
    except FavoriteSpotNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete(
    "/api/favorite-spots/{spot_id}", summary="删除收藏机位", response_model=DeletedSpotPayload
)
async def delete_favorite_spot(
    spot_id: str,
    request: Request,
    context: RequestContext = Depends(get_request_context),  # noqa: B008
) -> DeletedSpotPayload:
    """删除用户明确指定的一条机位；旧档案先备份。

    Args:
        spot_id: 机位稳定 ID。
        request: 当前 HTTP 请求。
        context: 当前用户上下文。

    Returns:
        已删除的机位 ID。

    Raises:
        HTTPException: 当前用户没有该机位。
    """
    try:
        request.app.state.deps.services.memory.delete_favorite_spot(context, spot_id)
    except FavoriteSpotNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return DeletedSpotPayload(deleted_id=spot_id)


@router.post("/api/spot-plans", summary="按已确认机位生成结构化单机位计划", response_model=SpotPlan)
async def create_spot_plan(
    payload: PlanningRequest,
    request: Request,
    response: Response,
    context: RequestContext = Depends(get_request_context),  # noqa: B008
) -> SpotPlan:
    """只按当前用户机位 ID 解析位置，保留缺事实时的 needs_data 状态。

    Args:
        payload: 本轮明确选择的日期、题材、时区与机位。
        request: 当前 HTTP 请求。
        response: 用于禁用坐标响应缓存。
        context: 当前用户上下文。

    Returns:
        可执行的太阳题材计划，或含明确数据缺口的部分计划。

    Raises:
        HTTPException: 机位 ID、名称或时区与当前用户档案不一致。
    """
    response.headers["Cache-Control"] = "private, no-store"
    services = request.app.state.deps.services
    favorites = services.memory.list_favorite_spots(context).records
    camera_body = services.memory.profile_for(context).camera_body
    scope = services.create_scope(context)

    def dispatch(name: str, args: str) -> str:
        """让新计划和现有管线使用同一请求工具注册表及离线替身。"""
        if services.dispatch is not None:
            result = services.dispatch(name, args)
            scope.recorder.record_tool(name, args, result)
            return result
        return services.registry.dispatch(name, args, scope.tool_context, recorder=scope.recorder)

    try:
        return await asyncio.to_thread(
            build_spot_plan, payload, favorites, dispatch, camera_body=camera_body
        )
    except SpotPlanInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/api/spot-plans/archive", response_model=PlanArchive, summary="导出脱敏的冻结机位计划"
)
async def export_spot_plan_archive(plan: SpotPlan, response: Response) -> PlanArchive:
    """从已验证计划移除经纬度，保留本轮请求和行动证据。"""
    response.headers["Cache-Control"] = "private, no-store"
    return archive_spot_plan(plan)


@router.post("/api/spot-plans/replay", response_model=PlanArchive, summary="校验并回放冻结机位计划")
async def replay_spot_plan_archive(archive: PlanArchive, response: Response) -> PlanArchive:
    """只校验并返回快照，不重新访问用户档案或外部数据源。"""
    response.headers["Cache-Control"] = "private, no-store"
    return archive


@router.get(
    "/api/map/config", summary="读取手动加载地图所需的公开配置", response_model=MapConfigPayload
)
async def get_map_config(request: Request, response: Response) -> MapConfigPayload:
    """只在前端主动加载地图时读取公开 Web Key，私密 jscode 不出服务端。"""
    _require_local_map_request(request)
    response.headers["Cache-Control"] = "private, no-store"
    settings = request.app.state.deps.services.settings
    return MapConfigPayload(
        available=settings.has_amap_js_credentials,
        js_key=settings.amap_js_api_key if settings.has_amap_js_credentials else None,
    )


@router.get("/_AMapService/{path:path}", include_in_schema=False)
async def proxy_map_service(path: str, request: Request) -> Response:
    """服务端代理高德 JS API 的有限路径，避免安全密钥进入浏览器包。"""
    _require_local_map_request(request)
    if path != "v4/map/styles":
        raise HTTPException(status_code=404, detail="该地图服务路径未开放")
    settings = request.app.state.deps.services.settings
    try:
        status, content, content_type = await forward_js_service(
            path,
            request.query_params.multi_items(),
            public_key=settings.amap_js_api_key,
            security_code=settings.amap_security_js_code,
        )
    except MapProxyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(
        content=content,
        status_code=status,
        headers={"Content-Type": content_type, "Cache-Control": "private, no-store"},
    )


@router.post(
    "/api/map/convert", response_model=MapConvertResult, summary="手动加载地图时转换已确认坐标"
)
async def convert_map_coordinates(
    payload: MapConvertPayload,
    request: Request,
    response: Response,
    context: RequestContext = Depends(get_request_context),  # noqa: B008
) -> MapConvertResult:
    """只转换当前用户已确认的 WGS84 机位，浏览器不提交精确坐标。"""
    _require_local_map_request(request)
    response.headers["Cache-Control"] = "private, no-store"
    services = request.app.state.deps.services
    records = {item.id: item for item in services.memory.list_favorite_spots(context).records}
    selected = [records.get(spot_id) for spot_id in payload.spot_ids]
    if any(
        spot is None
        or spot.coordinate_state != "ready"
        or spot.coordinate_system != "WGS84"
        or spot.latitude is None
        or spot.longitude is None
        for spot in selected
    ):
        raise HTTPException(status_code=422, detail="机位未找到或坐标不可用于地图")
    settings = services.settings
    try:
        positions = await convert_wgs84_points(
            [(spot.longitude, spot.latitude) for spot in selected if spot is not None],
            web_key=settings.amap_web_service_key,
        )
    except MapProxyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return MapConvertResult(
        positions=[MapConvertedPoint(longitude=lng, latitude=lat) for lng, lat in positions]
    )


@router.post(
    "/api/events",
    summary="确认写入拍摄事件（M1），仅显式请求才落库",
    response_model=EventCreatedPayload,
)
async def create_event(
    payload: EventCreatePayload,
    request: Request,
    context: RequestContext = Depends(get_request_context),  # noqa: B008
) -> EventCreatedPayload:
    """写入一条用户明确确认的事件记忆，不从照片复盘自动落盘。"""
    timestamp = payload.timestamp or datetime.now(timezone.utc)
    event_id = request.app.state.deps.services.memory.add_event(
        context,
        timestamp.isoformat(),
        payload.location,
        subject_type=payload.subject_type,
        summary=payload.summary,
        outcome=payload.outcome,
    )
    return EventCreatedPayload(event_id=event_id, timestamp=timestamp)


@router.post("/api/chase-plans", summary="多机位赶场规划（C1-1）", response_model=ChasePlanPayload)
async def create_chase_plan(
    payload: ChasePlanRequest,
    request: Request,
    context: RequestContext = Depends(get_request_context),  # noqa: B008
) -> ChasePlanPayload:
    """按拍摄窗口和路线服务生成确定性赶场计划，不调用 LLM。"""
    _require_timezone(payload.depart_at, "首次出发时间")
    if len({site.name for site in payload.sites}) != len(payload.sites):
        raise HTTPException(status_code=422, detail="机位名称不能重复")

    profile_sites = {
        item["name"]: item
        for item in request.app.state.deps.services.memory.profile_for(
            context
        ).valid_favorite_spots()
    }
    sites: list[ChaseSite] = []
    for item in payload.sites:
        _require_timezone(item.window_start, f"{item.name} 的窗口开始")
        _require_timezone(item.window_end, f"{item.name} 的窗口结束")
        profile_site = profile_sites.get(item.name)
        if profile_site is None:
            raise HTTPException(status_code=422, detail=f"档案中不存在机位：{item.name}")
        try:
            sites.append(
                ChaseSite(
                    name=item.name,
                    point=GeoPoint(
                        float(profile_site["latitude"]), float(profile_site["longitude"])
                    ),
                    window_start=item.window_start,
                    window_end=item.window_end,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        plan = request.app.state.deps.services.chase_planner.plan(
            sites,
            payload.transport_mode,
            payload.depart_at,
            transition_buffer_s=payload.transition_buffer_minutes * 60,
            min_shooting_s=payload.min_shoot_minutes * 60,
            transit_city=payload.transit_city,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ChasePlanPayload.model_validate(_chase_plan_to_dict(plan))


@router.get("/api/sessions", response_model=list[SessionPayload])
async def list_sessions(
    request: Request,
    response: Response,
    context: RequestContext = Depends(get_request_context),  # noqa: B008
) -> list[SessionPayload]:
    """读取当前用户已保存会话，不依赖浏览器缓存索引。"""
    response.headers["Cache-Control"] = "private, no-store"
    return [
        SessionPayload.model_validate(record.to_dict())
        for record in request.app.state.deps.sessions.list_for(context.user_id)
    ]


@router.get("/api/sessions/{session_id}/results/{result_id}", response_model=SavedResult)
async def get_saved_result(
    session_id: str,
    result_id: str,
    request: Request,
    response: Response,
    context: RequestContext = Depends(get_request_context),  # noqa: B008
) -> SavedResult:
    """按稳定标识只读原结果，不触发数据采集或更改有效期。"""
    response.headers["Cache-Control"] = "private, no-store"
    session = _owned_session(request.app.state.deps, session_id, context)
    return SavedResult.model_validate(_find_result(session, result_id))


@router.post("/api/saved-spot-plans", response_model=SavedResult)
async def create_saved_spot_plan(
    payload: SavedPlanRequest,
    request: Request,
    response: Response,
    context: RequestContext = Depends(get_request_context),  # noqa: B008
) -> SavedResult:
    """生成后保存实际 SpotPlan；保存故障明确失败，既有计划契约不变。"""
    deps = request.app.state.deps
    session = _load_or_create(deps, payload.session_id, context)
    _select_history(session, payload.result_id)
    plan = await create_spot_plan(payload.request, request, response, context)
    session.history.extend(
        [
            {"role": "user", "content": payload.request.model_dump_json()},
            {"role": "assistant", "content": plan.model_dump_json()},
        ]
    )
    try:
        return deps.sessions.save_result(
            session,
            kind="spot_plan",
            request=payload.request.model_dump(mode="json"),
            plan=plan,
            parent_result_id=payload.result_id,
        )
    except OSError as exc:
        logger.exception("会话 %s 计划保存失败", session.session_id)
        raise HTTPException(status_code=500, detail="计划保存失败，未确认已保存，请重试") from exc


@router.get(
    "/api/sessions/{session_id}",
    summary="会话历史与 TraceReport 回放",
    response_model=SessionResponsePayload,
)
async def get_session(
    session_id: str,
    request: Request,
    response: Response,
    context: RequestContext = Depends(get_request_context),  # noqa: B008
) -> SessionResponsePayload:
    """返回会话记录（历史 + 工作区）与最近一次运行的 TraceReport。"""
    response.headers["Cache-Control"] = "private, no-store"
    deps = request.app.state.deps
    session = _owned_session(deps, session_id, context)
    return SessionResponsePayload(
        session=session.to_dict(), trace=session.workspace.get("last_trace")
    )


# ------ 内部实现 ------
def _owned_session(deps: Any, session_id: str, context: RequestContext) -> SessionRecord:
    """读取会话前检查身份；不透露他人结果是否存在。"""
    session = deps.sessions.get(session_id)
    if session is None or (session.user_id or LOCAL_USER_ID) != context.user_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    return session


def _find_result(session: SessionRecord, result_id: str) -> dict[str, Any]:
    """在已授权会话内按稳定版本 ID 查找结果。"""
    for result in session.saved_results():
        if result["result_id"] == result_id:
            return result
    raise HTTPException(status_code=404, detail="结果版本不存在")


def _select_history(session: SessionRecord, result_id: str) -> None:
    """续问所选版本，不能静默使用后来产生的另一版本历史。"""
    if result_id:
        result = _find_result(session, result_id)
        session.history = list(result["history"])
        session.pipeline = dict(result["pipeline"])


def _load_or_create(deps: Any, session_id: str, context: RequestContext) -> SessionRecord:
    """空 ID 才创建；明确引用不存在的会话不能静默换成新会话。"""
    if session_id:
        return _owned_session(deps, session_id, context)
    return deps.sessions.create(user_id=context.user_id)


def _require_timezone(value: datetime, field: str) -> None:
    """拒绝无时区时间，避免赶场计划混用本地时间和 UTC。"""
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(status_code=422, detail=f"{field}必须包含时区")


def _chase_plan_to_dict(plan: Any) -> dict[str, Any]:
    """把领域赶场计划转换为稳定的 JSON API 形态。"""
    return {
        "ordered_site_names": list(plan.ordered_site_names),
        "feasible": plan.feasible,
        "risk_level": plan.risk_level,
        "total_effective_shooting_minutes": plan.total_effective_shooting_s // 60,
        "explanation": plan.explanation,
        "legs": [
            {
                "from_site": leg.from_site,
                "to_site": leg.to_site,
                "leave_at": leg.leave_at.isoformat(),
                "arrive_at": leg.arrive_at.isoformat(),
                "latest_leave_at": leg.latest_leave_at.isoformat(),
                "effective_shooting_minutes": leg.effective_shooting_s // 60,
                "margin_minutes": leg.margin_s // 60,
                "transition_buffer_minutes": leg.buffer_s // 60,
                "route": {
                    "distance_m": leg.route.distance_m,
                    "duration_minutes": leg.route.duration_s // 60,
                    "transport_mode": leg.route.transport_mode,
                    "traffic_level": leg.route.traffic_level,
                    "traffic_summary": leg.route.traffic_summary,
                    "provider": leg.route.provider,
                    "confidence": leg.route.confidence,
                    "queried_at": leg.route.queried_at.isoformat(),
                    "is_future_prediction": leg.route.is_future_prediction,
                    "degraded_reason": leg.route.degraded_reason,
                },
            }
            for leg in plan.legs
        ],
        "rejected_options": [
            {"ordered_site_names": list(item.ordered_site_names), "reason": item.reason}
            for item in plan.rejected_options
        ],
    }


__all__ = ["ChasePlanRequest", "ChatRequest", "DecideRequest", "ProfilePayload", "router"]
