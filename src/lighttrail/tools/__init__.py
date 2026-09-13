"""内置工具包：声明式工具的唯一收集点（B2 起）。

设计要点（v4 §2.4 工具热插拔）：
- 每个工具模块声明 `TOOLS: tuple[Tool, ...]`（ToolSpec 自描述 + 实现）；本包把它们
  汇成单一 `TOOLS`——**新增工具 = 在此追加一行**；
- 本包只依赖 `contracts/`（领域层纪律）；工具元数据（schema / 主字段 / 置信度 / 能力）
  不再手抄到 trace / confidence / ContextBuilder 三处。
"""

from __future__ import annotations

from lighttrail.contracts.tool import Tool
from lighttrail.tools import (
    astronomy,
    basic,
    exposure,
    memory_tool,
    photo_analysis,
    site_match,
    weather,
)

# 声明式工具清单（主路径逐步接管；B2-1 起 basic / exposure 已是声明式）
TOOLS: tuple[Tool, ...] = (
    *basic.TOOLS,
    *exposure.TOOLS,
)

# TODO(B2-4): 迁移期把声明式工具挂到旧全局注册表，供既有调用方（cli / tests）使用；
# 装配根 composition.py 落地后由它构造 ToolRegistry(TOOLS)，本段删除。
_registry = __import__("lighttrail.agent.tools", fromlist=["registry"]).registry
for _tool in TOOLS:
    _registry.register_tool(_tool)

__all__ = [
    "TOOLS",
    "astronomy",
    "basic",
    "exposure",
    "memory_tool",
    "photo_analysis",
    "site_match",
    "weather",
]