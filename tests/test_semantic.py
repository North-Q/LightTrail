"""语义记忆提炼（E6-3）的 pytest 用例。

验证：
- 事件补 outcome 字段（含旧库迁移）：无 outcome 事件的库升级后仍可用；
- extract_from_events：3 条同题材成功事件 → 命中规则产出候选；样本/成功率不足不出候选；
- MemoryManager.sediment_semantics：候选进待确认队列，未确认不注入；确认后注入生效；
- 重复沉淀防重（二次调用不重复入队）；
- MemoryManager.sediment_favorite_spots：高频带坐标机位沉淀进档案（根治坐标写死上海的
  数据前提），profile 注入段可展示。
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from lighttrail.memory import MemoryManager, SemanticStore


@pytest.fixture()
def workdir() -> Path:
    directory = Path(tempfile.mkdtemp(prefix="lt_semantic_"))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


def _manager(workdir: Path) -> MemoryManager:
    """内存语义存储 + 临时数据目录的 manager（不写真实 data/）。"""
    return MemoryManager(workdir, semantic_store=SemanticStore(path=None))


def _add_glow_events(manager: MemoryManager, successes: int, fails: int = 0) -> None:
    for index in range(successes):
        manager.add_event(
            f"2026-06-{index + 1:02d}T19:00:00+08:00",
            "临港海边",
            subject_type="火烧云",
            outcome="success",
            coordinates="30.9123,121.9123",
            weather_snapshot={"火烧云评分": 70 + index},
        )
    for index in range(fails):
        manager.add_event(
            f"2026-07-{index + 1:02d}T19:00:00+08:00",
            "临港海边",
            subject_type="火烧云",
            outcome="fail",
            coordinates="30.9123,121.9123",
        )


# ------ outcome 字段与迁移 ------
def test_event_outcome_roundtrip_and_migration(workdir: Path) -> None:
    """带 outcome 写入可读回；旧库（无 outcome 列）迁移后不报错。"""
    manager = _manager(workdir)
    manager.add_event("2026-06-01T19:00:00+08:00", "临港", subject_type="火烧云", outcome="success")
    hit = manager._events.search_events(subject_type="火烧云")
    assert hit[0].outcome == "success"
    # 模拟旧库：用 sqlite 直建无 outcome 表 → 新 EventStore 打开自动迁移
    import sqlite3

    old_db = workdir / "old.db"
    conn = sqlite3.connect(old_db)
    conn.execute(
        "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,"
        " location TEXT NOT NULL, coordinates TEXT NOT NULL DEFAULT '', subject_type TEXT NOT NULL DEFAULT '',"
        " summary TEXT NOT NULL DEFAULT '', lesson TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '[]',"
        " weather_snapshot TEXT NOT NULL DEFAULT '{}', equipment TEXT NOT NULL DEFAULT '',"
        " created_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    conn.execute(
        "INSERT INTO events (timestamp, location, subject_type) VALUES (?, ?, ?)",
        ("2026-01-01T10:00:00+08:00", "旧点", "日出"),
    )
    conn.commit()
    conn.close()
    from lighttrail.memory.events import EventStore

    migrated = EventStore(old_db)
    old_event = migrated.search_events(location="旧点")
    assert old_event[0].outcome == ""


# ------ extract_from_events 规则 ------
def test_extract_requires_min_samples(workdir: Path) -> None:
    """样本不足 3 不提炼。"""
    manager = _manager(workdir)
    _add_glow_events(manager, successes=2)
    assert manager.sediment_semantics() == []


def test_extract_only_high_success_rate(workdir: Path) -> None:
    """成功率低于阈值不提炼（2 成 1 成）。"""
    manager = _manager(workdir)
    _add_glow_events(manager, successes=1, fails=4)  # 5 样本成功率 20%
    assert manager.sediment_semantics() == []


def test_extract_produces_candidate_on_rule_hit(workdir: Path) -> None:
    """3 条成功事件 → 提炼结论候选（含题材关键词）。"""
    manager = _manager(workdir)
    _add_glow_events(manager, successes=3)
    candidates = manager.sediment_semantics()
    assert len(candidates) == 1
    assert "火烧云" in candidates[0].content
    assert "100%" in candidates[0].content
    assert candidates[0].keywords == ("火烧云",)


# ------ double-confirm 生效链 ------
def test_sediment_pending_not_injected_until_confirmed(workdir: Path) -> None:
    """候选未确认不注入；确认后 match 命中（double-confirm 防污染）。"""
    manager = _manager(workdir)
    manager.update_profile({"preferences": ["风光"]})
    _add_glow_events(manager, successes=3)
    manager.sediment_semantics()
    # 未确认：注入块不含 semantic（可能含 profile + events 按需块）
    blocks = manager.build_injections("火烧云")
    assert [b.name for b in blocks if b.name == "semantic"] == []
    # 确认第一个候选 → 注入命中
    assert manager.semantic_confirm(0) is True
    blocks = manager.build_injections("火烧云")
    assert any(b.name == "semantic" and "火烧云" in b.text for b in blocks)


def test_sediment_deduplicates_on_second_call(workdir: Path) -> None:
    """重复调用不重复入候选队列。"""
    manager = _manager(workdir)
    _add_glow_events(manager, successes=3)
    first = manager.sediment_semantics()
    second = manager.sediment_semantics()
    assert len(first) == len(second) == 1
    # 队列只有一份 → confirm id 0 命中
    assert manager.semantic_confirm(0) is True
    assert manager.semantic_confirm(0) is False  # 已被确认消费


# ------ favorite_spots 沉淀 ------
def test_sediment_favorite_spots(workdir: Path) -> None:
    """高频带坐标地点沉淀进档案 favorite_spots，且注入段可展示。"""
    manager = _manager(workdir)
    manager.update_profile({"preferences": ["风光"]})
    _add_glow_events(manager, successes=3)
    added = manager.sediment_favorite_spots(min_count=2)
    assert len(added) == 1
    assert added[0]["名称"] == "临港海边"
    assert added[0]["纬度"] == 30.9123
    # 重复调用不重复加
    assert manager.sediment_favorite_spots(min_count=2) == []
    # 档案落盘 + 注入段含坐标
    saved = json.loads((workdir / "profile.json").read_text(encoding="utf-8"))
    assert len(saved["favorite_spots"]) == 1
    assert "常去机位：临港海边(30.9123,121.9123)" in manager.profile.to_prompt_section()


def test_sediment_skips_locations_without_coordinates(workdir: Path) -> None:
    """无坐标事件不沉淀（数据前提缺失时跳过，不写脏数据）。"""
    manager = _manager(workdir)
    for index in range(2):
        manager.add_event(f"2026-05-{index + 1:02d}T19:00:00+08:00", "无名滩涂", subject_type="日落")
    assert manager.sediment_favorite_spots(min_count=2) == []
    assert manager.profile.favorite_spots == []
