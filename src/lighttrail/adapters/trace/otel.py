"""trace 事件 → OTel GenAI 语义属性映射（B3-4，v4 §3 D12）。

设计要点：
- **命名跟标准、实现保闭环**：内部事件载荷继续用中文键（SSE 协议与前端已依赖），
  本模块提供到 OpenTelemetry GenAI 语义约定的映射，供未来接入 OTel exporter / Logfire；
- 映射只做字段改名与类型规整，不引入新语义（未标准化的字段进 `lighttrail.*` 自定义命名空间）。
"""

from __future__ import annotations

from typing import Any

from lighttrail.contracts.events import KIND_LLM, KIND_STEP, KIND_TOOL, TraceEvent

# 事件类型 → gen_ai.operation.name
_OPERATION_NAMES: dict[str, str] = {
    KIND_LLM: "chat",
    KIND_TOOL: "execute_tool",
    KIND_STEP: "pipeline_step",
}


def to_otel_attributes(event: TraceEvent) -> dict[str, Any]:
    """把一条 trace 事件映射为 OTel GenAI 语义属性字典。

    Args:
        event: trace 事件快照。

    Returns:
        属性字典；含 `gen_ai.*` 标准键与 `lighttrail.*` 自定义键。
    """
    attributes: dict[str, Any] = {
        "gen_ai.operation.name": _OPERATION_NAMES.get(event.kind, event.kind),
        "lighttrail.event.kind": event.kind,
        "lighttrail.event.ts": event.ts,
    }
    payload = event.payload
    if event.kind == KIND_LLM:
        attributes["gen_ai.request.model"] = payload.get("模型") or event.name
        if payload.get("tokens") is not None:
            attributes["gen_ai.usage.total_tokens"] = payload.get("tokens")
        if payload.get("输入_tokens") is not None:
            attributes["gen_ai.usage.input_tokens"] = payload.get("输入_tokens")
        if payload.get("输出_tokens") is not None:
            attributes["gen_ai.usage.output_tokens"] = payload.get("输出_tokens")
        if payload.get("耗时_秒") is not None:
            attributes["lighttrail.llm.duration_s"] = payload.get("耗时_秒")
    elif event.kind == KIND_TOOL:
        attributes["gen_ai.tool.name"] = event.name
        if payload.get("耗时_ms") is not None:
            attributes["lighttrail.tool.duration_ms"] = payload.get("耗时_ms")
        if payload.get("数据来源"):
            attributes["lighttrail.tool.data_source"] = payload.get("数据来源")
        if payload.get("置信度"):
            attributes["lighttrail.tool.confidence"] = payload.get("置信度")
        if payload.get("来源字段"):
            attributes["lighttrail.tool.field"] = payload.get("来源字段")
    elif event.kind == KIND_STEP:
        attributes["lighttrail.pipeline.step"] = event.name
    return attributes


__all__ = ["to_otel_attributes"]