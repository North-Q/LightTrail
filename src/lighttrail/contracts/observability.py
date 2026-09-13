"""可观测性端口契约（B1-4，D12 / §2.3）。

设计要点：
- `TraceSink` 是 trace 事件的**唯一出口**：记录器（infra/trace）实现它，工具经
  `ToolContext.sink.emit` 上报，服务层订阅它做 SSE（B3-4 起加 OTel GenAI 命名与
  事件载荷白名单）；
- 端口只有「写事件 / 订阅 / 取注入文本」三件事：不做业务判断（置信度规则在
  infra/confidence，工具来源规则由 ToolSpec.confidence 声明）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from lighttrail.contracts.events import TraceEvent


@runtime_checkable
class TraceSink(Protocol):
    """观测事件出口（记录器实现；NullTrace 为关闭态实现）。"""

    def emit(self, event: TraceEvent) -> None:
        """写入一条观测事件并同步派发订阅者。

        Args:
            event: 事件快照。
        """
        ...

    def subscribe(self, callback: Callable[[TraceEvent], None]) -> Callable[[], None]:
        """订阅事件流。

        Args:
            callback: 事件回调（同步派发）。

        Returns:
            取消订阅的闭包。
        """
        ...

    def to_prompt_section(self, limit: int = 12) -> str:
        """生成注入 prompt 的精简轨迹文本。

        Args:
            limit: 最多纳入的轨迹条数。

        Returns:
            轨迹文本（无事件时为空串）。
        """
        ...


__all__ = ["TraceSink"]