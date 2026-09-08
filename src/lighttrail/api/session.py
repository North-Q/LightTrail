"""会话持久化管理（E7-2，A-08 对话持久化）。

设计要点（架构 v2.0 §2.9 会话层）：
- 会话 = {历史消息, 管线上下文快照, 记忆工作区}，以 `session_id` 为键；
- 进程内字典缓存（LRU 上限：淘汰只移出内存、不删磁盘）+ JSON 落盘
  `data/sessions/{session_id}.json`——创建 → 写入 → 重启进程 → 恢复闭环；
- 单机单进程为默认部署（默认串行配置下单进程即最优）：接口以
  session_id 为键、与进程无关，未来多 worker 仅需更换后端，接口不变；
- 预留 `user_id` 字段（单机自用无需认证，多用户时启用）；
- 序列化底线：内容全部 JSON 安全（pydantic 模型转 dict、未知类型 str 兜底），
  会话中不落任何凭据（API Key 不进会话）。
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lighttrail.orchestrator.context import PipelineContext
from lighttrail.orchestrator.schemas import DecisionCard, Intent

logger = logging.getLogger("lighttrail.api.session")

# 内存缓存默认上限（超过后按最近访问淘汰最久未用者）
_DEFAULT_MAX_SESSIONS = 100


class SessionError(Exception):
    """会话管理参数非法或数据损坏。"""


def _now_iso() -> str:
    """当前 UTC 时间的 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _json_safe(value: Any) -> Any:
    """把值转为 JSON 安全形态（pydantic 模型 dict 化，未知类型 str 兜底）。"""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def snapshot_context(ctx: PipelineContext | None) -> dict[str, Any]:
    """把管线上下文转为 JSON 安全快照（Intent / DecisionCard → 纯 dict）。

    Args:
        ctx: 管线上下文；None 时返回空快照。

    Returns:
        可直接落盘的上下文快照字典。
    """
    if ctx is None:
        return {}
    return {
        "user_request": ctx.user_request,
        "intent": ctx.intent.model_dump(mode="json") if ctx.intent is not None else None,
        "data": ctx.data,
        "scores": ctx.scores,
        "card": ctx.card.model_dump(mode="json") if ctx.card is not None else None,
    }


def restore_context(snapshot: dict[str, Any]) -> PipelineContext:
    """从快照还原管线上下文（字段缺失时用默认值，不抛错）。

    Args:
        snapshot: snapshot_context 产出的快照字典。

    Returns:
        还原的管线上下文。
    """
    intent_raw = snapshot.get("intent")
    card_raw = snapshot.get("card")
    return PipelineContext(
        user_request=snapshot.get("user_request", ""),
        intent=Intent.model_validate(intent_raw) if intent_raw else None,
        data=dict(snapshot.get("data") or {}),
        scores=dict(snapshot.get("scores") or {}),
        card=DecisionCard.model_validate(card_raw) if card_raw else None,
    )


