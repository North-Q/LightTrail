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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lighttrail.adapters.llm.client import ChatClient
from lighttrail.adapters.llm.provider import ChatClientProvider
from lighttrail.adapters.llm.pydantic_bridge import LightTrailModel
from lighttrail.adapters.maps import (
    AMapRoutingAdapter,
    BaiduRoutingAdapter,
    FallbackTravelTimeProvider,
)
from lighttrail.config import DEFAULT_PIPELINE_MAX_STEPS, PROJECT_ROOT, Settings, load_settings
from lighttrail.contracts.context import RequestContext
from lighttrail.contracts.knowledge import KnowledgeProvider
from lighttrail.contracts.plan import DEFAULT_PLAN_MAX_STEPS
from lighttrail.contracts.tool import ToolContext
from lighttrail.domain.chase import ChasePlanner, ManualTravelTimeProvider, TravelTimeProvider
from lighttrail.domain.knowledge import FtsKnowledgeProvider
from lighttrail.infra.quota import QuotaLedger
from lighttrail.infra.trace import Recorder, TraceRecorder
from lighttrail.llm.router import ModelRouter, RouteIntent
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
    "ApplicationServices",
    "RequestScope",
    "build_application",
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
    "build_travel_provider",
    "load_settings",
]


@dataclass(frozen=True)
class RequestScope:
    """一次请求的可变执行状态；共享服务不保存会话历史或请求身份。"""

    services: ApplicationServices
    request: RequestContext
    recorder: TraceRecorder
    tool_context: ToolContext
    runtime: AgentRuntime
    orchestrator: Orchestrator


@dataclass(frozen=True, repr=False)
class ApplicationServices:
    """应用级依赖快照；Web、CLI 和评估从此创建相同的请求作用域。

    配置包含全部资源上限，客户端与照片工具共用同一配额和限流器。
    记忆服务按请求身份选目录；模型桥、上下文与 trace 每次请求重新创建。
    """

    settings: Settings
    client: ChatClient
    provider: ChatClientProvider
    router: ModelRouter
    quota: QuotaLedger
    memory: MemoryManager
    knowledge: KnowledgeProvider
    registry: ToolRegistry
    chase_planner: ChasePlanner
    dispatch: Callable[[str, str], str] | None = field(default=None, repr=False)
    photo_analyze: Callable[[str, str], dict[str, Any]] | None = field(default=None, repr=False)
    photo_reverse: Callable[[str, str, str], dict[str, Any]] | None = field(
        default=None, repr=False
    )
    _owned_knowledge: FtsKnowledgeProvider | None = field(default=None, repr=False)

    def create_scope(
        self, request: RequestContext, *, recorder: TraceRecorder | None = None
    ) -> RequestScope:
        """装配一次请求，所有执行通道使用相同的身份、工具上下文和记录器。

        Args:
            request: 入口解析的身份与会话上下文。
            recorder: 可选的请求记录器，供 SSE 在执行前订阅。

        Returns:
            独立的请求作用域；不修改应用级共享依赖。
        """
        active_recorder = recorder if recorder is not None else build_recorder()
        tool_context = ToolContext(
            request=request,
            llm=self.provider,
            datasource=None,  # type: ignore[arg-type] - 数据源接入保持现有工具实现
            memory=self.memory,
            knowledge=self.knowledge,
            sink=active_recorder,
        )
        runtime = build_runtime(
            self.provider,
            self.registry,
            self.settings,
            recorder=active_recorder,
            memory=self.memory,
            knowledge=self.knowledge,
            router=self.router,
            tool_context=tool_context,
        )
        dispatch = None
        if self.dispatch is not None:
            raw_dispatch = self.dispatch

            def dispatch(name: str, args: str) -> str:
                """为显式注入的数据替身补充请求 trace。"""
                result = raw_dispatch(name, args)
                active_recorder.record_tool(name, args, result)
                return result

        orchestrator = build_orchestrator(
            self.registry,
            runtime,
            memory=self.memory,
            recorder=active_recorder,
            settings=self.settings,
            router=self.router,
            quota=self.quota,
            tool_context=tool_context,
            dispatch=dispatch,
            photo_analyze=self.photo_analyze,
            photo_reverse=self.photo_reverse,
        )
        return RequestScope(self, request, active_recorder, tool_context, runtime, orchestrator)

    def close(self) -> None:
        """释放本装配拥有的知识索引连接；调用方注入的知识服务由调用方管理。"""
        if self._owned_knowledge is not None:
            self._owned_knowledge.close()


