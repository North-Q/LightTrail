"""ReAct 循环：chat → tool_calls → dispatch → 回传，直至模型输出纯文本。

设计要点：
- 自 agent/core.py 原样迁出，职责收敛为「循环执行 + 工具结果回传」，
  不再关心上下文组装（context.py）与模型选择（llm/router.py）；
- LLM 并发策略由 ChatClient 的 serial_llm 开关负责（默认串行适配 ECNU，
  换 API 可关闭），本层不感知、不做假设；
- `max_tool_rounds` 防止模型陷入无限工具调用。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from lighttrail.agent.context import ContextBuilder
from lighttrail.agent.tools import ToolRegistry
from lighttrail.infra.trace import Recorder, null_trace
from lighttrail.llm.client import ChatClient

logger = logging.getLogger("lighttrail.agent")

# 单轮对话中允许的最大工具调用轮次（防止模型陷入无限循环）
MAX_TOOL_ROUNDS = 8


class ReActLoop:
    """多轮工具调用的执行循环。"""

    def __init__(
        self,
        client: ChatClient,
        registry: ToolRegistry,
        *,
        model: str | None = None,
        max_tool_rounds: int = MAX_TOOL_ROUNDS,
        context: ContextBuilder | None = None,
        recorder: Recorder | None = None,
    ) -> None:
        self._client = client
        self._registry = registry
        self._model = model
        self._max_tool_rounds = max_tool_rounds
        self._context = context or ContextBuilder()
        self._recorder: Recorder = recorder or null_trace

    def run(self, messages: list[dict[str, Any]], *, system_prompt: str | None = None) -> str:
        """执行一轮 ReAct 循环，就地追加 messages 历史。

        Args:
            messages: 会话消息历史（不含 system），助手与工具消息会追加到该列表。
            system_prompt: 兼容参数：传入时走旧拼接（ContextBuilder.build），
                缺省使用分层组装（ContextBuilder.to_openai_messages，E1-2 起默认路径）。

        Returns:
            最终纯文本回复。

        Raises:
            底层 LLMError / 工具异常原样上抛，由门面决定是否回滚历史。
        """
        tools = self._registry.to_openai_schema()
        for _ in range(self._max_tool_rounds):
            if system_prompt is None:
                request_messages = self._context.to_openai_messages(messages)
            else:
                request_messages = self._context.build(system_prompt, messages)
            started = time.perf_counter()
            resp = self._client.chat(
                request_messages,
                model=self._model,
                tools=tools,
            )
            self._recorder.record_llm(
                self._model or "default",
                prompt_summary=f"消息数 {len(request_messages)}",
                duration_s=time.perf_counter() - started,
            )
            messages.append(resp)

            tool_calls = resp.get("tool_calls")
            if not tool_calls:
                return resp.get("content", "").strip()

            self._execute_tool_calls(tool_calls, messages)

        logger.warning("工具调用超过 %d 轮，终止本轮对话", self._max_tool_rounds)
        return "（工具调用次数过多，本轮对话已终止。请简化问题或换一种问法。）"

    def _execute_tool_calls(
        self,
        tool_calls: list[dict[str, Any]],
        messages: list[dict[str, Any]],
    ) -> None:
        """逐条执行工具调用，并把结果以 tool 消息回传模型。"""
        for tc in tool_calls:
            func = tc["function"]
            result = self._registry.dispatch(func["name"], func.get("arguments", ""), recorder=self._recorder)
            logger.debug("工具 %s -> %s", func["name"], result[:200])
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                }
            )