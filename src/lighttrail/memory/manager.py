"""记忆统一门面：按 ``RequestContext.user_id`` 隔离所有可写记忆。

设计要点：
- 每位用户的档案、事件和语义记忆固定落在 ``data/users/{user_id}/memory/``；
- ``RequestContext`` 是所有读写接口的首参，身份不由记忆层自行猜测；
- 存储实现仍是 JSON/SQLite，未来替换为共享数据库时不改变上层调用约定。
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from lighttrail.contracts.context import LOCAL_USER_ID, RequestContext
from lighttrail.contracts.favorite_spot import FavoriteSpotInput, FavoriteSpotRecord
from lighttrail.memory.events import EventRecord, EventStore, to_prompt_section
from lighttrail.memory.migration import migrate_memory_namespace
from lighttrail.memory.profile import UserProfile
from lighttrail.memory.semantic import SemanticCandidate, SemanticStore, extract_from_events
from lighttrail.memory.spot_migration import SpotMigrationPreview, preview_favorite_spot_migration

_USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_LOCAL_CONTEXT = RequestContext()


def _spot_record(spot_id: str, payload: FavoriteSpotInput) -> FavoriteSpotRecord:
    """将已校验输入转成可持久化记录，不推断坐标系。

    Args:
        spot_id: 当前用户命名空间中的稳定机位 ID。
        payload: 已验证的机位输入。

    Returns:
        带计算状态的机位记录。
    """
    if payload.latitude is None:
        state = "missing_coordinates"
    elif payload.coordinate_system == "WGS84":
        state = "ready"
    else:
        state = "unsupported_system"
    return FavoriteSpotRecord.model_validate(
        {**payload.model_dump(), "id": spot_id, "coordinate_state": state}
    )


@dataclass(frozen=True)
class MemoryBlock:
    """一个记忆注入块。"""

    name: str
    text: str = ""


class FavoriteSpotNotFoundError(LookupError):
    """当前用户档案中不存在指定机位。"""


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

    def equipment_for(self, ctx: RequestContext) -> str:
        """从当前用户档案提取照片处方所需的相机与镜头。"""
        profile = self.profile_for(ctx)
        parts: list[str] = []
        if profile.camera_body:
            parts.append(profile.camera_body)
        if profile.lenses:
            parts.append("镜头：" + "、".join(profile.lenses))
        return "；".join(parts)

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

    def list_favorite_spots(self, ctx: RequestContext) -> SpotMigrationPreview:
        """只读列出当前用户机位，旧坐标缺坐标系时保持不可计算。

        Args:
            ctx: 当前请求用户。

        Returns:
            机位记录与无法投影的旧条目索引。
        """
        return preview_favorite_spot_migration(
            self._state(ctx).profile.favorite_spots, ctx.user_id, policy="unknown"
        )

    def create_favorite_spot(
        self, ctx: RequestContext, payload: FavoriteSpotInput
    ) -> FavoriteSpotRecord:
        """在当前用户档案新增一个有稳定 ID 的机位。

        Args:
            ctx: 当前请求用户。
            payload: 新机位的已验证输入。

        Returns:
            已保存的机位记录。
        """
        state = self._state(ctx)
        record = _spot_record(str(uuid4()), payload)
        self._write_spots(state, [*state.profile.favorite_spots, record.model_dump()])
        return record

    def update_favorite_spot(
        self, ctx: RequestContext, spot_id: str, payload: FavoriteSpotInput
    ) -> FavoriteSpotRecord:
        """显式确认并更新一条机位；首次修改前保留原档案备份。

        Args:
            ctx: 当前请求用户。
            spot_id: 待更新机位的稳定 ID。
            payload: 替换该机位的完整输入。

        Returns:
            更新后的机位记录。

        Raises:
            FavoriteSpotNotFoundError: 当前用户没有此机位。
        """
        state = self._state(ctx)
        index = self._spot_index(ctx, spot_id)
        record = _spot_record(spot_id, payload)
        spots = state.profile.favorite_spots.copy()
        spots[index] = record.model_dump()
        self._write_spots(state, spots)
        return record

    def delete_favorite_spot(self, ctx: RequestContext, spot_id: str) -> None:
        """按当前用户的机位 ID 删除记录，保留首次修改前的档案备份。

        Args:
            ctx: 当前请求用户。
            spot_id: 待删除机位的稳定 ID。

        Raises:
            FavoriteSpotNotFoundError: 当前用户没有此机位。
        """
        state = self._state(ctx)
        index = self._spot_index(ctx, spot_id)
        spots = state.profile.favorite_spots.copy()
        spots.pop(index)
        self._write_spots(state, spots)

    def _write_spots(self, state: _UserMemory, spots: list[dict[str, Any]]) -> None:
        """备份原档案并原子写入；写盘失败时恢复内存视图。

        Args:
            state: 当前用户的存储状态。
            spots: 待写入的完整机位列表。
        """
        self._backup_profile(state)
        previous = state.profile.favorite_spots
        state.profile.favorite_spots = spots
        try:
            state.profile.save(state.directory)
        except OSError:
            state.profile.favorite_spots = previous
            raise

    def _spot_index(self, ctx: RequestContext, spot_id: str) -> int:
        """仅在当前用户档案中解析机位 ID。

        Args:
            ctx: 当前请求用户。
            spot_id: 待查找的机位 ID。

        Returns:
            原始档案列表中的索引。

        Raises:
            FavoriteSpotNotFoundError: 当前用户没有此机位。
        """
        preview = self.list_favorite_spots(ctx)
        for record, index in zip(preview.records, preview.record_indices, strict=True):
            if record.id == spot_id:
                return index
        raise FavoriteSpotNotFoundError("当前用户档案中不存在该机位")

    @staticmethod
    def _backup_profile(state: _UserMemory) -> None:
        """首次修改机位前保留原始 profile.json；后续写入不覆盖备份。

        Args:
            state: 当前用户的存储状态。
        """
        original = state.directory / "profile.json"
        backup = state.directory / "profile.pre-s1-2.json.bak"
        if original.is_file() and not backup.exists():
            shutil.copy2(original, backup)

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

    def build_injections(
        self, ctx: RequestContext | str = _LOCAL_CONTEXT, intent: str = ""
    ) -> list[MemoryBlock]:
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

    def search_events(
        self,
        ctx: RequestContext,
        query: str = "",
        *,
        location: str | None = None,
        subject_type: str | None = None,
        limit: int = 5,
    ) -> list[EventRecord]:
        """按请求用户检索事件，供 ReAct 工具经记忆端口访问。

        Args:
            ctx: 请求身份。
            query: 地点、摘要或标签关键词。
            location: 可选地点过滤。
            subject_type: 可选题材过滤。
            limit: 最多返回条数。

        Returns:
            当前用户目录内的命中事件。
        """
        return self._state(ctx).events.search_events(
            query, location=location, subject_type=subject_type, limit=limit
        )

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
        self,
        ctx: RequestContext = _LOCAL_CONTEXT,
        *,
        min_samples: int = 3,
        min_success_rate: float = 0.7,
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
        existing = {
            str(spot.get("名称") or spot.get("name") or "") for spot in state.profile.favorite_spots
        }
        added: list[dict[str, Any]] = []
        for location, info in grouped.items():
            if info["count"] < min_count or not info["coordinates"] or location in existing:
                continue
            latitude, longitude = _split_coordinates(str(info["coordinates"]))
            if latitude is None:
                continue
            spot = {
                "name": location,
                "latitude": latitude,
                "longitude": longitude,
                "subject": "、".join(sorted(info["subjects"]))[:40],
            }
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
                events=event_store
                if event_store is not None
                else EventStore(directory / "events.db"),
                semantic=semantic_store
                if semantic_store is not None
                else SemanticStore(directory / "semantic.json"),
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
    ("日出", "日出"),
    ("日落", "日落"),
    ("朝霞", "朝霞"),
    ("晚霞", "晚霞"),
    ("火烧云", "火烧云"),
    ("银河", "银河"),
    ("星空", "星空"),
    ("星轨", "星轨"),
    ("蓝调", "蓝调"),
    ("黄金时刻", "黄金时刻"),
    ("夜景", "夜景"),
    ("车轨", "车轨"),
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