@dataclass
class SessionRecord:
    """一个会话的可持久化状态。

    Attributes:
        session_id: 会话唯一标识（uuid4 hex）。
        history: 消息历史（OpenAI 格式消息字典列表，不含 system）。
        pipeline: 管线上下文快照（snapshot_context 产物）。
        workspace: 记忆工作区（意图/临时状态等）。
        user_id: 预留用户标识（单机自用缺省空串）。
        created_at: 创建时间（UTC ISO 8601）。
        updated_at: 最近保存时间（UTC ISO 8601）。
    """

    session_id: str
    history: list[dict[str, Any]] = field(default_factory=list)
    pipeline: dict[str, Any] = field(default_factory=dict)
    workspace: dict[str, Any] = field(default_factory=dict)
    user_id: str = ""
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    # ------ 对外接口：序列化 ------
    def to_dict(self) -> dict[str, Any]:
        """转为可落盘字典（全 JSON 安全）。"""
        return {
            "session_id": self.session_id,
            "history": _json_safe(self.history),
            "pipeline": _json_safe(self.pipeline),
            "workspace": _json_safe(self.workspace),
            "user_id": self.user_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionRecord:
        """从落盘字典还原（未知键忽略，缺失键用默认值，保证向后兼容）。"""
        return cls(
            session_id=str(data.get("session_id", "")),
            history=list(data.get("history") or []),
            pipeline=dict(data.get("pipeline") or {}),
            workspace=dict(data.get("workspace") or {}),
            user_id=str(data.get("user_id", "")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )


class SessionManager:
    """会话管理器：内存缓存（LRU）+ JSON 落盘。

    Args:
        data_dir: 数据目录（会话落盘到其下 sessions/ 子目录，缺省自动创建）。
        max_sessions: 内存缓存上限；超过后按最近访问淘汰最久未用会话
            （磁盘文件保留，get/restore 仍可恢复）。
    """

    def __init__(self, data_dir: str | Path, *, max_sessions: int = _DEFAULT_MAX_SESSIONS) -> None:
        if max_sessions < 1:
            raise SessionError("max_sessions 必须 >= 1")
        self._data_dir = Path(data_dir)
        self._sessions_dir = self._data_dir / "sessions"
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self._max_sessions = max_sessions
        self._lock = threading.Lock()
        self._cache: dict[str, SessionRecord] = {}
        self._access: dict[str, int] = {}
        self._clock = 0

    # ------ 对外接口：增查存 ------
    def create(self, *, user_id: str = "") -> SessionRecord:
        """新建会话（生成 session_id 并登记内存；磁盘留待首次 save）。

        Args:
            user_id: 预留用户标识。

        Returns:
            新会话记录。
        """
        session = SessionRecord(session_id=uuid.uuid4().hex, user_id=user_id)
        with self._lock:
            self._cache[session.session_id] = session
            self._touch(session.session_id)
            self._evict_if_needed()
        return session

    def get(self, session_id: str) -> SessionRecord | None:
        """取会话：内存命中直接返回，未命中尝试从磁盘恢复。

        Args:
            session_id: 会话标识。

        Returns:
            会话记录；不存在时返回 None。
        """
        with self._lock:
            cached = self._cache.get(session_id)
            if cached is not None:
                self._touch(session_id)
                return cached
        return self.restore(session_id)

    def restore(self, session_id: str) -> SessionRecord | None:
        """从磁盘恢复会话（进程重启后恢复入口）。

        Args:
            session_id: 会话标识。

        Returns:
            恢复的会话记录；磁盘无此会话时返回 None。
        """
        path = self._path_of(session_id)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        try:
            session = SessionRecord.from_dict(json.loads(raw))
        except (ValueError, TypeError) as exc:
            logger.warning("会话 %s 落盘数据损坏，忽略：%s", session_id, exc)
            return None
        with self._lock:
            self._cache[session.session_id] = session
            self._touch(session.session_id)
            self._evict_if_needed()
        return session

    def save(self, session: SessionRecord) -> None:
        """持久化会话到 JSON 并刷新内存缓存与访问序。

        Args:
            session: 待保存的会话记录。

        Raises:
            SessionError: session_id 为空。
        """
        if not session.session_id:
            raise SessionError("session_id 不能为空")
        session.updated_at = _now_iso()
        data = session.to_dict()
        self._path_of(session.session_id).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with self._lock:
            self._cache[session.session_id] = session
            self._touch(session.session_id)
            self._evict_if_needed()

    # ------ 内部实现 ------
    def _path_of(self, session_id: str) -> Path:
        """会话落盘路径（session_id 只允许安全字符）。"""
        if any(ch in session_id for ch in '/\\:*?"<>|'):
            raise SessionError(f"非法 session_id：{session_id!r}")
        return self._sessions_dir / f"{session_id}.json"

    def _touch(self, session_id: str) -> None:
        """更新最近访问序号（LRU 老化依据）。"""
        self._clock += 1
        self._access[session_id] = self._clock

    def _evict_if_needed(self) -> None:
        """内存超限时淘汰最久未访问的会话（先尽力落盘，不删磁盘文件）。"""
        while len(self._cache) > self._max_sessions:
            victim_id = min(self._access, key=self._access.get)  # type: ignore[arg-type]
            victim = self._cache.pop(victim_id, None)
            self._access.pop(victim_id, None)
            if victim is not None and not self._path_of(victim_id).exists():
                try:
                    self.save(victim)
                    logger.info("淘汰内存会话 %s 前已落盘", victim_id)
                except Exception:  # noqa: BLE001 - 淘汰兜底失败不影响其他会话
                    logger.warning("淘汰会话落盘失败：%s", victim_id)


__all__ = [
    "SessionError",
    "SessionManager",
    "SessionRecord",
    "restore_context",
    "snapshot_context",
]