def build_application(
    settings: Settings,
    *,
    client: ChatClient | None = None,
    router: ModelRouter | None = None,
    quota: QuotaLedger | None = None,
    memory: MemoryManager | None = None,
    knowledge: KnowledgeProvider | None = None,
    registry: ToolRegistry | None = None,
    travel_provider: TravelTimeProvider | None = None,
    dispatch: Callable[[str, str], str] | None = None,
    photo_analyze: Callable[[str, str], dict[str, Any]] | None = None,
    photo_reverse: Callable[[str, str, str], dict[str, Any]] | None = None,
) -> ApplicationServices:
    """构造所有入口共用的应用服务，替身只覆盖明确传入的依赖。

    Args:
        settings: 已解析的配置快照。
        client: 同步/异步 LLM 客户端替身；生产默认只创建一个客户端。
        router: 能力路由覆盖；默认按配置中的主模型和深推理模型构造。
        quota: 配额覆盖；默认复用注入客户端的账本或按配置新建。
        memory: 记忆服务覆盖。
        knowledge: 只读知识服务覆盖。
        registry: 工具注册表覆盖。
        travel_provider: 路线数据服务覆盖。
        dispatch: 管线采集替身；生产使用注册表与请求工具上下文。
        photo_analyze: 照片分析替身。
        photo_reverse: 照片反推替身。

    Returns:
        应用级服务；通过 create_scope 创建请求执行状态。
    """
    active_quota = quota or getattr(client, "quota", None) or build_ledger(settings)
    active_client = client if client is not None else build_client(settings, quota=active_quota)
    active_router = router or ModelRouter(
        default_model=settings.model, reason_model=settings.model_reason
    )
    owned_knowledge = build_knowledge(settings) if knowledge is None else None
    return ApplicationServices(
        settings=settings,
        client=active_client,
        provider=ChatClientProvider(active_client),
        router=active_router,
        quota=active_quota,
        memory=memory if memory is not None else build_memory(settings),
        knowledge=knowledge if knowledge is not None else owned_knowledge,
        registry=registry
        if registry is not None
        else build_registry(photo_client_factory=lambda: active_client, photo_router=active_router),
        chase_planner=ChasePlanner(travel_provider or build_travel_provider(settings)),
        dispatch=dispatch,
        photo_analyze=photo_analyze,
        photo_reverse=photo_reverse,
        _owned_knowledge=owned_knowledge,
    )


def build_registry(
    *,
    recorder: Recorder | None = None,
    spec_tools: tuple | None = None,
    photo_client_factory: Callable[[], ChatClient] | None = None,
    photo_router: ModelRouter | None = None,
) -> ToolRegistry:
    """构造工具注册表（声明式收集点注入；不再有全局单例）。

    Args:
        recorder: 可观测性记录器（缺省关闭态）。
        spec_tools: 工具清单覆盖（测试注入替身；缺省用领域收集点 TOOLS）。
        photo_client_factory: 照片工具的同步 LLM 客户端工厂；缺省按当前 Settings 构造。
        photo_router: 照片工具的能力路由器；生产与应用服务共用同一实例。

    Returns:
        装配完成的工具注册表。
    """
    if spec_tools is not None:
        return ToolRegistry(spec_tools, recorder=recorder)
    factory = photo_client_factory or (lambda: build_client(load_settings()))
    active_router = photo_router or ModelRouter()
    return ToolRegistry(
        (*TOOLS, *photo_analysis.build_tools(factory, active_router)), recorder=recorder
    )


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
    knowledge: KnowledgeProvider | None = None,
    router: ModelRouter | None = None,
    tool_context: ToolContext | None = None,
) -> AgentRuntime:
    """构造 PydanticAI Agent runtime（B2-6；工具 schema 真源为 ToolSpec）。

    模型桥在本层构造（装配根可用 adapters），runtime 只接收 pydantic-ai Model，
    保持 `runtime → contracts` 的分层方向（lint-imports 强制）。
    """
    active_knowledge = knowledge or build_knowledge(settings)
    active_router = router or ModelRouter(
        default_model=settings.model, reason_model=settings.model_reason
    )
    active_context = tool_context or ToolContext(
        request=RequestContext(),
        llm=provider,
        datasource=None,  # type: ignore[arg-type]
        memory=memory,
        knowledge=active_knowledge,
    )
    return AgentRuntime(
        LightTrailModel(
            provider, model_name=RouteIntent.TOOLS.resolve(active_router), recorder=recorder
        ),
        registry,
        reason_model=LightTrailModel(
            provider,
            model_name=RouteIntent.DEEP_REASONING.resolve(active_router),
            recorder=recorder,
        ),
        context=build_context(
            registry,
            settings=settings,
            recorder=recorder,
            memory=memory,
            knowledge=active_knowledge,
            request=active_context.request,
        ),
        recorder=recorder,
        max_tool_rounds=getattr(settings, "react_max_rounds", DEFAULT_MAX_TOOL_ROUNDS),
        reason_thinking=settings.reason_thinking,
        tool_context=active_context,
    )


