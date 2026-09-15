"""记忆统一门面：按 ``RequestContext.user_id`` 隔离所有可写记忆。

设计要点：
- 每位用户的档案、事件和语义记忆固定落在 ``data/users/{user_id}/memory/``；
- ``RequestContext`` 是所有读写接口的首参，身份不由记忆层自行猜测；
- 存储实现仍是 JSON/SQLite，未来替换为共享数据库时不改变上层调用约定。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lighttrail.contracts.context import LOCAL_USER_ID, RequestContext
from lighttrail.memory.events import EventRecord, EventStore, to_prompt_section
from lighttrail.memory.migration import migrate_memory_namespace
from lighttrail.memory.profile import UserProfile
from lighttrail.memory.semantic import SemanticCandidate, SemanticStore, extract_from_events

_USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_LOCAL_CONTEXT = RequestContext()


@dataclass(frozen=True)
class MemoryBlock:
    """一个记忆注入块。"""

    name: str
    text: str = ""


@dataclass
class _UserMemory:
    """单个用户命名空间中已打开的存储对象。"""

    directory: Path
    profile: UserProfile
    events: EventStore
    semantic: SemanticStore


class MemoryManager:
    """记忆层门面：用请求上下文选择 per-user 的存储命名空间。"""

    def __init__(
        self,
        data_dir: str | Path,
        profile: UserProfile | None = None,
        event_store: EventStore | None = None,
        semantic_store: SemanticStore | None = None,
    ) -> None:
        """初始化记忆管理器。

        注入替身仅绑定本期默认 ``_local`` 用户，供离线测试使用；其他用户总是从其
        独立目录构造存储。
        """
        self._data_dir = Path(data_dir)
        self._users: dict[str, _UserMemory] = {}
        self._injected = (profile, event_store, semantic_store)
        if not any(item is not None for item in self._injected):
            migrate_memory_namespace(self._data_dir)

    # ------ 对外接口 ------
    def profile_for(self, ctx: RequestContext) -> UserProfile:
        """返回指定用户的档案。"""
        return self._state(ctx).profile

    @property
    def profile(self) -> UserProfile:
        """默认本地用户档案（仅兼容本期单用户展示路径）。"""
        return self.profile_for(RequestContext())

    def update_profile(
        self, ctx: RequestContext | dict[str, Any], fields: dict[str, Any] | None = None
    ) -> UserProfile:
        """更新并持久化指定用户档案。"""
        if isinstance(ctx, dict):
            fields = ctx
            ctx = RequestContext()
        if fields is None:
            raise TypeError("fields 不能为空")
        state = self._state(ctx)
        state.profile.update(fields)
        state.profile.save(state.directory)
        return state.profile

    def add_event(
        self,
        ctx: RequestContext | str,
        timestamp: str,
        location: str | None = None,
        **fields: object,
    ) -> int:
        """追加指定用户的一条事件记忆。"""
        if isinstance(ctx, str):
            ctx, timestamp, location = RequestContext(), ctx, timestamp
        if location is None:
            raise TypeError("location 不能为空")
        return self._state(ctx).events.add_event(timestamp, location, **fields)

    def build_injections(self, ctx: RequestContext | str = _LOCAL_CONTEXT, intent: str = "") -> list[MemoryBlock]:
        """按意图组装指定用户的档案、语义与事件注入块。"""
        if isinstance(ctx, str):
            ctx, intent = RequestContext(), ctx
        state = self._state(ctx)
        blocks: list[MemoryBlock] = []
        profile_text = state.profile.to_prompt_section()
        if profile_text:
            blocks.append(MemoryBlock("profile", profile_text))
        semantic_hits = state.semantic.match(intent)
        if semantic_hits:
            blocks.append(MemoryBlock("semantic", "\n".join(semantic_hits)))
        events = self.retrieve_events(ctx, intent)
        if events:
            blocks.append(MemoryBlock("events", to_prompt_section(events)))
        return blocks

    def retrieve_events(
        self, ctx: RequestContext | str, intent: str | None = None, limit: int = 3
    ) -> list[EventRecord]:
        """按意图检索指定用户的事件记忆。"""
        if isinstance(ctx, str):
            ctx, intent = RequestContext(), ctx
        if intent is None:
            raise TypeError("intent 不能为空")
        if not intent:
            return []
        state = self._state(ctx)
        location = self._extract_location(state.profile, intent)
        subject = _extract_subject(intent)
        if not location and not subject:
            return []
        return state.events.search_events(location=location, subject_type=subject, limit=limit)

    def semantic_propose(
        self,
        ctx: RequestContext | str,
        content: str | list[str] | tuple[str, ...],
        keywords: list[str] | tuple[str, ...] | None = None,
    ) -> int:
        """暂存一条指定用户的语义记忆候选。"""
        if isinstance(ctx, str):
            ctx, content, keywords = RequestContext(), ctx, content  # type: ignore[assignment]
        return self._state(ctx).semantic.staged_add(str(content), keywords)

    def semantic_confirm(self, ctx: RequestContext | int, candidate_id: int | None = None) -> bool:
        """确认指定用户的语义候选并持久化。"""
        if isinstance(ctx, int):
            ctx, candidate_id = RequestContext(), ctx
        if candidate_id is None:
            raise TypeError("candidate_id 不能为空")
        return self._state(ctx).semantic.confirm(candidate_id)

    def semantic_reject(self, ctx: RequestContext | int, candidate_id: int | None = None) -> bool:
        """否决指定用户的语义候选。"""
        if isinstance(ctx, int):
            ctx, candidate_id = RequestContext(), ctx
        if candidate_id is None:
            raise TypeError("candidate_id 不能为空")
        return self._state(ctx).semantic.reject(candidate_id)

    def sediment_semantics(
        self, ctx: RequestContext = _LOCAL_CONTEXT, *, min_samples: int = 3, min_success_rate: float = 0.7
    ) -> list[SemanticCandidate]:
        """从指定用户事件中提炼语义候选，仍须二次确认才会注入。"""
        state = self._state(ctx)
        candidates = extract_from_events(
            state.events.search_events(limit=500),
            min_samples=min_samples,
            min_success_rate=min_success_rate,
        )
        for candidate in candidates:
            if not state.semantic.contains(candidate.content):
                state.semantic.staged_add(candidate.content, list(candidate.keywords))
        return candidates

    def sediment_favorite_spots(
        self, ctx: RequestContext = _LOCAL_CONTEXT, *, min_count: int = 2
    ) -> list[dict[str, Any]]:
        """将有坐标的高频事件地点沉淀到指定用户档案。"""
        state = self._state(ctx)
        grouped: dict[str, dict[str, Any]] = {}
        for event in state.events.search_events(limit=500):
            location = event.location.strip()
            if not location:
                continue
            info = grouped.setdefault(location, {"count": 0, "coordinates": "", "subjects": set()})
            info["count"] += 1
            if event.coordinates:
                info["coordinates"] = event.coordinates
            if event.subject_type:
                info["subjects"].add(event.subject_type)
        existing = {str(spot.get("名称") or spot.get("name") or "") for spot in state.profile.favorite_spots}
        added: list[dict[str, Any]] = []
        for location, info in grouped.items():
            if info["count"] < min_count or not info["coordinates"] or location in existing:
                continue
            latitude, longitude = _split_coordinates(str(info["coordinates"]))
            if latitude is None:
                continue
            spot = {"名称": location, "纬度": latitude, "经度": longitude, "题材": "、".join(sorted(info["subjects"]))[:40]}
            state.profile.favorite_spots.append(spot)
            added.append(spot)
        if added:
            state.profile.save(state.directory)
        return added

    # ------ 内部实现 ------
    def _state(self, ctx: RequestContext) -> _UserMemory:
        """按 user_id 取得或惰性创建用户存储。"""
        user_id = ctx.user_id
        if not _USER_ID_PATTERN.fullmatch(user_id):
            raise ValueError("user_id 只能包含字母、数字、下划线和连字符")
        if user_id in self._users:
            return self._users[user_id]

        directory = self._data_dir / "users" / user_id / "memory"
        profile, event_store, semantic_store = self._injected
        if user_id == LOCAL_USER_ID and any(item is not None for item in self._injected):
            state = _UserMemory(
                directory=directory,
                profile=profile if profile is not None else UserProfile.load(directory),
                events=event_store if event_store is not None else EventStore(directory / "events.db"),
                semantic=semantic_store if semantic_store is not None else SemanticStore(directory / "semantic.json"),
            )
        else:
            state = _UserMemory(
                directory=directory,
                profile=UserProfile.load(directory),
                events=EventStore(directory / "events.db"),
                semantic=SemanticStore(directory / "semantic.json"),
            )
        self._users[user_id] = state
        return state

    @staticmethod
    def _extract_location(profile: UserProfile, intent: str) -> str | None:
        """从用户档案中的已知地点识别地点关键词。"""
        for name in profile.common_locations:
            if name and name in intent:
                return name
        return None


_SUBJECT_KEYWORDS = (
    ("日出", "日出"), ("日落", "日落"), ("朝霞", "朝霞"), ("晚霞", "晚霞"),
    ("火烧云", "火烧云"), ("银河", "银河"), ("星空", "星空"), ("星轨", "星轨"),
    ("蓝调", "蓝调"), ("黄金时刻", "黄金时刻"), ("夜景", "夜景"), ("车轨", "车轨"),
    ("云雾", "云雾"),
)


def _extract_subject(intent: str) -> str | None:
    """从意图中识别首个规范题材。"""
    for keyword, canonical in _SUBJECT_KEYWORDS:
        if keyword in intent:
            return canonical
    return None


def _split_coordinates(value: str) -> tuple[float | None, float | None]:
    """把 ``lat,lng`` 解析为浮点对，非法输入返回空值。"""
    try:
        latitude_text, longitude_text = value.split(",")
        return float(latitude_text), float(longitude_text)
    except ValueError:
        return None, None
