"""记忆统一门面：MemoryManager 按意图组装记忆注入块（M1）。

设计要点（架构 v2.0 §2.5，E3-1 起步）：
- 暴露给 ContextBuilder/编排层的是 `build_injections(intent) -> list[MemoryBlock]`，
  ContextBuilder 只关心「有哪些块、各块文本」，不感知具体记忆实现；
- 四层记忆对应四种注入策略：档案常驻（profile）、事件按需（events，E3-2）、
  语义择优（semantic，E3-3）、短期记忆在对话窗口内由 ContextBuilder 管理；
- 数据本地化：档案 JSON / 语义 JSON / 事件 SQLite（data/ 下）；
- 事件按需注入：retrieve_events(intent) 由地点/题材规则命中才检索 top-k，不常驻；
- 语义择优注入：SemanticStore.match(intent) 命中规则时注入 1-2 条到档案段旁（第④层）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lighttrail.memory.events import EventRecord, EventStore, to_prompt_section
from lighttrail.memory.profile import UserProfile
from lighttrail.memory.semantic import SemanticCandidate, SemanticStore, extract_from_events


@dataclass(frozen=True)
class MemoryBlock:
    """一个记忆注入块。

    Attributes:
        name: 块名（profile / events / semantic…），供 ContextBuilder 与测试识别。
        text: 注入文本（非空才有意义，空块由调用方丢弃）。
    """

    name: str
    text: str = ""


class MemoryManager:
    """记忆层门面：持有档案等记忆源，按意图产出注入块。"""

    def __init__(
        self,
        data_dir: str | Path,
        profile: UserProfile | None = None,
        event_store: EventStore | None = None,
        semantic_store: SemanticStore | None = None,
    ) -> None:
        """初始化记忆管理器。

        Args:
            data_dir: 数据目录（profile.json / events.db / semantic.json 所在）。
            profile: 可注入的档案实例；缺省从 data_dir 加载。
            event_store: 可注入的事件存储（测试隔离用）；缺省用 data_dir/events.db。
            semantic_store: 可注入的语义存储（测试隔离用）；缺省用 data_dir/semantic.json。
        """
        self._data_dir = Path(data_dir)
        self._profile = profile if profile is not None else UserProfile.load(self._data_dir)
        self._events = event_store if event_store is not None else EventStore(self._data_dir / "events.db")
        self._semantic = (
            semantic_store if semantic_store is not None else SemanticStore(self._data_dir / "semantic.json")
        )

    # ------ 对外接口 ------
    @property
    def profile(self) -> UserProfile:
        """当前用户档案。"""
        return self._profile

    def update_profile(self, fields: dict[str, Any]) -> UserProfile:
        """更新并持久化用户档案（E3-1 阶段唯一写路径）。

        Args:
            fields: 待更新字段（见 UserProfile.update）。

        Returns:
            更新后的档案实例。
        """
        self._profile.update(fields)
        self._profile.save(self._data_dir)
        return self._profile

    def add_event(self, timestamp: str, location: str, **fields: object) -> int:
        """追加事件记忆（转发 EventStore.add_event）。

        Args:
            timestamp: 事件时间（ISO 8601）。
            location: 地点名称。
            **fields: 其余事件字段（subject_type / summary / coordinates /
                weather_snapshot / equipment…），见 EventStore.add_event。

        Returns:
            新事件 ID。
        """
        return self._events.add_event(timestamp, location, **fields)

    def build_injections(self, intent: str = "") -> list[MemoryBlock]:
        """按意图组装记忆注入块（档案常驻 + 事件按需）。

        Args:
            intent: 用户意图/查询文本；命中地点/题材规则时追加事件块。

        Returns:
            非空注入块列表；无可用记忆时为空列表。
        """
        blocks: list[MemoryBlock] = []
        profile_text = self._profile.to_prompt_section()
        if profile_text:
            blocks.append(MemoryBlock("profile", profile_text))
        semantic_hits = self._semantic.match(intent)
        if semantic_hits:
            blocks.append(MemoryBlock("semantic", "\n".join(semantic_hits)))
        events = self.retrieve_events(intent)
        if events:
            blocks.append(MemoryBlock("events", to_prompt_section(events)))
        return blocks

    def retrieve_events(self, intent: str, limit: int = 3) -> list[EventRecord]:
        """按意图检索事件记忆（地点/题材规则命中才检索，不常驻）。

        Args:
            intent: 用户意图/查询文本。
            limit: 最多返回条数。

        Returns:
            命中事件列表；意图不含地点/题材时返回空列表。
        """
        if not intent:
            return []
        location = self._extract_location(intent)
        subject = _extract_subject(intent)
        if not location and not subject:
            return []
        return self._events.search_events(
            location=location,
            subject_type=subject,
            limit=limit,
        )

    def semantic_propose(self, content: str, keywords: list[str] | tuple[str, ...] | None = None) -> int:
        """暂存一条语义记忆候选（double-confirm 第一步）。

        Args:
            content: 结论文本。
            keywords: 命中关键词。

        Returns:
            候选 ID。
        """
        return self._semantic.staged_add(content, keywords)

    def semantic_confirm(self, candidate_id: int) -> bool:
        """确认候选并落盘（double-confirm 第二步）。

        Args:
            candidate_id: 候选 ID。

        Returns:
            是否确认成功。
        """
        return self._semantic.confirm(candidate_id)

    def sediment_semantics(
        self,
        *,
        min_samples: int = 3,
        min_success_rate: float = 0.7,
    ) -> list[SemanticCandidate]:
        """从事件记忆聚合提炼语义候选（E6-3）。

        成功率命中规则的结论进入语义存储的**待确认队列**（staged_add）；
        必须经 semantic_confirm 才写入 data/semantic.json 参与注入（double-confirm 防污染）。

        Args:
            min_samples: 最小样本数。
            min_success_rate: 最低成功率（0-1）。

        Returns:
            本次提炼出的候选列表（已入待确认队列）。
        """
        all_events = self._events.search_events(limit=500)
        candidates = extract_from_events(
            all_events,
            min_samples=min_samples,
            min_success_rate=min_success_rate,
        )
        for candidate in candidates:
            if not self._semantic.contains(candidate.content):
                self._semantic.staged_add(candidate.content, list(candidate.keywords))
        return candidates

    def sediment_favorite_spots(self, *, min_count: int = 2) -> list[dict[str, Any]]:
        """把高频常去机位（出现 ≥min_count 且带精确坐标）沉淀进档案 favorite_spots（E6-3）。

        数据前提：事件记忆含 coordinates（E3-2 已落地）；结果供 E9-2 就近推荐
        与管线按档案精确定位（根治「坐标写死上海」遗留）。

        Args:
            min_count: 同一地点最少出现次数。

        Returns:
            本次新增沉淀的机位列表（已写档）。
        """
        events = self._events.search_events(limit=500)
        grouped: dict[str, dict[str, Any]] = {}
        for event in events:
            location = event.location.strip()
            if not location:
                continue
            info = grouped.setdefault(location, {"count": 0, "coordinates": "", "subjects": set()})
            info["count"] += 1
            if event.coordinates:
                info["coordinates"] = event.coordinates
            if event.subject_type:
                info["subjects"].add(event.subject_type)
        existing = {str(spot.get("名称") or spot.get("name") or "") for spot in self._profile.favorite_spots}
        added: list[dict[str, Any]] = []
        for location, info in grouped.items():
            if info["count"] < min_count or not info["coordinates"]:
                continue
            if location in existing:
                continue
            latitude, longitude = _split_coordinates(str(info["coordinates"]))
            if latitude is None:
                continue
            spot = {
                "名称": location,
                "纬度": latitude,
                "经度": longitude,
                "题材": "、".join(sorted(info["subjects"]))[:40],
            }
            self._profile.favorite_spots.append(spot)
            added.append(spot)
        if added:
            self._profile.save(self._data_dir)
        return added

    def semantic_reject(self, candidate_id: int) -> bool:
        """否决候选，丢弃不落盘。

        Args:
            candidate_id: 候选 ID。

        Returns:
            是否丢弃成功。
        """
        return self._semantic.reject(candidate_id)

    # ------ 内部实现 ------
    def _extract_location(self, intent: str) -> str | None:
        """从意图中识别已知地点（档案常去机位命中才返回，避免误判）。"""
        for name in self._profile.common_locations:
            if name and name in intent:
                return name
        return None


# 题材关键词 → 规范题材名（意图检索用；命中首个即返回）
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
    """从意图中识别题材（关键词精确命中首个即返回规范名）。"""
    for keyword, canonical in _SUBJECT_KEYWORDS:
        if keyword in intent:
            return canonical
    return None


def _split_coordinates(value: str) -> tuple[float | None, float | None]:
    """把 "lat,lng" 解析为浮点对；非法返回 (None, None)。"""
    try:
        latitude_text, longitude_text = str(value).split(",")
        return float(latitude_text), float(longitude_text)
    except (ValueError, AttributeError):
        return None, None