def build_context(
    registry: ToolRegistry,
    *,
    settings: Settings | None = None,
    recorder: Recorder | None = None,
    memory: MemoryManager | None = None,
    knowledge: KnowledgeProvider | None = None,
    request: RequestContext | None = None,
) -> ContextBuilder:
    """按请求身份构造动态记忆、全局知识与工具轨迹上下文。"""
    memory_provider = None
    active_request = request if request is not None else RequestContext()
    if memory is not None:
        reported_memory: set[tuple[str, tuple[str, ...]]] = set()

        def memory_provider(query: str) -> str:
            """按本轮问题读取当前用户的档案、事件和语义记忆。"""
            blocks = memory.build_injections(active_request, query)
            names = tuple(block.name for block in blocks)
            marker = (query, names)
            if names and recorder is not None and marker not in reported_memory:
                recorder.record_step(
                    "记忆_注入",
                    input_summary=query[:80],
                    output_summary=f"users/{active_request.user_id}/memory/：{','.join(names)}",
                )
                reported_memory.add(marker)
            return "\n".join(block.text for block in blocks)

    def report_knowledge(query: str, sources: str) -> None:
        """只记录全局知识来源，不把知识内容复制到 trace。"""
        if recorder is not None:
            recorder.record_step(
                "知识_命中",
                input_summary=query[:80],
                output_summary=f"全局只读知识：{sources}"[:120],
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
        memory_provider=memory_provider,
        knowledge_provider=knowledge,
        knowledge_source_reporter=report_knowledge if knowledge is not None else None,
        trace_provider=(lambda: recorder.to_prompt_section()) if recorder is not None else None,
        budgets=budgets,
    )


def build_memory(settings: Settings) -> MemoryManager:
    """构造记忆管理器（档案 / 事件 / 语义；B5 起按 user_id 命名空间隔离）。"""
    return MemoryManager(settings.data_dir)


def build_travel_provider(settings: Settings) -> TravelTimeProvider:
    """构造地图路线服务；未配置高德 Key 时保持本地保守估算可用。"""
    if settings.map_primary_provider == "amap" and settings.has_amap_web_service_key:
        primary = AMapRoutingAdapter(settings.amap_web_service_key)
        if settings.has_baidu_map_ak:
            secondary: TravelTimeProvider = FallbackTravelTimeProvider(
                BaiduRoutingAdapter(settings.baidu_map_ak), ManualTravelTimeProvider()
            )
        else:
            secondary = ManualTravelTimeProvider()
        return FallbackTravelTimeProvider(primary, secondary)
    return ManualTravelTimeProvider()


def build_knowledge(settings: Settings) -> FtsKnowledgeProvider:
    """构造全局只读知识检索器（索引位于 data/knowledge/）。"""
    root = Path(settings.data_dir) / "knowledge"
    root.mkdir(parents=True, exist_ok=True)
    seed = root / "seed" / "atmospheric-knowledge.json"
    if not seed.exists():
        seed = PROJECT_ROOT / "data" / "knowledge" / "seed" / "atmospheric-knowledge.json"
    return FtsKnowledgeProvider(root / "knowledge.db", seed)


def build_recorder() -> TraceRecorder:
    """构造可观测性记录器（每请求/每会话一个，独立订阅）。"""
    return TraceRecorder()


def build_orchestrator(
    registry: ToolRegistry,
    runtime: AgentRuntime,
    *,
    memory: MemoryManager | None = None,
    recorder: Recorder | None = None,
    settings: Settings | None = None,
    router: ModelRouter | None = None,
    quota: QuotaLedger | None = None,
    tool_context: ToolContext | None = None,
    dispatch: Callable[[str, str], str] | None = None,
    photo_analyze: Callable[[str, str], dict[str, Any]] | None = None,
    photo_reverse: Callable[[str, str, str], dict[str, Any]] | None = None,
) -> Orchestrator:
    """构造编排器（四管线 + 降级 ReAct 通道）。"""
    threshold = settings.routing_confidence_threshold if settings is not None else 0.6
    return Orchestrator(
        registry,
        runtime,
        memory=memory,
        recorder=recorder,
        routing_confidence_threshold=threshold,
        router=router,
        quota=quota,
        tool_context=tool_context,
        dispatch=dispatch,
        photo_analyze=photo_analyze,
        photo_reverse=photo_reverse,
        plan_max_steps=settings.plan_max_steps if settings is not None else DEFAULT_PLAN_MAX_STEPS,
        pipeline_max_steps=(
            settings.pipeline_max_steps if settings is not None else DEFAULT_PIPELINE_MAX_STEPS
        ),
    )
