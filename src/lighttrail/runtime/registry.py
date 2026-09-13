"""运行时工具注册表（B2-4，v4 §2.3）：声明式收集 + 注入式分发，无模块级单例。

设计要点：
- **控制反转归位**：注册表由装配根用工具实例构造（`ToolRegistry(TOOLS)`），工具模块不再
  反向 import 注册表（R1 的解药）；工具元数据（schema / 主字段 / 置信度 / 能力）全部来自
  各自的 `ToolSpec`，消灭 trace 手抄表、confidence 三集合、ContextBuilder 手写清单三处漂移；
- 只依赖 `contracts`：runtime → contracts 是合法方向（不 import domain / application）；
- `dispatch` 只认 Tool 端口：异常转结构化错误回传模型（模型可自修正重试）；
- 兼容入口：`register()` 保留给「动态注册的临时工具与测试替身」（领域工具一律走 ToolSpec）。

签名说明：v4 §2.3 写作 `ToolRegistry(specs)`，实现按「spec 挂在 Tool 上」调整为
`ToolRegistry(tools: Iterable[Tool])`——dispatch 需要实现体，只传 spec 无法调用。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterable
from typing import Any

from lighttrail.contracts.context import RequestContext
from lighttrail.contracts.tool import Tool, ToolContext, ToolSpec
from lighttrail.infra.confidence import resolve_confidence
from lighttrail.infra.trace import Recorder, null_trace

logger = logging.getLogger("lighttrail.runtime.registry")

# 工具名允许的字符集（OpenAI 要求 ^[a-zA-Z0-9_-]{1,64}$）
_ALLOWED_NAME_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


class ToolError(ValueError):
    """工具注册非法或执行失败（继承 ValueError：参数/命名非法语义，便于调用方捕获）。"""


class ToolRegistry:
    """声明式工具注册表：构造注入工具实例，分发时走 Tool 端口。

    Args:
        tools: 声明式工具（自带 ToolSpec）；缺省空表（测试可后续注册）。
        recorder: 可观测性记录器（缺省关闭态零开销）。
    """

    def __init__(self, tools: Iterable[Tool] = (), *, recorder: Recorder | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        self._specs: dict[str, ToolSpec] = {}
        self._dynamic: dict[str, dict[str, Any]] = {}
        self._recorder: Recorder = recorder or null_trace
        for tool in tools:
            self.register_tool(tool)

    # ------ 注册 ------
    def register_tool(self, tool: Tool) -> None:
        """注册声明式工具（ToolSpec + Tool 实现）。

        Args:
            tool: 满足 Tool 端口的工具（自带 spec）。

        Raises:
            ToolError: 工具名非法或重复。
        """
        spec = tool.spec
        self._validate_name(spec.name)
        if spec.name in self._tools or spec.name in self._dynamic:
            raise ToolError(f"工具已存在：{spec.name}")
        self._tools[spec.name] = tool
        self._specs[spec.name] = spec
        logger.debug("已注册工具：%s", spec.name)

    def register(
        self,
        func: Callable[..., Any],
        *,
        name: str | None = None,
        description: str = "",
        parameters: dict[str, Any] | None = None,
    ) -> Callable[..., Any]:
        """动态注册一个普通函数（测试替身 / 临时工具；领域工具走 ToolSpec）。

        Args:
            func: 工具实现函数。
            name: 工具名（缺省取函数名）。
            description: 面向模型的描述。
            parameters: 参数 JSON Schema（缺省无参数）。

        Returns:
            原函数（便于装饰器式使用）。

        Raises:
            ToolError: 工具名非法或重复。
        """
        tool_name = name or func.__name__
        self._validate_name(tool_name)
        if tool_name in self._tools or tool_name in self._dynamic:
            raise ToolError(f"工具已存在：{tool_name}")
        self._dynamic[tool_name] = {
            "func": func,
            "description": description,
            "parameters": parameters or {"type": "object", "properties": {}},
        }
        logger.debug("已动态注册工具：%s", tool_name)
        return func

    # ------ 查询 ------
    def names(self) -> list[str]:
        """已注册工具名（排序）。"""
        return sorted({*self._tools, *self._dynamic})

    def specs(self) -> list[ToolSpec]:
        """已注册声明式工具的 ToolSpec 列表（供能力叙述 / 路由 / 热插拔断言）。"""
        return [self._specs[name] for name in sorted(self._specs)]

    def to_openai_schema(self) -> list[dict[str, Any]]:
        """转换为 OpenAI tools 参数格式（声明式工具以 ToolSpec 为唯一真源）。"""
        schemas = [spec.to_openai_schema() for spec in self.specs()]
        schemas.extend(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": self._dynamic[name]["description"],
                    "parameters": self._dynamic[name]["parameters"],
                },
            }
            for name in sorted(self._dynamic)
        )
        return schemas

    def describe_tools(self) -> str:
        """生成工具清单文本（供 ContextBuilder 能力叙述自动生成）。"""
        return "\n".join(f"- {spec.name}：{spec.description.splitlines()[0]}" for spec in self.specs())

    # ------ 分发 ------
    def dispatch(
        self,
        name: str,
        arguments_json: str,
        ctx: ToolContext | None = None,
        *,
        recorder: Recorder | None = None,
    ) -> str:
        """按名称与 JSON 参数执行工具，返回可回传模型的字符串结果。

        Args:
            name: 工具名。
            arguments_json: 工具参数 JSON 字符串。
            ctx: 运行上下文（声明式工具需要；缺省用迁移期空上下文）。
            recorder: 本次调用的记录器覆盖（缺省用构造时注入的记录器）。

        Returns:
            工具结果 JSON 字符串；异常时返回 {"error": ...}，模型可据此修正。
        """
        active_recorder: Recorder = recorder or self._recorder
        started = time.perf_counter()
        main_field: str | None = None
        confidence: str | None = None
        if name in self._tools:
            spec = self._specs[name]
            try:
                arguments = json.loads(arguments_json) if arguments_json else {}
                if not isinstance(arguments, dict):
                    result: Any = {"error": "工具参数必须为 JSON 对象"}
                else:
                    tool_result = self._tools[name](ctx or _default_context(), **arguments)
                    result = tool_result.content
                    # 元数据单一真源：trace 主字段与置信度来自 ToolSpec
                    main_field = spec.main_field or None
                    confidence = resolve_confidence(spec, tool_result.data)
            except TypeError as exc:
                result = {"error": f"工具参数不合法：{exc}"}
            except Exception as exc:
                logger.exception("工具 %s 执行异常", name)
                result = {"error": f"工具执行失败：{exc}"}
        elif name in self._dynamic:
            try:
                arguments = json.loads(arguments_json) if arguments_json else {}
                if not isinstance(arguments, dict):
                    result = {"error": "工具参数必须为 JSON 对象"}
                else:
                    result = self._dynamic[name]["func"](**arguments)
            except TypeError as exc:
                result = {"error": f"工具参数不合法：{exc}"}
            except Exception as exc:
                logger.exception("工具 %s 执行异常", name)
                result = {"error": f"工具执行失败：{exc}"}
        else:
            result = {"error": f"未知工具：{name}，可用工具：{', '.join(self.names())}"}

        if isinstance(result, str):
            # 声明式工具已产出面向模型的字符串（ToolResult.content），不再二次编码
            result_text = result
        else:
            result_text = json.dumps(result, ensure_ascii=False, default=str) if not isinstance(result, str) else result
        active_recorder.record_tool(
            name,
            arguments_json,
            result_text,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            main_field=main_field,
            confidence=confidence,
        )
        return result_text

    # ------ 内部实现 ------
    @staticmethod
    def _validate_name(name: str) -> None:
        """校验工具名符合 OpenAI 命名约定。"""
        if not name or set(name) - _ALLOWED_NAME_CHARS:
            raise ToolError(f"工具名不合法（须为字母/数字/_/-）：{name!r}")


def _default_context() -> ToolContext:
    """迁移期默认上下文：为满足 Tool 端口签名而构造的空上下文。

    说明（TODO(B2-4+)）：装配根落地后由调用方显式传入 ctx；纯计算工具不使用上下文任何
    字段，智能工具的外部依赖在**构造时**注入（不经上下文），因此空上下文是安全的。
    """
    return ToolContext(request=RequestContext(), llm=None, datasource=None)  # type: ignore[arg-type]


__all__ = ["ToolError", "ToolRegistry"]