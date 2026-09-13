"""观测与前后端事件契约（B1-2 自 infra/trace 与 api/events 下沉，D8 / D12）。

设计要点：
- `TraceEvent` 是一次可观测事件的快照（LLM / 工具 / 步骤三类），下沉到契约层后
  由记录器（infra/trace）、SSE 桥（api/events）、评估与前端类型生成（B4）共用同一份定义；
- `SSEEvent` 是 SSE 协议事件类型枚举，前后端**单一真源**：api/events.py 的常量由它派生，
  B4 起由 pydantic/OpenAPI 生成前端类型，消灭 `frontend/src/api/events.ts` 手抄漂移；
- 事件负载字段名保持既有中文口径（"输入摘要" / "结果摘要" / "数据来源" / "置信度" /
  "来源字段" / "耗时_ms"），迁移期前后端零改动。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

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


__all__ = [
    "KIND_LLM",
    "KIND_STEP",
    "KIND_TOOL",
    "SSEEvent",
    "TraceEvent",
]