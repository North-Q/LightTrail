"""工具注册表：定义、注册、分发模型可调用的工具（B2 起为迁移期实现）。

设计要点：
- B2 目标形态：`runtime/registry.py` 的 `ToolRegistry(specs)`，由装配根用声明式
  ToolSpec 列表构造，工具元数据（schema / 主字段 / 置信度 / 能力）全部来自 ToolSpec；
- 本文件当前是**迁移期实现**：同时支持声明式工具（`register_tool(Tool)`，主路径）
  与旧的装饰器注册（`register(func, ...)`，B2-2/B2-3 逐个改造后删除）；
- TODO(B2-4): 迁移完成后本模块转 re-export shim，全局单例 `registry` 由装配根取代。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

from lighttrail.contracts.context import RequestContext
from lighttrail.contracts.tool import Tool, ToolContext, ToolSpec
from lighttrail.infra.confidence import resolve_confidence
from lighttrail.infra.trace import Recorder, null_trace

logger = logging.getLogger("lighttrail.tools")

# 工具名允许的字符集（OpenAI 要求 ^[a-zA-Z0-9_-]{1,64}$）
_ALLOWED_NAME_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


class ToolError(Exception):
    """工具执行失败。"""


class ToolRegistry:
    """线程安全的工具注册表（当前为全局单例）。

    Args:
        recorder: 可观测性记录器（E2-1），dispatch 执行工具时写入 tool 事件；
            缺省使用关闭态 null_trace（零开销，行为与未接入一致）。
    """

    def __init__(self, *, recorder: Recorder | None = None) -> None:
        self._tools: dict[str, dict[str, Any]] = {}
        self._recorder: Recorder = recorder or null_trace

    # ------------------------------------------------------------------
    # 注册
    # ------------------------------------------------------------------
    def register_tool(self, tool: Tool) -> None:
        """注册声明式工具（ToolSpec + Tool 实现）——B2 起的主路径。

        Args:
            tool: 满足 Tool 端口的工具（自带 spec）。

        Raises:
            ValueError: 工具名非法或重复。
        """
        spec = tool.spec
        self._validate_name(spec.name)
        if spec.name in self._tools:
            raise ValueError(f"工具已存在：{spec.name}")
        self._tools[spec.name] = {"tool": tool, "spec": spec}
        logger.debug("已注册声明式工具：%s", spec.name)

    def register(
        self,
        func: Callable[..., Any],
        *,
        name: str | None = None,
        description: str = "",
        parameters: dict[str, Any] | None = None,
    ) -> Callable[..., Any]:
        """注册一个工具函数。

        Args:
            func: 工具实现函数。
            name: 工具名，缺省取函数名。
            description: 工具用途描述（模型据此选择工具，须写清楚）。
            parameters: JSON Schema 格式的参数定义（OpenAI function calling 格式），
                缺省视为无参数。
        """
        tool_name = name or func.__name__
        self._validate_name(tool_name)
        if tool_name in self._tools:
            raise ValueError(f"工具已存在：{tool_name}")
        self._tools[tool_name] = {
            "func": func,
            "description": description,
            "parameters": parameters or {"type": "object", "properties": {}},
        }
        logger.debug("已注册工具：%s", tool_name)
        return func

    def tool(
        self,
        name: str | None = None,
        description: str = "",
        parameters: dict[str, Any] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """装饰器形式的注册入口，供工具模块声明式使用。"""

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            return self.register(func, name=name, description=description, parameters=parameters)

        return decorator

    # ------------------------------------------------------------------
    # 查询与分发
    # ------------------------------------------------------------------
    def names(self) -> list[str]:
        """已注册工具名（排序）。"""
        return sorted(self._tools)

    def specs(self) -> list[ToolSpec]:
        """已注册声明式工具的 ToolSpec 列表（供能力叙述 / 路由 / 热插拔断言）。"""
        return [meta["spec"] for meta in self._tools.values() if meta.get("spec") is not None]

    def to_openai_schema(self) -> list[dict[str, Any]]:
        """转换为 OpenAI tools 参数格式（声明式工具走 ToolSpec.to_openai_schema）。"""
        schemas: list[dict[str, Any]] = []
        for name, meta in sorted(self._tools.items()):
            spec: ToolSpec | None = meta.get("spec")
            if spec is not None:
                schemas.append(spec.to_openai_schema())
                continue
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": meta["description"],
                        "parameters": meta["parameters"],
                    },
                }
            )
        return schemas

    def dispatch(
        self,
        name: str,
        arguments_json: str,
        *,
        recorder: Recorder | None = None,
        ctx: ToolContext | None = None,
    ) -> str:
        """按名称与 JSON 参数执行工具，返回可回传模型的字符串结果。

        Args:
            name: 工具名。
            arguments_json: 工具参数的 JSON 字符串。
            recorder: 本次调用的可观测性记录器覆盖（缺省用构造时注入的记录器）。
            ctx: 运行上下文（声明式工具需要；缺省用迁移期空上下文）。

        Returns:
            工具结果 JSON 字符串；异常时返回 {"error": ...}，模型可据此修正。
        """
        active_recorder: Recorder = recorder or self._recorder
        started = time.perf_counter()
        meta = self._tools.get(name)
        main_field: str | None = None
        confidence: str | None = None
        if meta is None:
            result = json.dumps({"error": f"未知工具：{name}，可用工具：{', '.join(self.names())}"}, ensure_ascii=False)
        else:
            spec: ToolSpec | None = meta.get("spec")
            try:
                arguments = json.loads(arguments_json) if arguments_json else {}
                if not isinstance(arguments, dict):
                    result = json.dumps({"error": "工具参数必须为 JSON 对象"}, ensure_ascii=False)
                elif spec is not None:
                    tool_result = meta["tool"](ctx or _default_context(), **arguments)
                    result = tool_result.content
                    # 元数据单一真源：trace 主字段与置信度来自 ToolSpec（不再查手抄表）
                    main_field = spec.main_field or None
                    confidence = resolve_confidence(spec, tool_result.data)
                else:
                    result = meta["func"](**arguments)
            except TypeError as exc:
                result = {"error": f"工具参数不合法：{exc}"}
            except Exception as exc:
                logger.exception("工具 %s 执行异常", name)
                result = {"error": f"工具执行失败：{exc}"}

        if isinstance(result, str):
            # 声明式工具已产出面向模型的字符串（ToolResult.content），不再二次编码
            result_text = result
        elif isinstance(result, dict):
            result_text = json.dumps(result, ensure_ascii=False, default=str)
        else:
            try:
                result_text = json.dumps(result, ensure_ascii=False, default=str)
            except TypeError:
                result_text = str(result)
        active_recorder.record_tool(
            name,
            arguments_json,
            result_text,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            main_field=main_field,
            confidence=confidence,
        )
        return result_text

    @staticmethod
    def _validate_name(name: str) -> None:
        if not name or set(name) - _ALLOWED_NAME_CHARS:
            raise ValueError(f"工具名不合法（须为字母/数字/_/-）：{name!r}")


def _default_context() -> ToolContext:
    """迁移期默认上下文：为满足 Tool 端口签名而构造的空上下文。

    说明（TODO(B2-4)）：装配根落地后由调用方显式传入 ctx；纯计算工具不使用上下文任何
    字段，智能工具的外部依赖在**构造时**注入（不经上下文），因此空上下文是安全的。
    """
    return ToolContext(request=RequestContext(), llm=None, datasource=None)  # type: ignore[arg-type]


# 全局单例：B2-4 装配根落地前的迁移期入口
registry = ToolRegistry()
