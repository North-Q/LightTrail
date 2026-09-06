"""管线上下文（E5-1）：一条管线执行期间的共享数据对象。

存放意图、采集的数据集、代码化评分与最终卡片；各管线步骤按
「数据采集 → 评分 → 综合 → 卡片」顺序填充，TraceRecorder 记录每步。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lighttrail.orchestrator.schemas import DecisionCard, Intent


@dataclass
class PipelineContext:
    """一条管线执行的数据载体。

    Attributes:
        user_request: 用户原始请求。
        intent: 规范化意图（意图理解产出）。
        data: 数据集 {工具名: 解析后的结果 dict}。
        scores: 代码化评分 {字段: 值}（确定性规则，不让模型自评）。
        card: 最终决策卡片（reason 综合产出）。
    """

    user_request: str = ""
    intent: Intent | None = None
    data: dict[str, Any] = field(default_factory=dict)
    scores: dict[str, Any] = field(default_factory=dict)
    card: DecisionCard | None = None
