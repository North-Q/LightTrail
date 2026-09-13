"""工具契约：ToolSpec / ToolContext / Tool / Confidence / ToolResult（B1-1，D9 / §2.3）。

设计要点：
- `ToolSpec` 是工具的**完整自描述**：注册 schema、trace 主字段（main_field）、置信度
  规则（confidence）、能力声明（capabilities）全部来源于它——消灭现状三处漂移源
  （`infra/trace._MAIN_FIELD` 手抄表、`infra/confidence` 三个集合、
  `agent/context.DEFAULT_ROLE_PROMPT` 手写工具清单）；
- `ToolContext` 是工具的**注入式运行上下文**：替代模块级可写全局与 setter 注入
  （服务定位器模式），工具不再自建客户端、不再反向 import 注册表（R1 的解药）；
- 本模块只依赖标准库与契约层内部类型：禁止 import domain / runtime / application /
  adapters / interface（由 import-linter 强制）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from lighttrail.contracts.context import RequestContext

if TYPE_CHECKING:  # 仅类型注解用：运行时不 import（B1-3 / B1-4 落地实现侧协议）
    from lighttrail.contracts.datasource import DataSource
    from lighttrail.contracts.llm import LLMProvider
    from lighttrail.contracts.memory import MemoryStore
    from lighttrail.contracts.observability import TraceSink


class Confidence(str, Enum):
    """工具来源的置信度档位（确定性规则产出，不做模型自评）。

    注：用 `str, Enum` 而非 `enum.StrEnum`，保持项目 py310+ 兼容（StrEnum 需 3.11）。
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class ToolSpec:
    """工具的完整自描述（注册 / 能力叙述 / trace / 置信度四处共用）。

    Attributes:
        name: 工具名（OpenAI function name，全局唯一）。
        description: 面向模型的描述（含「何时使用」，见 §4 工具描述三法则）。
        parameters: 参数 JSON Schema（object 类型）。
        capabilities: 能力声明（如 {"tools"}），供 ModelRouter 按需路由。
        main_field: 该工具进入 TraceReport.sources 的主字段名（报告中文字段）。
        confidence: 该工具来源的固定置信度（规则表来源，见 infra/confidence.py 的对齐）。
    """

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    capabilities: frozenset[str] = frozenset()
    main_field: str = ""
    confidence: Confidence = Confidence.LOW

    def to_openai_schema(self) -> dict[str, Any]:
        """转成 OpenAI function calling 的 tool 定义。

        Returns:
            {"type": "function", "function": {name, description, parameters}}。
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True)
class ToolResult:
    """工具返回载体（B2 起 dispatch 统一返回它，替代裸 JSON 字符串）。

    Attributes:
        content: 面向模型的文本/JSON 字符串（进 messages 的部分）。
        data: 结构化结果（供 trace 置信度判定与主字段派生；空 dict 表示无结构体）。
    """

    content: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolContext:
    """工具运行上下文（注入式，替代模块级全局与服务定位器）。

    Attributes:
        request: 本次请求的身份与配置上下文。
        llm: LLM 端口（智能工具的多模态调用走它，不再自建客户端）。
        datasource: 数据源端口（天气/地图等外部数据统一出口）。
        memory: 记忆端口（per-user；无记忆场景为 None）。
        sink: 观测端口（trace 事件出口；None 表示关闭态，零开销）。
    """

    request: RequestContext
    llm: LLMProvider
    datasource: DataSource
    memory: MemoryStore | None = None
    sink: TraceSink | None = None

    def emit(self, kind: str, name: str, **payload: Any) -> None:
        """发一条观测事件（无 sink 时静默丢弃；sink 异常不打断工具执行）。

        Args:
            kind: 事件类型（llm / tool / step，与 infra.trace 的 KIND_* 对齐）。
            name: 事件名（工具名 / 步骤名）。
            **payload: 事件负载（字段名与既有 trace 事件保持一致）。
        """
        if self.sink is None:
            return
        # 延迟 import：events 与 tool 同属契约层，此处避免模块级循环引用
        from lighttrail.contracts.events import TraceEvent

        self.sink.emit(TraceEvent(kind=kind, name=name, payload=dict(payload)))


@runtime_checkable
class Tool(Protocol):
    """工具端口：自带 ToolSpec，调用签名统一吃 ToolContext。

    说明：B1 只定义端口（不实现）；B2 起领域工具改为声明式 ToolSpec +
    `__call__(ctx, **kwargs)`，由装配根收集构造注册表（控制反转归位）。
    """

    spec: ToolSpec

    def __call__(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        """执行工具。

        Args:
            ctx: 注入式运行上下文。
            **kwargs: 工具参数（由 JSON Schema 校验后的实参）。

        Returns:
            工具返回载体。
        """
        ...


__all__ = [
    "Confidence",
    "Tool",
    "ToolContext",
    "ToolResult",
    "ToolSpec",
]