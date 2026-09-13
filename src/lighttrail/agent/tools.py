"""工具注册表的迁移期 shim（B2-4）：真源已迁至 `lighttrail.runtime.registry`。

TODO(B2-7): 删除本 shim——调用方改 `from lighttrail.runtime.registry import ToolRegistry`；
全局单例 `registry` 由装配根（`composition.py`）构造后显式注入。

为什么保留本文件（shim 纪律，v4 §2.2）：迁移期仍有调用方从 `lighttrail.agent.tools` 取
`ToolRegistry` / `registry` / `ToolError`（agent 内部、api、orchestrator、evals、测试）。
一次性改完会与 PydanticAI runtime 改造（B2-5/B2-6）叠加成不可审查的大 diff；
B2-7 收口时统一改指 runtime 并删除本文件。
"""

from __future__ import annotations

from lighttrail.runtime.registry import ToolError, ToolRegistry
from lighttrail.tools import TOOLS

# 迁移期全局单例（由声明式收集点构造；B2-7 删除，改由 composition 注入）
registry = ToolRegistry(TOOLS)

__all__ = ["TOOLS", "ToolError", "ToolRegistry", "registry"]