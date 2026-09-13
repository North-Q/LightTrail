"""Agent runtime（B2-6）：PydanticAI 组装门面（ReAct 对话 / 意图解析 / reason 综合共用装配）。

设计要点（v4 §2.3 runtime/agent.py、D2）：
- **框架管通用机制**（循环 / 重试 / 用量上限 / DI），运行时只做装配：模型（自定义 Model 桥）、
  工具（ToolSpec → PydanticAI Tool）、指令（ContextBuilder 五层）、trace（桥记 LLM、注册表记工具）；
- **工具 schema 真源是 ToolSpec**：`Tool.from_schema(..., json_schema=spec.parameters)`，
  新增工具改一行收集点即可生效，本文件与任何提示词都不用手改（热插拔口径）；
- **依赖方向：`runtime → contracts`**——模型（自定义 Model 桥）与工具实例一律由装配根注入，
  本层不 import adapters（否则违反分层契约，由 lint-imports 强制）；
- 迁移期：`agent/core.py` 的旧 Agent 仍在生产路径上（B2-7 切换 + TestModel 用例 + shim 清理）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from functools import partial
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.models import Model, ModelSettings
from pydantic_ai.tools import Tool as PydanticTool
from pydantic_ai.usage import UsageLimits

from lighttrail.infra.trace import Recorder, TraceReport, null_trace
from lighttrail.runtime.context import ContextBuilder
from lighttrail.runtime.registry import ToolRegistry

logger = logging.getLogger("lighttrail.runtime.agent")

# 默认 ReAct 轮数上限（settings.REACT_MAX_ROUNDS，B1-5 落地为 12；原 8）
DEFAULT_MAX_TOOL_ROUNDS = 12
# 深推理通道默认温度（与旧 reason 通道一致）
_REASON_TEMPERATURE = 0.3
# 常规对话默认温度（工具链路用低值保证稳定）
_CHAT_TEMPERATURE = 0.2


class AgentRuntime:
    """PydanticAI Agent 组装门面：ReAct 对话与深推理共用一套装配。

    Args:
        model: 工具链路的 pydantic-ai 模型（由装配根用自定义 Model 桥构造）。
        registry: 工具注册表（声明式；其 ToolSpec 直接映射为框架工具 schema）。
        reason_model: 深推理模型（缺省复用 model）。
        context: 五层上下文组装器（缺省按注册表自动生成工具层）。
        recorder: 可观测性记录器（LLM 事件由桥记录，工具事件由注册表记录）。
        max_tool_rounds: ReAct 请求上限（映射为框架 UsageLimits.request_limit）。
        reason_thinking: 深推理通道是否携带 thinking 扩展参数（ADR-003）。
    """

    def __init__(
        self,
        model: Model,
        registry: ToolRegistry,
        *,
        reason_model: Model | None = None,
        context: ContextBuilder | None = None,
        recorder: Recorder | None = None,
        max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
        reason_thinking: bool = False,
    ) -> None:
        self._registry = registry
        self._recorder: Recorder = recorder or null_trace
        self._context = context or ContextBuilder(registry=registry)
        self._max_tool_rounds = max_tool_rounds
        self._reason_thinking = reason_thinking
        self._model = model
        self._model_reason = reason_model or model
        self._agent = Agent(self._model, tools=self._build_tools())
        self._chat_agent = Agent(self._model)  # 无工具的原始补全（意图解析等结构化输出）
        self._reason_agent = Agent(self._model_reason)
        self._messages: list[Any] = []

    # ------ 对外接口：ReAct 对话 ------
    async def arun(self, user_input: str) -> str:
        """异步执行一轮 ReAct 对话（框架负责循环与工具回传）。

        Args:
            user_input: 用户输入。

        Returns:
            最终纯文本回复。

        Raises:
            UsageLimitExceeded: 工具轮数超过 max_tool_rounds。
            ModelAPIError: 供应商调用失败。
        """
        result = await self._agent.run(
            user_input,
            instructions=self.system_prompt(),
            message_history=self._messages,
            usage_limits=UsageLimits(request_limit=self._max_tool_rounds),
        )
        self._messages = list(result.all_messages())
        return str(result.output)

    async def arun_with_trace(self, user_input: str) -> tuple[str, TraceReport]:
        """异步执行一轮 ReAct 对话，并返回「本轮」结构化报告（按事件游标切片）。

        Args:
            user_input: 用户输入。

        Returns:
            (最终文本, 本轮 TraceReport)。
        """
        cursor = self._recorder.cursor()
        text = await self.arun(user_input)
        return text, self._recorder.to_report(since=cursor)

    def run(self, user_input: str) -> str:
        """同步门面（供 CLI / 管线等同步调用方使用；事件循环内请 await arun）。

        Args:
            user_input: 用户输入。

        Returns:
            最终纯文本回复。

        Raises:
            RuntimeError: 在已运行的事件循环内调用（请改用 arun）。
        """
        return _run_sync(self.arun(user_input))

    def run_with_trace(self, user_input: str) -> tuple[str, TraceReport]:
        """同步门面：返回 (文本, 本轮报告)。"""
        return _run_sync(self.arun_with_trace(user_input))

    # ------ 对外接口：原始补全（结构化输出通道）------
    async def acomplete(
        self,
        prompt: str,
        *,
        system: str = "",
        temperature: float = _CHAT_TEMPERATURE,
    ) -> str:
        """用工具链路模型做一次无工具的原始补全（不走 ReAct 循环）。

        用途：意图解析等「强约束 JSON + 自愈」场景（D4 双轨边界：管线外复用自研
        `parse_with_retry`，框架侧只负责一次干净的补全）。

        Args:
            prompt: 用户侧提示（含 JSON 结构要求）。
            system: 系统指令（缺省用五层系统提示）。
            temperature: 采样温度。

        Returns:
            模型输出文本（未做结构校验，由调用方 parse_with_retry 处理）。
        """
        result = await self._chat_agent.run(
            prompt,
            instructions=system or self.system_prompt(),
            model_settings=ModelSettings(temperature=temperature),
        )
        return str(result.output).strip()

    def complete(
        self,
        prompt: str,
        *,
        system: str = "",
        temperature: float = _CHAT_TEMPERATURE,
    ) -> str:
        """同步门面：原始补全（事件循环内请 await acomplete）。"""
        return _run_sync(self.acomplete(prompt, system=system, temperature=temperature))

    # ------ 对外接口：深推理 ------
    async def areason(
        self,
        prompt: str,
        *,
        system: str = "",
        reasoning_effort: str | None = None,
        temperature: float = _REASON_TEMPERATURE,
    ) -> str:
        """异步执行深推理通道（不带工具，走深推理模型）。

        Args:
            prompt: 推理问题文本。
            system: 附加指令（缺省用五层系统提示）。
            reasoning_effort: 推理强度（仅当 reason_thinking 开启时透传）。
            temperature: 采样温度。

        Returns:
            纯文本推理结果。
        """
        extra_body: dict[str, Any] = {}
        if self._reason_thinking:
            extra_body["thinking"] = {"type": "enabled"}
            if reasoning_effort is not None:
                extra_body["reasoning_effort"] = reasoning_effort
        result = await self._reason_agent.run(
            prompt,
            instructions=system or self.system_prompt(),
            model_settings=ModelSettings(temperature=temperature, extra_body=extra_body or None),
        )
        return str(result.output).strip()

    def reason(
        self,
        prompt: str,
        *,
        system: str = "",
        reasoning_effort: str | None = None,
        temperature: float = _REASON_TEMPERATURE,
    ) -> str:
        """同步门面：深推理（事件循环内请 await areason）。"""
        return _run_sync(
            self.areason(prompt, system=system, reasoning_effort=reasoning_effort, temperature=temperature)
        )

    # ------ 对外接口：上下文与历史 ------
    def system_prompt(self) -> str:
        """当前五层系统提示（工具层由注册表自动生成，注册表变更即刷新）。"""
        return self._context.build_system_prompt()

    @property
    def messages(self) -> list[Any]:
        """当前框架消息历史（pydantic-ai ModelMessage 列表）。"""
        return list(self._messages)

    def load_messages(self, messages: list[Any]) -> None:
        """载入既有消息历史（会话恢复用；格式由调用方经桥转换）。

        Args:
            messages: pydantic-ai ModelMessage 列表。
        """
        self._messages = list(messages)

    def reset(self) -> None:
        """清空对话历史（保留装配）。"""
        self._messages = []

    @property
    def registry(self) -> ToolRegistry:
        """工具注册表（可观测性/测试断言用）。"""
        return self._registry

    # ------ 内部实现 ------
    def _build_tools(self) -> list[PydanticTool[Any]]:
        """把声明式工具映射为框架工具（schema 来自 ToolSpec，不经类型注解推断）。"""
        return [
            PydanticTool.from_schema(
                partial(self._dispatch, spec.name),
                name=spec.name,
                description=spec.description,
                json_schema=spec.parameters or {"type": "object", "properties": {}},
            )
            for spec in self._registry.specs()
        ]

    def _dispatch(self, name: str, **kwargs: Any) -> str:
        """工具调用落地：仍走注册表 dispatch（trace / 置信度 / 主字段口径不变）。"""
        return self._registry.dispatch(
            name,
            json.dumps(kwargs, ensure_ascii=False, default=str),
            recorder=self._recorder,
        )


def _run_sync(coro: Any) -> Any:
    """在无事件循环的调用方执行协程（Web 端在 worker 线程调用同样安全）。

    Args:
        coro: 待执行协程。

    Returns:
        协程结果。

    Raises:
        RuntimeError: 当前线程已有运行中的事件循环（应改用 async 接口）。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("AgentRuntime 的同步门面不能在事件循环内调用，请改用 arun/areason")


__all__ = ["DEFAULT_MAX_TOOL_ROUNDS", "AgentRuntime"]