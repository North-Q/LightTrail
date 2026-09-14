"""适配层：可观测性（B3-4）——TraceSink 端口的实现门面与 OTel 映射。

设计要点：
- `contracts.observability.TraceSink` 是端口；本期实现仍是 `infra.trace.TraceRecorder`
  （runtime / 域工具均可引用 infra，不违反分层契约），本包提供适配层视角的门面与 OTel 映射，
  TODO(B3-5/B5-4): 目标分层落地后把实现移入本包（域工具经 ToolContext.sink 注入）；
- `to_otel_attributes` 把内部中文键载荷映射为 OTel GenAI 语义属性（命名跟标准、实现保闭环）。
"""

from __future__ import annotations

from lighttrail.adapters.trace.otel import to_otel_attributes
from lighttrail.contracts.observability import TraceSink
from lighttrail.infra.trace import TraceRecorder

__all__ = ["TraceRecorder", "TraceSink", "to_otel_attributes"]