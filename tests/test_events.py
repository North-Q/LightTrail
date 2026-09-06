"""事件记忆（E3-2）的 pytest 用例。

验证：
- EventStore 增/查闭环：按地点/题材/关键词检索命中，坐标与天气快照字段保留；
- 参数化查询防注入（SQL 特殊字符不破坏表）；
- occurrence_counts 地点聚合（复拍提醒 D2.3-07 数据基础）；
- to_prompt_section 注入文本格式；
- MemoryManager 意图驱动按需检索（地点/题材规则命中才注入，空意图不注入）；
- search_memory 工具返回结构化事件（含坐标与快照）。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from lighttrail.agent.tools import registry
from lighttrail.memory import EventStore, MemoryManager
from lighttrail.memory.events import to_prompt_section
from lighttrail.tools import memory_tool

_SNAPSHOT = {"云量（%）": 35, "火烧云评分": 78, "月相": "蛾眉月"}


@pytest.fixture()
def events_dir() -> Path:
    """自建临时数据目录（pytest tmp_path 受沙箱 ACL 限制时仍可用）。"""
    directory = Path(tempfile.mkdtemp(prefix="lt_events_"))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture()
def store(events_dir: Path) -> EventStore:
    """独立事件存储（避免污染全局默认存储）。"""
    return EventStore(events_dir / "events.db")


def _seed(store: EventStore) -> None:
    store.add_event(
        "2026-03-18T05:20:00+08:00",
        "福州大楼",
        subject_type="朝霞",
        summary="霞光极佳，日出方位正对街道",
        lesson="提前 40 分钟到机位可占位",
        tags=["城市", "朝霞"],
        coordinates="31.2301,121.4735",
        weather_snapshot=_SNAPSHOT,
        equipment="24-105mm F4",
    )
    store.add_event(
        "2026-07-02T19:05:00+08:00",
        "临港海边",
        subject_type="火烧云",
        summary="高云为主，晚霞中等偏上",
        weather_snapshot={"云量（%）": 55, "火烧云评分": 62},
        coordinates="30.9123,121.9123",
    )


# ------ 增 / 查闭环 ------
def test_add_and_search_by_location(store) -> None:
    """按地点模糊检索命中，且保留坐标与天气快照字段。"""
    _seed(store)
    hits = store.search_events(location="福州", limit=5)
    assert len(hits) == 1
    event = hits[0]
    assert event.subject_type == "朝霞"
    assert event.coordinates == "31.2301,121.4735"
    assert event.weather_snapshot["火烧云评分"] == 78
    assert event.tags == ("城市", "朝霞")


def test_search_by_subject_and_query(store) -> None:
    """按题材精确 + 关键词模糊（摘要/标签）命中。"""
    _seed(store)
    assert len(store.search_events(subject_type="火烧云")) == 1
    assert len(store.search_events(query="晚霞")) == 1  # 摘要命中
    assert len(store.search_events(query="霞光")) == 1


def test_search_no_match_returns_empty(store) -> None:
    """无命中返回空列表。"""
    _seed(store)
    assert store.search_events("不存在的关键词") == []
    assert store.search_events(subject_type="银河") == []


def test_search_order_by_timestamp_desc(store) -> None:
    """结果按时间倒序。"""
    _seed(store)
    hits = store.search_events(limit=5)
    assert [h.timestamp[:10] for h in hits] == ["2026-07-02", "2026-03-18"]


def test_occurrence_counts_by_location(store) -> None:
    """地点聚合次数（同一地点多次事件合并计数）。"""
    _seed(store)
    store.add_event("2026-03-19T05:20:00+08:00", "福州大楼", subject_type="朝霞", summary="再拍")
    counts = store.occurrence_counts(subject_type="朝霞")
    assert counts == {"福州大楼": 2}
    assert store.occurrence_counts()["临港海边"] == 1


# ------ 注入安全 ------
def test_query_injection_safe(store) -> None:
    """LIKE 参数化：SQL 注入字符不破坏表结构。"""
    _seed(store)
    before = store.count_all()
    hits = store.search_events("'; DROP TABLE events; --")
    assert hits == []
    assert store.count_all() == before  # 表未被破坏
    # 还能正常检索
    assert len(store.search_events(location="福州")) == 1


# ------ 注入文本 ------
def test_to_prompt_section_format(store) -> None:
    """注入文本包含日期/地点/题材/快照/坐标，空列表返回空串。"""
    _seed(store)
    text = to_prompt_section(store.search_events(location="福州"))
    assert "· [2026-03-18] 福州大楼" in text
    assert "朝霞" in text
    assert "云量（%）:35" in text
    assert "坐标 31.2301,121.4735" in text
    assert to_prompt_section([]) == ""


# ------ MemoryManager 意图驱动 ------
def test_memory_manager_retrieve_events_by_intent(events_dir) -> None:
    """意图含地点+题材时命中；无关意图不检索。"""
    manager = MemoryManager(events_dir)
    manager.update_profile({"common_locations": ["临港海边", "福州大楼"]})
    manager.add_event(
        "2026-07-02T19:05:00+08:00",
        "临港海边",
        subject_type="火烧云",
        summary="晚霞中等偏上",
        weather_snapshot={"火烧云评分": 62},
        coordinates="30.9123,121.9123",
    )
    hits = manager.retrieve_events("明天去临港海边拍火烧云如何？")
    assert len(hits) == 1
    assert hits[0].location == "临港海边"
    assert manager.retrieve_events("晚上吃饭去哪？") == []


def test_build_injections_appends_events_block(events_dir) -> None:
    """命中事件时注入块顺序为 profile → events；空意图只有档案块。"""
    manager = MemoryManager(events_dir)
    manager.update_profile({"common_locations": ["临港海边"], "preferences": ["风光"]})
    manager.add_event(
        "2026-07-02T19:05:00+08:00",
        "临港海边",
        subject_type="火烧云",
        summary="晚霞中等偏上",
    )
    blocks = manager.build_injections("临港海边拍火烧云")
    assert [b.name for b in blocks] == ["profile", "events"]
    assert "临港海边" in blocks[1].text
    assert manager.build_injections("随便聊聊") == [blocks[0]]  # 只含档案


# ------ search_memory 工具 ------
def test_search_memory_tool_returns_structured_events(store, monkeypatch) -> None:
    """工具返回中文字段事件（含坐标与快照），命中数为 0 时返回空列表。"""
    _seed(store)
    monkeypatch.setattr(memory_tool, "_DEFAULT_STORE", store)
    result = memory_tool.search_memory(location="福州")
    assert result["命中数"] == 1
    event = result["事件"][0]
    assert event["坐标"] == "31.2301,121.4735"
    assert event["天气/天象快照"]["火烧云评分"] == 78
    assert event["题材"] == "朝霞"
    empty = memory_tool.search_memory(query="银河")
    assert empty["命中数"] == 0
    assert empty["事件"] == []


def test_search_memory_tool_validates_limit(store, monkeypatch) -> None:
    """limit 越界返回 error。"""
    monkeypatch.setattr(memory_tool, "_DEFAULT_STORE", store)
    assert "error" in memory_tool.search_memory(limit=0)
    assert "error" in memory_tool.search_memory(limit=11)


def test_search_memory_registered() -> None:
    """search_memory 已注册到全局注册表。"""
    names = {s["function"]["name"] for s in registry.to_openai_schema()}
    assert "search_memory" in names
