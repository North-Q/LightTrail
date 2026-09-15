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

# 基础声明式工具清单。照片工具需要同步 LLM 端口，只能由 composition 注入后追加，
# 避免领域层自行构造适配器客户端。
TOOLS: tuple[Tool, ...] = (
    *basic.TOOLS,
    *exposure.TOOLS,
    *astronomy.TOOLS,
    *weather.TOOLS,
    *site_match.TOOLS,
    *memory_tool.build_tools(memory_tool.default_store_factory),
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
