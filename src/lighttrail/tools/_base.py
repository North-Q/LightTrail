"""工具适配基件：把无状态函数包装成声明式 Tool（域内通用件）。

设计要点（B2：工具热插拔）：
- 领域工具一律「ToolSpec 自描述 + 纯函数实现」；本模块只做适配，不含领域逻辑；
- 纯计算工具不需要运行上下文（ctx 未用），依赖外部能力的工具（智能工具）自行在
  构造时注入依赖，避免模块级可写全局与服务定位器（R1/R2 的解药）。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from lighttrail.contracts.tool import ToolContext, ToolResult, ToolSpec


@dataclass(frozen=True)
class PureTool:
    """纯计算工具适配器：ToolSpec + 无状态领域函数。

    Attributes:
        spec: 工具自描述（注册 schema / trace 主字段 / 置信度 / 能力声明）。
        func: 领域实现（关键字参数；返回 dict 或任意可 JSON 序列化对象）。
    """

    spec: ToolSpec
    func: Callable[..., Any]

    def __call__(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        """执行工具（纯计算工具不使用 ctx）。

        Args:
            ctx: 运行上下文（纯计算工具忽略；保持 Tool 端口签名一致）。
            **kwargs: 工具参数。

        Returns:
            工具返回载体（content 为 JSON 字符串，data 为结构化结果）。
        """
        data = self.func(**kwargs)
        if isinstance(data, ToolResult):
            return data
        if isinstance(data, dict):
            return ToolResult(
                content=json.dumps(data, ensure_ascii=False, default=str),
                data=data,
            )
        return ToolResult(content=json.dumps(data, ensure_ascii=False, default=str))


__all__ = ["PureTool"]