"""观测与前后端事件契约（B1-2 自 infra/trace 与 api/events 下沉，D8 / D12）。

设计要点：
- `TraceEvent` 是一次可观测事件的快照（LLM / 工具 / 步骤三类），下沉到契约层后
  由记录器（infra/trace）、SSE 桥（api/events）、评估与前端类型生成（B4）共用同一份定义；
- `SSEEvent` 是 SSE 协议事件类型枚举，前后端**单一真源**：api/events.py 的常量由它派生，
  B4 起由 pydantic/OpenAPI 生成前端类型，消灭 `frontend/src/api/events.ts` 手抄漂移；
- 事件负载字段名保持既有中文口径（"输入摘要" / "结果摘要" / "数据来源" / "置信度" /
  "来源字段" / "耗时_ms"），迁移期前后端零改动；
- B4-1 起负载以 pydantic 模型显式建模（`SSEEventPayload` 判别联合）：模型直接进
  OpenAPI components，前端类型由 `npm run gen:api` 生成——事件字段改名会同时改两侧，
  漂移在 `gen:api && git diff --exit-code` 门禁上暴露。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from lighttrail.contracts.models import DecisionCard

# 轨迹事件类型（trace 记录器的三类写入点）
KIND_LLM = "llm"
KIND_TOOL = "tool"
KIND_STEP = "step"


def _now_iso() -> str:
    """当前 UTC 时间的 ISO 8601 字符串（事件时间戳）。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class SSEEvent(str, Enum):
    """SSE 协议事件类型（前后端契约真源，D8）。

    注：用类 str+Enum 而非 enum.StrEnum（后者需 py3.11，项目要求 py310+）。
    """

    QUEUED = "queued"
    STEP = "step"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TOKEN = "token"
    CARD = "card"
    ERROR = "error"
    DONE = "done"


@dataclass(frozen=True)
class TraceEvent:
    """一次可观测事件的快照。

    Attributes:
        kind: 事件类型，llm / tool / step。
        name: 事件名（模型名 / 工具名 / 步骤名）。
        payload: 事件详情（参数、结果摘要、耗时等）。
        ts: 事件时间戳（UTC ISO 8601）。
    """

    kind: str
    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=_now_iso)


# ------ SSE 事件负载（前后端单一真源：OpenAPI 生成前端类型） ------
class QueuedEvent(BaseModel):
    """排队事件：告知当前 LLM 队列位置。"""

    type: Literal["queued"] = "queued"
    position: int = 1
    session_id: str = ""


class StepEvent(BaseModel):
    """管线步骤事件（trace step 的协议形态）。"""

    type: Literal["step"] = "step"
    name: str = ""
    input_summary: str = ""
    output_summary: str = ""
    session_id: str = ""
    ts: str = ""


class ToolCallEvent(BaseModel):
    """工具调用事件（trace tool 拆成 call / result 两条）。"""

    type: Literal["tool_call"] = "tool_call"
    name: str = ""
    arguments: str = ""
    session_id: str = ""
    ts: str = ""


class ToolResultEvent(BaseModel):
    """工具结果事件：摘要文本 + **结构化结果**（前端直接消费字段，不做正则解析）。

    Attributes:
        result: 人类可读摘要（截断，供轨迹面板展示）。
        data: 工具返回的结构化 dict（JSON 可解析时提供，否则缺省）；前端按真实字段
            读取（如 sun_times 的「日出」），不再对摘要文本做正则解析（B4-4）。
    """

    type: Literal["tool_result"] = "tool_result"
    name: str = ""
    result: str = ""
    data: dict[str, Any] | None = None
    data_source: str = ""
    confidence: str = "low"
    field: str = ""
    elapsed_ms: float = 0.0
    session_id: str = ""
    ts: str = ""


class TokenEvent(BaseModel):
    """正文事件（当前按「每轮完整文本」发送，逐 token 流式留待 streaming 通道）。"""

    type: Literal["token"] = "token"
    content: str = ""
    session_id: str = ""


class CardEvent(BaseModel):
    """决策卡片事件（管线末端产物）。"""

    type: Literal["card"] = "card"
    card: DecisionCard
    text: str = ""
    session_id: str = ""


class ErrorEvent(BaseModel):
    """错误事件（单请求失败不中断流，随后必有 done）。"""

    type: Literal["error"] = "error"
    message: str = ""
    session_id: str = ""


class DoneEvent(BaseModel):
    """结束事件（事件流终止标记）。"""

    type: Literal["done"] = "done"
    text: str = ""
    session_id: str = ""


# SSE 事件负载判别联合（type 字段判别）：OpenAPI 生成前端类型的单一真源
SSEEventPayload = Annotated[
    QueuedEvent | StepEvent | ToolCallEvent | ToolResultEvent | TokenEvent | CardEvent | ErrorEvent | DoneEvent,
    Field(discriminator="type"),
]


__all__ = [
    "KIND_LLM",
    "KIND_STEP",
    "KIND_TOOL",
    "CardEvent",
    "DoneEvent",
    "ErrorEvent",
    "QueuedEvent",
    "SSEEvent",
    "SSEEventPayload",
    "StepEvent",
    "TokenEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    "TraceEvent",
]