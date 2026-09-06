"""事件记忆检索工具（M1.2：search_memory 供 ReAct 主动检索）。

设计要点（架构 v2.0 §2.5，E3-2 落地）：
- 工具返回结构化中文事件列表（含坐标与天气/天象快照），供模型做个性化判断、
  复拍对比（D2.3-07）等需要历史记忆的场景主动检索；
- 默认存储指向 data_dir/events.db（经 load_settings 读取，可被测试注入替换）；
- 检索三个维度：关键词（地点/摘要/标签模糊）、地点、题材，参数化查询防注入。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lighttrail.agent.tools import registry
from lighttrail.config import load_settings
from lighttrail.memory.events import EventRecord, EventStore

# 模块级默认存储：首次调用时按配置初始化（测试可经 set_event_store 注入替换）
_DEFAULT_STORE: EventStore | None = None


def set_event_store(store: EventStore | None) -> None:
    """替换默认事件存储（测试注入用；传 None 恢复懒加载）。

    Args:
        store: 事件存储实例。
    """
    global _DEFAULT_STORE
    _DEFAULT_STORE = store


def _get_store() -> EventStore:
    """返回当前事件存储（缺省按 settings.data_dir/events.db 懒加载）。"""
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = EventStore(Path(load_settings().data_dir) / "events.db")
    return _DEFAULT_STORE


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


@registry.tool(
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
)
def search_memory(query: str = "", location: str = "", subject_type: str = "", limit: int = 5) -> dict:
    """检索历史拍摄事件。

    Args:
        query: 关键词（模糊匹配地点/摘要/标签）。
        location: 地点关键字（模糊）。
        subject_type: 题材（精确）。
        limit: 最多返回条数。

    Returns:
        {"命中数": int, "事件": [...]}；无命中时事件为空列表。
    """
    if not 1 <= limit <= 10:
        return {"error": "limit 须在 1-10 之间"}
    events = _get_store().search_events(
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
