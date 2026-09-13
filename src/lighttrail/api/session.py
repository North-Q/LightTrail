"""会话持久化管理（E7-2，A-08 对话持久化）。

设计要点（架构 v2.0 §2.9 会话层）：
- 会话 = {历史消息, 管线上下文快照, 记忆工作区}，以 `session_id` 为键；
- 进程内字典缓存（LRU 上限：淘汰只移出内存、不删磁盘）+ JSON 落盘
  `data/sessions/{session_id}.json`——创建 → 写入 → 重启进程 → 恢复闭环；
- 单机单进程为默认部署：接口以 session_id 为键、与进程无关，未来多 worker
  仅需更换后端，接口不变；
- 预留 `user_id` 字段（单机自用无需认证，多用户时启用）；
- 并发约束（B0-1 修掉三处缺陷）：
  1. 锁内只改内存（缓存字典与 LRU 访问序），磁盘 IO 一律在锁外完成，
     淘汰路径不再「持锁调 save 再取锁」自锁（threading.Lock 不可重入）；
  2. 缓存主体不外借：create/get/restore 一律返回深拷贝，调用方改副本不影响
     缓存，改动必须经 save() 才生效；
  3. 落盘走「同目录临时文件 + os.replace」原子替换，同 id 并发保存不会写出
     半截 JSON；
- 序列化底线：内容全部 JSON 安全（pydantic 模型转 dict、未知类型 str 兜底），
  会话中不落任何凭据（API Key 不进会话）。
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
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
# Windows 上 os.replace 遇目标文件被短暂占用（并发替换 / 杀软扫描）时的重试次数与退避基数（秒）
_REPLACE_RETRIES = 8
_REPLACE_BACKOFF_SEC = 0.01


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
            （淘汰前尽力落盘，不删磁盘文件；get/restore 仍可恢复）。
    """

    def __init__(self, data_dir: str | Path, *, max_sessions: int = _DEFAULT_MAX_SESSIONS) -> None:
        if max_sessions < 1:
            raise SessionError("max_sessions 必须 >= 1")
        self._data_dir = Path(data_dir)
        self._sessions_dir = self._data_dir / "sessions"
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self._max_sessions = max_sessions
        self._lock = threading.Lock()
        # 实例级写锁：串行化落盘（只包住临时文件写入 + os.replace，不与 self._lock 嵌套）
        self._write_lock = threading.Lock()
        self._cache: dict[str, SessionRecord] = {}
        self._access: dict[str, int] = {}
        self._clock = 0

    # ------ 对外接口：增查存 ------
    def create(self, *, user_id: str = "") -> SessionRecord:
        """新建会话（生成 session_id 并登记内存；磁盘留待首次 save）。

        Args:
            user_id: 预留用户标识。

        Returns:
            新会话记录的深拷贝——调用方改动需经 save() 才写回缓存与磁盘。
        """
        session = SessionRecord(session_id=uuid.uuid4().hex, user_id=user_id)
        with self._lock:
            self._cache[session.session_id] = session
            self._touch(session.session_id)
            evicted = self._evict_if_needed()
        self._persist_evicted(evicted)
        return copy.deepcopy(session)

    def get(self, session_id: str) -> SessionRecord | None:
        """取会话：内存命中返回深拷贝，未命中尝试从磁盘恢复。

        Args:
            session_id: 会话标识。

        Returns:
            会话记录的深拷贝（改动需经 save() 生效）；不存在时返回 None。
        """
        with self._lock:
            cached = self._cache.get(session_id)
            if cached is not None:
                self._touch(session_id)
                return copy.deepcopy(cached)
        return self.restore(session_id)

    def restore(self, session_id: str) -> SessionRecord | None:
        """从磁盘恢复会话（进程重启后恢复入口）。

        Args:
            session_id: 会话标识。

        Returns:
            恢复的会话记录深拷贝；磁盘无此会话时返回 None。
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
            evicted = self._evict_if_needed()
        self._persist_evicted(evicted)
        return copy.deepcopy(session)

    def save(self, session: SessionRecord) -> None:
        """持久化会话到 JSON 并刷新内存缓存与访问序。

        落盘（原子替换）在锁外、缓存写入在锁内；缓存存的是深拷贝，
        保存后继续改动入参对象不会污染缓存。

        Args:
            session: 待保存的会话记录。

        Raises:
            SessionError: session_id 为空或含非法字符（路径穿越等）。
        """
        if not session.session_id:
            raise SessionError("session_id 不能为空")
        session.updated_at = _now_iso()
        self._write_session(session)
        with self._lock:
            self._cache[session.session_id] = copy.deepcopy(session)
            self._touch(session.session_id)
            evicted = self._evict_if_needed()
        self._persist_evicted(evicted)

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

    def _write_session(self, session: SessionRecord) -> None:
        """原子落盘：先写同目录临时文件，再 os.replace 覆盖目标。

        同 id 并发保存时，读者只会看到「旧的完整文件」或「新的完整文件」，
        不会读到写了一半的 JSON（B0-1）。

        Args:
            session: 待落盘的会话记录（调用方负责先刷新 updated_at）。
        """
        path = self._path_of(session.session_id)
        tmp_path = path.parent / f"{path.name}.{uuid.uuid4().hex}.tmp"
        payload = json.dumps(session.to_dict(), ensure_ascii=False, indent=2)
        # 写锁串行化落盘：避免两个 os.replace 争抢同一目标（Windows 上会互相干扰）
        with self._write_lock:
            try:
                tmp_path.write_text(payload, encoding="utf-8")
                self._replace_with_retry(tmp_path, path)
            finally:
                tmp_path.unlink(missing_ok=True)

    @staticmethod
    def _replace_with_retry(tmp_path: Path, path: Path) -> None:
        """os.replace 覆盖目标文件，Windows 短时占用时退避重试。

        Windows 上目标文件被其他句柄持有（外部读者 / 杀软扫描）时，替换会抛
        PermissionError（WinError 5）；短暂退避后重试即可完成。

        Args:
            tmp_path: 已写完内容的临时文件。
            path: 目标文件路径。

        Raises:
            PermissionError: 重试耗尽后目标仍被占用。
        """
        for attempt in range(_REPLACE_RETRIES):
            try:
                os.replace(tmp_path, path)
                return
            except PermissionError:
                if attempt == _REPLACE_RETRIES - 1:
                    raise
                time.sleep(_REPLACE_BACKOFF_SEC * (attempt + 1))

    def _evict_if_needed(self) -> list[SessionRecord]:
        """内存超限时摘出最久未访问的会话（调用方须持锁）。

        只改内存、不落盘：返回被摘出的会话主体，由调用方在**锁外**落盘，
        避免「持不可重入锁再调 save」造成自锁。

        Returns:
            被淘汰的会话记录列表（按淘汰顺序；可能为空）。
        """
        evicted: list[SessionRecord] = []
        while len(self._cache) > self._max_sessions and self._access:
            victim_id = min(self._access, key=lambda key: self._access[key])
            victim = self._cache.pop(victim_id, None)
            self._access.pop(victim_id, None)
            if victim is not None:
                evicted.append(victim)
        return evicted

    def _persist_evicted(self, evicted: list[SessionRecord]) -> None:
        """把淘汰出内存的会话尽力落盘（锁外调用；已有磁盘文件的不重写）。

        Args:
            evicted: _evict_if_needed 返回的淘汰列表。
        """
        for victim in evicted:
            try:
                if self._path_of(victim.session_id).exists():
                    continue
                self._write_session(victim)
                logger.info("淘汰内存会话 %s 前已落盘", victim.session_id)
            except Exception:  # noqa: BLE001 - 淘汰兜底失败不影响其他会话
                logger.warning("淘汰会话落盘失败：%s", victim.session_id)


__all__ = [
    "SessionError",
    "SessionManager",
    "SessionRecord",
    "restore_context",
    "snapshot_context",
]