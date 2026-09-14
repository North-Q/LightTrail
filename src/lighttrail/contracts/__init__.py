"""契约层（零依赖）：跨层共享的类型与端口定义。

设计要点（架构 v4 §2.1 / §2.2 / §2.3，B1 起落地）：
- 本包**零依赖**：只允许 import 标准库与 pydantic。工具、编排、基础设施一律
  依赖契约，不再互相 import（R1/R2 的解药）；该约束由 import-linter 契约
  「契约层零依赖」强制（B1-5 起跑门禁）；
- 只放「跨层共享的类型与端口」，不放实现、不放业务规则：
  - tool.py：ToolSpec / ToolContext / Tool / Confidence / ToolResult；
  - context.py：RequestContext（用户体系预留，贯穿 session/memory/quota/LLM 配置）；
  - llm.py：LLMConfig / LLMProvider / UserConfigProvider / KeyVault；
  - models.py / plan.py / events.py：Intent / DecisionCard / Plan / TraceEvent / SSEEvent；
  - memory.py / knowledge.py / datasource.py / observability.py：其余端口；
- 分层纪律：`contracts → ∅`（零依赖），`domain/adapters → contracts`，反向依赖一律违规。
- 迁移期兼容：旧路径（orchestrator.schemas、infra.trace）保留 re-export shim，
  shim 带 TODO 与删除批次（B5），到期不删即批次不通过。
"""

from __future__ import annotations

from lighttrail.contracts.context import RequestContext
from lighttrail.contracts.datasource import DataSource
from lighttrail.contracts.events import (
    KIND_LLM,
    KIND_STEP,
    KIND_TOOL,
    SSEEvent,
    SSEEventPayload,
    TraceEvent,
)
from lighttrail.contracts.knowledge import KnowledgeChunk, KnowledgeProvider
from lighttrail.contracts.llm import (
    DEFAULT_CONCURRENCY,
    KeyVault,
    LLMConfig,
    LLMProvider,
    UserConfigProvider,
)
from lighttrail.contracts.memory import EventRecord, MemoryBlock, MemoryStore, TokenBudget
from lighttrail.contracts.models import (
    ConfidenceDetail,
    DecisionCard,
    Intent,
    LocationSuggestion,
    ParamSuggestion,
    PhotoAnalysisReport,
    PhotoReverseReport,
    Source,
)
from lighttrail.contracts.observability import TraceSink
from lighttrail.contracts.plan import DEFAULT_PLAN_MAX_STEPS, Plan, PlanStep
from lighttrail.contracts.tool import Confidence, Tool, ToolContext, ToolResult, ToolSpec

__all__ = [
    "DEFAULT_CONCURRENCY",
    "DEFAULT_PLAN_MAX_STEPS",
    "KIND_LLM",
    "KIND_STEP",
    "KIND_TOOL",
    "Confidence",
    "ConfidenceDetail",
    "DataSource",
    "DecisionCard",
    "EventRecord",
    "Intent",
    "KeyVault",
    "KnowledgeChunk",
    "KnowledgeProvider",
    "LLMConfig",
    "LLMProvider",
    "LocationSuggestion",
    "MemoryBlock",
    "MemoryStore",
    "ParamSuggestion",
    "PhotoAnalysisReport",
    "PhotoReverseReport",
    "Plan",
    "PlanStep",
    "RequestContext",
    "SSEEvent",
    "SSEEventPayload",
    "Source",
    "TokenBudget",
    "Tool",
    "ToolContext",
    "ToolResult",
    "ToolSpec",
    "TraceEvent",
    "TraceSink",
    "UserConfigProvider",
]