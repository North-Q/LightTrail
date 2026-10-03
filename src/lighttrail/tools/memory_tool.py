"""事件记忆检索工具（M1.2：search_memory 供 ReAct 主动检索）。

设计要点（架构 v2.0 §2.5，E3-2 落地；R0-3 请求身份接线）：
- 工具返回结构化中文事件列表（含坐标与天气/天象快照），供模型做个性化判断、
  复拍对比（D2.3-07）等需要历史记忆的场景主动检索；
- 生产检索只经 ToolContext.memory 与 ToolContext.request，缺少请求记忆时明确报错，
  不回落到部署根目录的旧事件库；
- 检索三个维度：关键词（地点/摘要/标签模糊）、地点、题材，参数化查询防注入。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from lighttrail.contracts.memory import EventRecord as ContractEventRecord
from lighttrail.contracts.tool import Confidence, Tool, ToolContext, ToolResult, ToolSpec
from lighttrail.memory.events import EventRecord, EventStore


def _event_to_dict(event: EventRecord | ContractEventRecord) -> dict[str, Any]:
    """把事件记录转为工具返回的中文字段 dict。"""
    return {
        "时间": event.timestamp,
        "地点": event.location,
        "坐标": event.coordinates,
        "题材": event.subject_type,
        "摘要": event.summary,
        "教训": event.lesson,
        "标签": list(event.tags),
        "天气/天象快照": event.weather_snapshot,
        "器材": event.equipment,
    }


_SPEC_SEARCH_MEMORY = ToolSpec(
    name="search_memory",
    description=(
        "检索历史拍摄事件记忆：按关键词（地点/摘要/标签）、地点、题材过滤，返回最近命中的事件列表"
        "（含精确坐标与当时的天气/天象快照）。当用户询问『以前拍过吗』『某地某题材的历史情况』"
        "『复拍对比』『这次和上次比如何』等需要结合历史拍摄经历的问题时调用，用于个性化决策与复拍判断。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "关键词，模糊匹配地点/摘要/标签，如『临港』『朝霞』",
                "default": "",
            },
            "location": {
                "type": "string",
                "description": "地点关键字，模糊匹配，如『临港』",
                "default": "",
            },
            "subject_type": {
                "type": "string",
                "description": "题材，精确匹配，如『火烧云』『银河』",
                "default": "",
            },
            "limit": {
                "type": "integer",
                "description": "最多返回条数（1-10，默认 5）",
                "default": 5,
            },
        },
        "required": [],
    },
    capabilities=frozenset(["tools"]),
    main_field="命中数",
    confidence=Confidence.LOW,
)


def search_memory(
    store: EventStore,
    query: str = "",
    location: str = "",
    subject_type: str = "",
    limit: int = 5,
) -> dict:
    """检索历史拍摄事件（存储显式传入，模块无全局态）。

    Args:
        store: 事件存储。
        query: 关键词（模糊匹配地点/摘要/标签）。
        location: 地点关键字（模糊）。
        subject_type: 题材（精确）。
        limit: 最多返回条数。

    Returns:
        {"命中数": int, "事件": [...]}；无命中时事件为空列表。
    """
    if not 1 <= limit <= 10:
        return {"error": "limit 须在 1-10 之间"}
    events = store.search_events(
        query,
        location=location or None,
        subject_type=subject_type or None,
        limit=limit,
    )
    return _format_events(events)


def _format_events(events: Sequence[EventRecord | ContractEventRecord]) -> dict[str, Any]:
    """把当前命名空间的检索结果转为工具可读数据。"""
    return {
        "命中数": len(events),
        "事件": [_event_to_dict(event) for event in events],
        "提示": "坐标为拍摄机位精确值，可配合当前天象/天气判断复拍价值",
    }


class SearchMemoryTool:
    """事件记忆检索工具：从请求上下文解析用户命名空间。"""

    spec = _SPEC_SEARCH_MEMORY

    def __call__(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        """只查询当前请求用户的事件记忆。

        Args:
            ctx: 携带用户身份和记忆端口的工具上下文。
            **kwargs: 检索参数。

        Returns:
            工具返回载体。
        """
        limit = kwargs.get("limit", 5)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
            data: dict[str, Any] = {"error": "limit 须在 1-10 之间"}
        elif ctx.memory is None:
            data = {"error": "当前请求未装配用户记忆"}
        else:
            events = ctx.memory.search_events(
                ctx.request,
                str(kwargs.get("query") or ""),
                location=str(kwargs.get("location") or "") or None,
                subject_type=str(kwargs.get("subject_type") or "") or None,
                limit=limit,
            )
            data = _format_events(events)
            data["数据来源"] = f"用户事件记忆：{ctx.request.user_id}"
        return ToolResult(content=json.dumps(data, ensure_ascii=False, default=str), data=data)


def build_tools() -> tuple[Tool, ...]:
    """构造由 ToolContext 提供请求记忆的声明式工具。

    Returns:
        本模块的声明式工具元组。
    """
    return (SearchMemoryTool(),)


__all__ = ["SearchMemoryTool", "build_tools", "search_memory"]
