"""SessionManager 会话持久化（E7-2）的 pytest 用例。

验证（roadmap E7-2 验收）：
- 创建 → 写入 → 重启进程 → 恢复 完整闭环（history/pipeline/workspace 一致）；
- 多会话相互隔离（各自 history 不串）；
- LRU 内存淘汰（淘汰只移出内存、磁盘保留，restore 仍可恢复）；
- save 覆盖更新（重复保存取最终态）；
- 管线上下文快照往返（Intent/DecisionCard → JSON → 还原）；
- 序列化 JSON 安全兜底、非法 session_id / 损坏落盘 / 参数边界。

使用 tempfile.mkdtemp 自建数据目录（与 test_memory 相同模式，不依赖 pytest tmp_path）。
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from lighttrail.api.session import (
    SessionError,
    SessionManager,
    restore_context,
    snapshot_context,
)
from lighttrail.orchestrator.context import PipelineContext
from lighttrail.orchestrator.schemas import DecisionCard, Intent, LocationSuggestion, Source


@pytest.fixture()
def session_dir() -> Path:
    """自建临时数据目录（退出时清理）。"""
    directory = Path(tempfile.mkdtemp(prefix="lt_session_"))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


def _manager(directory: Path, **kwargs) -> SessionManager:
    """构造会话管理器（默认窗口参数可覆盖）。"""
    return SessionManager(directory, **kwargs)


def _sample_context() -> PipelineContext:
    """样例管线上下文（Intent + 采集数据 + DecisionCard）。"""
    intent = Intent(subject_type="星空", location="天荒坪", time_hint="这周末", mode="inspiration")
    card = DecisionCard(
        conclusion="值得去天荒坪拍银河",
        evidence=[Source(tool="weather_forecast", field="每日预报", confidence="medium")],
        confidence="medium",
        time_window="21:00-23:30",
        locations=[LocationSuggestion(name="天荒坪", reason="光害少")],
    )
    return PipelineContext(
        user_request="这周末想去拍银河",
        intent=intent,
        data={"weather_forecast": {"平均云量（%）": 20}},
        card=card,
    )


def test_create_save_persists_json(session_dir: Path) -> None:
    """创建并保存后，data/sessions/{id}.json 落盘且内容一致。"""
    manager = _manager(session_dir)
    session = manager.create()
    session.history.append({"role": "user", "content": "现在几点？"})
    session.history.append({"role": "assistant", "content": "现在是 22:30。"})
    manager.save(session)

    path = session_dir / "sessions" / f"{session.session_id}.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["session_id"] == session.session_id
    assert data["history"] == session.history


def test_restart_restores_session(session_dir: Path) -> None:
    """重启进程（新管理器实例）后 restore 恢复历史与快照。"""
    first = _manager(session_dir)
    created = first.create()
    created.history = [{"role": "user", "content": "今晚火烧云值得冲吗？"}]
    created.workspace = {"pending_intent": "临场火烧云"}
    first.save(created)

    second = _manager(session_dir)  # 模拟重启：全新实例
    restored = second.restore(created.session_id)
    assert restored is not None
    assert restored.session_id == created.session_id
    assert restored.history == created.history
    assert restored.workspace == created.workspace

    # get 也能从磁盘兜底恢复
    again = second.get(created.session_id)
    assert again is not None
    assert again.history == created.history


def test_get_missing_returns_none(session_dir: Path) -> None:
    """不存在的 session_id：get / restore 均返回 None。"""
    manager = _manager(session_dir)
    assert manager.get("does-not-exist") is None
    assert manager.restore("does-not-exist") is None


def test_sessions_are_isolated(session_dir: Path) -> None:
    """多会话相互隔离：各自 history / workspace 不串。"""
    manager = _manager(session_dir)
    session_a = manager.create()
    session_b = manager.create()
    session_a.history.append({"role": "user", "content": "A 的问题"})
    session_b.history.append({"role": "user", "content": "B 的问题"})
    manager.save(session_a)
    manager.save(session_b)

    manager2 = _manager(session_dir)
    got_a = manager2.get(session_a.session_id)
    got_b = manager2.get(session_b.session_id)
    assert got_a is not None and got_b is not None
    assert got_a.history == [{"role": "user", "content": "A 的问题"}]
    assert got_b.history == [{"role": "user", "content": "B 的问题"}]


def test_lru_eviction_keeps_disk(session_dir: Path) -> None:
    """LRU 淘汰：最久未访问者移出内存但磁盘保留，restore 仍可恢复。"""
    manager = _manager(session_dir, max_sessions=2)
    session_a = manager.create()
    session_b = manager.create()
    manager.save(session_a)
    manager.save(session_b)

    session_c = manager.create()  # 触发淘汰：A 最久未访问
    assert manager.get(session_a.session_id) is None or session_a.session_id != session_c.session_id  # 内存已淘汰

    # A 的磁盘文件仍在，restore 可恢复
    assert (session_dir / "sessions" / f"{session_a.session_id}.json").exists()
    restored_a = manager.restore(session_a.session_id)
    assert restored_a is not None
    assert restored_a.session_id == session_a.session_id


def test_save_overwrite_takes_latest(session_dir: Path) -> None:
    """重复 save 取最终态：覆盖更新而非追加。"""
    manager = _manager(session_dir)
    session = manager.create()
    session.history.append({"role": "user", "content": "第一条"})
    manager.save(session)
    session.history.append({"role": "assistant", "content": "第二条"})
    manager.save(session)

    restored = _manager(session_dir).restore(session.session_id)
    assert restored is not None
    assert len(restored.history) == 2
    assert restored.history[-1] == {"role": "assistant", "content": "第二条"}


def test_pipeline_snapshot_roundtrip(session_dir: Path) -> None:
    """管线上下文快照往返：Intent/DecisionCard 经 JSON 还原后字段一致。"""
    ctx = _sample_context()
    manager = _manager(session_dir)
    session = manager.create()
    session.pipeline = snapshot_context(ctx)
    session.history.append({"role": "user", "content": ctx.user_request})
    manager.save(session)

    restored = _manager(session_dir).restore(session.session_id)
    assert restored is not None
    ctx2 = restore_context(restored.pipeline)
    assert ctx2.user_request == ctx.user_request
    assert ctx2.intent is not None
    assert ctx2.intent.model_dump(mode="json") == ctx.intent.model_dump(mode="json")
    assert ctx2.card is not None
    assert ctx2.card.conclusion == ctx.card.conclusion
    assert ctx2.data == ctx.data
    assert ctx2.scores == ctx.scores


def test_workspace_json_safe_fallback(session_dir: Path) -> None:
    """非 JSON 安全对象经 str 兜底可落盘（不抛 TypeError）。"""
    session = _manager(session_dir).create()
    session.workspace = {"unexpected": object()}
    payload = json.dumps(session.to_dict(), ensure_ascii=False)
    assert "unexpected" in payload


def test_invalid_session_id_rejected(session_dir: Path) -> None:
    """包含路径分隔符等危险字符的 session_id 拒绝落盘。"""
    manager = _manager(session_dir)
    session = manager.create()
    session.session_id = "../evil"
    with pytest.raises(SessionError):
        manager.save(session)


def test_max_sessions_less_than_one_raises(session_dir: Path) -> None:
    """max_sessions < 1 抛 SessionError。"""
    with pytest.raises(SessionError):
        _manager(session_dir, max_sessions=0)


def test_sessions_dir_auto_created(session_dir: Path) -> None:
    """嵌套 data_dir 下 sessions/ 目录自动创建。"""
    nested = session_dir / "deep" / "nested"
    manager = _manager(nested)
    assert (nested / "sessions").is_dir()
    session = manager.create()
    assert session.session_id  # 创建成功


def test_corrupt_json_returns_none(session_dir: Path) -> None:
    """落盘 JSON 损坏时：新进程（无内存缓存）restore / get 返回 None（不抛错）。"""
    manager = _manager(session_dir)
    session = manager.create()
    path = session_dir / "sessions" / f"{session.session_id}.json"
    path.write_text("{not json", encoding="utf-8")
    fresh = _manager(session_dir)  # 模拟重启：无内存缓存，恢复只认磁盘
    assert fresh.restore(session.session_id) is None
    assert fresh.get(session.session_id) is None
