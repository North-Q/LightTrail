"""装配根（B2-4，v4 §2.1 / D6）：全仓**唯一**的 new 点。

设计要点：
- 唯一职责是装配：Settings → 契约端口 → 领域工具 → 运行时/应用对象；不含业务判断、
  不含品牌字面量（品牌默认值在 config 默认值与 adapters/llm）；
- 允许依赖所有层（顶层例外），业务代码一律只依赖 contracts 端口；
- LLM 客户端实现位于 adapters，运行时统一使用 `runtime.AgentRuntime`；调用方（CLI / API）
  只调用本模块的装配函数。
- 参数一律显式传入（不读全局单例、不读环境变量），便于测试整体替换与依赖审计。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from lighttrail.adapters.llm.client import ChatClient
from lighttrail.adapters.llm.provider import ChatClientProvider
from lighttrail.adapters.llm.pydantic_bridge import LightTrailModel
from lighttrail.config import Settings, load_settings
from lighttrail.contracts.context import RequestContext
from lighttrail.contracts.tool import ToolContext
from lighttrail.domain.knowledge import FtsKnowledgeProvider
from lighttrail.infra.quota import QuotaLedger
from lighttrail.infra.trace import Recorder, TraceRecorder
from lighttrail.memory import MemoryManager
from lighttrail.orchestrator import Orchestrator
from lighttrail.runtime.agent import DEFAULT_MAX_TOOL_ROUNDS, AgentRuntime
from lighttrail.runtime.context import (
    LAYER_CONDUCT,
    LAYER_PROFILE,
    LAYER_ROLE,
    LAYER_TOOLS,
    LAYER_TRACE,
    ContextBuilder,
)
from lighttrail.runtime.registry import ToolRegistry
from lighttrail.tools import TOOLS, photo_analysis

__all__ = [
    "build_client",
    "build_context",
    "build_knowledge",
    "build_ledger",
    "build_memory",
    "build_orchestrator",
    "build_provider",
    "build_recorder",
    "build_registry",
    "build_runtime",
    "load_settings",
]


def build_registry(
    *,
    recorder: Recorder | None = None,
    spec_tools: tuple | None = None,
    photo_client_factory: Callable[[], ChatClient] | None = None,
) -> ToolRegistry:
    """构造工具注册表（声明式收集点注入；不再有全局单例）。

    Args:
        recorder: 可观测性记录器（缺省关闭态）。
        spec_tools: 工具清单覆盖（测试注入替身；缺省用领域收集点 TOOLS）。
        photo_client_factory: 照片工具的同步 LLM 客户端工厂；缺省按当前 Settings 构造。

    Returns:
        装配完成的工具注册表。
    """
    if spec_tools is not None:
        return ToolRegistry(spec_tools, recorder=recorder)
    factory = photo_client_factory or (lambda: build_client(load_settings()))
    return ToolRegistry((*TOOLS, *photo_analysis.build_tools(factory)), recorder=recorder)


def build_ledger(settings: Settings, *, now: float | None = None) -> QuotaLedger:
    """构造配额账本（按 settings 的水位阈值）。"""
    return QuotaLedger(warn_threshold=settings.quota_warn_threshold, now=now)


def build_client(settings: Settings, *, quota: QuotaLedger | None = None) -> ChatClient:
    """构造 LLM 客户端（适配层 async-first 实现的同步门面）。"""
    return ChatClient(
        settings.api_key,
        settings.base_url,
        concurrency=settings.concurrency,
        quota=quota or build_ledger(settings),
    )


def build_provider(settings: Settings, *, quota: QuotaLedger | None = None) -> ChatClientProvider:
    """构造 LLMProvider 端口实现。"""
    return ChatClientProvider(build_client(settings, quota=quota))


def build_runtime(
    provider: ChatClientProvider,
    registry: ToolRegistry,
    settings: Settings,
    *,
    recorder: Recorder | None = None,
    memory: MemoryManager | None = None,
    knowledge: FtsKnowledgeProvider | None = None,
) -> AgentRuntime:
    """构造 PydanticAI Agent runtime（B2-6；工具 schema 真源为 ToolSpec）。

    模型桥在本层构造（装配根可用 adapters），runtime 只接收 pydantic-ai Model，
    保持 `runtime → contracts` 的分层方向（lint-imports 强制）。
    """
    active_knowledge = knowledge or build_knowledge(settings)
    return AgentRuntime(
        LightTrailModel(provider, model_name=settings.model, recorder=recorder),
        registry,
        reason_model=LightTrailModel(provider, model_name=settings.model_reason, recorder=recorder),
        context=build_context(
            registry,
            settings=settings,
            recorder=recorder,
            memory=memory,
            knowledge=active_knowledge,
        ),
        recorder=recorder,
        max_tool_rounds=getattr(settings, "react_max_rounds", DEFAULT_MAX_TOOL_ROUNDS),
        reason_thinking=settings.reason_thinking,
        tool_context=ToolContext(
            request=RequestContext(),
            llm=provider,
            datasource=None,  # type: ignore[arg-type]
            memory=memory,
            knowledge=active_knowledge,
        ),
    )


def build_context(
    registry: ToolRegistry,
    *,
    settings: Settings | None = None,
    recorder: Recorder | None = None,
    memory: MemoryManager | None = None,
    knowledge: FtsKnowledgeProvider | None = None,
) -> ContextBuilder:
    """构造五层上下文组装器（③工具层由注册表自动生成；④档案记忆；⑤会话轨迹）。"""
    profile_provider = None
    if memory is not None:
        profile_provider = lambda: "\n".join(
            block.text for block in memory.build_injections(RequestContext(), "")
        )
    budgets = None
    if settings is not None and hasattr(settings, "context_role_budget"):
        budgets = {
            LAYER_ROLE: settings.context_role_budget,
            LAYER_CONDUCT: settings.context_conduct_budget,
            LAYER_TOOLS: settings.context_tools_budget,
            LAYER_PROFILE: settings.context_profile_budget,
            LAYER_TRACE: settings.context_trace_budget,
        }
    return ContextBuilder(
        registry=registry,
        profile_provider=profile_provider,
        knowledge_provider=knowledge,
        trace_provider=(lambda: recorder.to_prompt_section()) if recorder is not None else None,
        budgets=budgets,
    )


def build_memory(settings: Settings) -> MemoryManager:
    """构造记忆管理器（档案 / 事件 / 语义；B5 起按 user_id 命名空间隔离）。"""
    return MemoryManager(settings.data_dir)


def build_knowledge(settings: Settings) -> FtsKnowledgeProvider:
    """构造全局只读知识检索器（索引位于 data/knowledge/）。"""
    root = Path(settings.data_dir) / "knowledge"
    return FtsKnowledgeProvider(root / "knowledge.db", root / "seed" / "atmospheric-knowledge.json")


def build_recorder() -> TraceRecorder:
    """构造可观测性记录器（每请求/每会话一个，独立订阅）。"""
    return TraceRecorder()


def build_orchestrator(
    registry: ToolRegistry,
    runtime: AgentRuntime,
    *,
    memory: MemoryManager | None = None,
    recorder: Recorder | None = None,
) -> Orchestrator:
    """构造编排器（四管线 + 降级 ReAct 通道）。"""
    return Orchestrator(registry, runtime, memory=memory, recorder=recorder)
