"""契约层（零依赖）：跨层共享的类型与端口定义。

设计要点（架构 v4 §2.1 / §2.2 / §2.3，B1-1 起落地）：
- 本包**零依赖**：只允许 import 标准库与 pydantic。工具、编排、基础设施一律
  依赖契约，不再互相 import（R1/R2 的解药）；该约束由 import-linter 契约
  「契约层零依赖」强制（B1-5 起跑门禁）；
- 只放「跨层共享的类型与端口」，不放实现、不放业务规则：
  - tool.py：ToolSpec / ToolContext / Tool / Confidence / ToolResult（B2 消费）；
  - context.py：RequestContext（用户体系预留，贯穿 session/memory/quota/LLM 配置）；
  - llm.py：LLMConfig / LLMProvider / UserConfigProvider / KeyVault（B1-3）；
  - 其余端口：MemoryStore / KnowledgeProvider / DataSource / TraceSink（B1-4）；
  - models.py / plan.py / events.py：Intent / DecisionCard / Plan / TraceEvent（B1-2）；
- 迁移期兼容：旧路径（orchestrator.schemas、infra.trace）保留 re-export shim，
  shim 带 TODO 与删除批次（B5），到期不删即批次不通过。
"""

from __future__ import annotations

from lighttrail.contracts.context import RequestContext
from lighttrail.contracts.tool import Confidence, Tool, ToolContext, ToolResult, ToolSpec

__all__ = [
    "Confidence",
    "RequestContext",
    "Tool",
    "ToolContext",
    "ToolResult",
    "ToolSpec",
]