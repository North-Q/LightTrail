"""记忆端口契约（B1-4，D10 / D13 / §2.3 / §7.1）。

设计要点：
- 记忆是 **per-user、可写** 的链路（与全局只读知识库分工见 D10）：所有读写都经
  `RequestContext.user_id` 隔离命名空间（`data/users/{user_id}/...`）；
- 契约层只定义端口与数据结构，不含存储实现：SQLite/JSON 实现在 domain 侧，
  未来多用户/多 worker 只需换实现，接口不变；
- 方法首参一律 `RequestContext`（§7.1 预留点①）：未来接认证后由中间件解析构造，
  业务层零改动。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from lighttrail.contracts.context import RequestContext


@dataclass(frozen=True)
class TokenBudget:
    """注入预算：按 token 上限裁剪记忆注入（防止记忆挤占决策上下文）。

    Attributes:
        max_tokens: 该段注入的总 token 上限。
        max_blocks: 最多注入块数（0 表示不限）。
    """

    max_tokens: int = 300
    max_blocks: int = 0


@dataclass(frozen=True)
class MemoryBlock:
    """一个记忆注入块（注入文本 + 来源标记，供可解释性展示）。

    Attributes:
        name: 块名（profile / events / semantic…）。
        text: 注入文本（空块由调用方丢弃）。
        source: 数据来源标记（本地档案 / 事件库 / 语义库…），供解释中心展示。
    """

    name: str
    text: str = ""
    source: str = ""


@dataclass(frozen=True)
class EventRecord:
    """一条拍摄事件记忆（可检索、可注入、可聚合）。

    Attributes:
        id: 存储自增 ID（未落库时为 0）。
        timestamp: 事件发生时间（ISO 8601）。
        location: 地点名称（如「临港海边」）。
        coordinates: 精确坐标（如 "31.23,121.47"，楼栋级；复拍对比用）。
        subject_type: 题材（日出 / 日落 / 火烧云 / 银河 / 星空…）。
        summary: 事件结论摘要。
        lesson: 经验教训（D4 复盘产出）。
        outcome: 结果（success / fail，空串未知）。
        tags: 标签列表。
        weather_snapshot: 当时的天气/天象快照（云量/火烧云评分/月相…）。
        equipment: 器材参数（后续可接 EXIF）。
    """

    id: int = 0
    timestamp: str = ""
    location: str = ""
    coordinates: str = ""
    subject_type: str = ""
    summary: str = ""
    lesson: str = ""
    outcome: str = ""
    tags: tuple[str, ...] = ()
    weather_snapshot: dict[str, Any] = field(default_factory=dict)
    equipment: str = ""


@runtime_checkable
class MemoryStore(Protocol):
    """记忆端口：per-user、可写（实现见 domain 侧 memory/）。"""

    def build_injections(
        self,
        ctx: RequestContext,
        intent: str = "",
        *,
        budget: TokenBudget | None = None,
    ) -> list[MemoryBlock]:
        """按意图产出注入块（档案常驻 + 事件检索 + 语义精选，按预算裁剪）。

        Args:
            ctx: 请求上下文（user_id 决定命名空间）。
            intent: 意图文本（题材等，用于命中规则）。
            budget: 注入预算；None 表示用实现默认值。

        Returns:
            记忆注入块列表（空块由实现丢弃）。
        """
        ...

    def write_event(self, ctx: RequestContext, event: EventRecord) -> int:
        """写入一条事件记忆。

        Args:
            ctx: 请求上下文（user_id 决定命名空间）。
            event: 事件记录（id 由实现分配）。

        Returns:
            新记录的自增 ID。
        """
        ...

    def propose_semantic(self, ctx: RequestContext, content: str, keywords: list[str]) -> int:
        """提交一条语义记忆候选（进候选池，须双确认后才注入）。

        Args:
            ctx: 请求上下文（user_id 决定命名空间）。
            content: 结论型经验文本。
            keywords: 命中关键词（题材等）。

        Returns:
            候选记录的自增 ID。
        """
        ...


__all__ = ["EventRecord", "MemoryBlock", "MemoryStore", "TokenBudget"]