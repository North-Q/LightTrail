"""内置工具包：声明式工具的唯一收集点（B2 起）。

设计要点（v4 §2.4 工具热插拔）：
- 每个工具模块声明 `TOOLS: tuple[Tool, ...]`（ToolSpec 自描述 + 实现）；本包把它们
  汇成单一 `TOOLS`——**新增工具 = 在此追加一行**；
- 本包只依赖 `contracts/`（领域层纪律）；工具元数据（schema / 主字段 / 置信度 / 能力）
  不再手抄到 trace / confidence / ContextBuilder 三处；
- B2-4 起本包不再向任何全局注册表自注册：装配根（composition.py）用
  `ToolRegistry(TOOLS)` 构造实例并注入；旧全局单例只作为迁移期 shim 存在于 agent/tools.py。
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

# 声明式工具清单：15 个工具全部声明式（新增工具 = 在此追加一行）
# 智能工具（记忆检索 / 照片分析 / 照片反推）的依赖经工厂注入，无模块级可写全局
TOOLS: tuple[Tool, ...] = (
    *basic.TOOLS,
    *exposure.TOOLS,
    *astronomy.TOOLS,
    *weather.TOOLS,
    *site_match.TOOLS,
    *memory_tool.build_tools(memory_tool.default_store_factory),
    *photo_analysis.build_tools(photo_analysis.default_client_factory),
)

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