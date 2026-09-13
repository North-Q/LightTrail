"""事件记忆检索工具（M1.2：search_memory 供 ReAct 主动检索）。

设计要点（架构 v2.0 §2.5，E3-2 落地；B2-3 去全局态）：
- 工具返回结构化中文事件列表（含坐标与天气/天象快照），供模型做个性化判断、
  复拍对比（D2.3-07）等需要历史记忆的场景主动检索；
- 存储依赖**经构造注入**（store_factory）：本模块无模块级可写全局、无 setter ——
  测试与装配根各自构造自己的 SearchMemoryTool，互不污染（R1/R2 的解药）；
- 检索三个维度：关键词（地点/摘要/标签模糊）、地点、题材，参数化查询防注入。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lighttrail.config import load_settings
from lighttrail.contracts.tool import Confidence, Tool, ToolContext, ToolResult, ToolSpec
from lighttrail.memory.events import EventRecord, EventStore


def default_store_factory() -> EventStore:
    """按部署配置构造默认事件存储（data_dir/events.db）。

    由装配根（或迁移期收集点）调用；本模块不持有任何全局存储实例。

    Returns:
        事件存储实例。
    """
    return EventStore(Path(load_settings().data_dir) / "events.db")


def _event_to_dict(event: EventRecord) -> dict[str, Any]:
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
    return {
        "命中数": len(events),
        "事件": [_event_to_dict(event) for event in events],
        "提示": "坐标为拍摄机位精确值，可配合当前天象/天气判断复拍价值",
    }


class SearchMemoryTool:
    """事件记忆检索工具：存储经构造注入（无模块级全局与服务定位器）。

    Args:
        store_factory: 事件存储工厂（每次调用取一次，便于装配根懒加载与测试注入）。
    """

    spec = _SPEC_SEARCH_MEMORY

    def __init__(self, store_factory: Callable[[], EventStore]) -> None:
        self._store_factory = store_factory

    def __call__(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        """执行检索（依赖从工厂取，ctx 未用）。

        Args:
            ctx: 运行上下文（本工具依赖在构造时注入）。
            **kwargs: 检索参数。

        Returns:
            工具返回载体。
        """
        data = search_memory(self._store_factory(), **kwargs)
        return ToolResult(content=json.dumps(data, ensure_ascii=False, default=str), data=data)


def build_tools(store_factory: Callable[[], EventStore]) -> tuple[Tool, ...]:
    """构造本模块工具（依赖显式注入；装配根/测试各自构造自己的实例）。

    Args:
        store_factory: 事件存储工厂。

    Returns:
        本模块的声明式工具元组。
    """
    return (SearchMemoryTool(store_factory),)


__all__ = ["SearchMemoryTool", "build_tools", "default_store_factory", "search_memory"]