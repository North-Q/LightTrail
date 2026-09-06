"""工具注册表：定义、注册、分发模型可调用的工具。

设计要点：
- 通过装饰器把普通 Python 函数注册为模型工具；
- 自动生成 OpenAI 兼容的 tools schema（function calling 格式）；
- dispatch 统一把工具结果序列化为字符串回传模型；工具执行异常时
  返回结构化错误信息，让模型可以自行修正参数后重试。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

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
        return sorted(self._tools)

    def to_openai_schema(self) -> list[dict[str, Any]]:
        """转换为 OpenAI tools 参数格式（function calling）。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": meta["description"],
                    "parameters": meta["parameters"],
                },
            }
            for name, meta in sorted(self._tools.items())
        ]

    def dispatch(self, name: str, arguments_json: str, *, recorder: Recorder | None = None) -> str:
        """按名称与 JSON 参数执行工具，返回可回传模型的字符串结果。

        Args:
            name: 工具名。
            arguments_json: 工具参数的 JSON 字符串。
            recorder: 本次调用的可观测性记录器覆盖（缺省用构造时注入的记录器）。

        Returns:
            工具结果 JSON 字符串；异常时返回 {"error": ...}，模型可据此修正。
        """
        active_recorder: Recorder = recorder or self._recorder
        started = time.perf_counter()
        meta = self._tools.get(name)
        if meta is None:
            result = json.dumps({"error": f"未知工具：{name}，可用工具：{', '.join(self.names())}"}, ensure_ascii=False)
        else:
            try:
                arguments = json.loads(arguments_json) if arguments_json else {}
                if not isinstance(arguments, dict):
                    result = json.dumps({"error": "工具参数必须为 JSON 对象"}, ensure_ascii=False)
                else:
                    result = meta["func"](**arguments)
            except TypeError as exc:
                result = {"error": f"工具参数不合法：{exc}"}
            except Exception as exc:
                logger.exception("工具 %s 执行异常", name)
                result = {"error": f"工具执行失败：{exc}"}

        if isinstance(result, dict):
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
        )
        return result_text

    @staticmethod
    def _validate_name(name: str) -> None:
        if not name or set(name) - _ALLOWED_NAME_CHARS:
            raise ValueError(f"工具名不合法（须为字母/数字/_/-）：{name!r}")


# 全局单例：内置工具模块导入即完成注册
registry = ToolRegistry()
