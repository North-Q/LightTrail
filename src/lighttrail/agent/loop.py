"""ReAct 循环：chat → tool_calls → dispatch → 回传，直至模型输出纯文本。

设计要点：
- 自 agent/core.py 原样迁出，职责收敛为「循环执行 + 工具结果回传」，
  不再关心上下文组装（context.py）与模型选择（llm/router.py）；
- LLM 并发策略由 ChatClient 负责（迁移期仍由 serial_llm 开关驱动，其取值由
  settings.concurrency == 1 派生；B3 换成 async-first 单 Semaphore 后本层仍不感知）；
- `max_tool_rounds` 防止模型陷入无限工具调用。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from lighttrail.agent.context import ContextBuilder
from lighttrail.agent.tools import ToolRegistry
from lighttrail.infra.trace import Recorder, null_trace
from lighttrail.llm.client import ChatClient, UsageStats
from lighttrail.llm.router import ModelRouter, RouteIntent

logger = logging.getLogger("lighttrail.agent")

# 单轮对话中允许的最大工具调用轮次（防止模型陷入无限循环）
MAX_TOOL_ROUNDS = 8


def _usage_token_fields(stats: UsageStats | None) -> dict[str, int | None]:
    """把 usage 折算为 record_llm 的 token 参数（供应商未返回 usage 时三项全 None）。

    Args:
        stats: ChatClient 回传的用量；未回调时为 None。

    Returns:
        可直接展开进 record_llm 的关键字参数（tokens / prompt_tokens / completion_tokens）。
    """
    if stats is None:
        return {"tokens": None, "prompt_tokens": None, "completion_tokens": None}
    return {
        "tokens": stats.total_tokens,
        "prompt_tokens": stats.prompt_tokens,
        "completion_tokens": stats.completion_tokens,
    }


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
        router: ModelRouter | None = None,
    ) -> None:
        self._client = client
        self._registry = registry
        self._model = model
        self._max_tool_rounds = max_tool_rounds
        self._context = context or ContextBuilder()
        self._recorder: Recorder = recorder or null_trace
        self._router = router

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
            captured: list[UsageStats] = []
            resp = self._client.chat(
                request_messages,
                model=self._resolve_model(),
                tools=tools,
                usage_callback=captured.append,
            )
            self._recorder.record_llm(
                self._model or "default",
                prompt_summary=f"消息数 {len(request_messages)}",
                duration_s=time.perf_counter() - started,
                **_usage_token_fields(captured[0] if captured else None),
            )
            messages.append(resp)

            tool_calls = resp.get("tool_calls")
            if not tool_calls:
                return resp.get("content", "").strip()

            self._execute_tool_calls(tool_calls, messages)

        logger.warning("工具调用超过 %d 轮，终止本轮对话", self._max_tool_rounds)
        return "（工具调用次数过多，本轮对话已终止。请简化问题或换一种问法。）"

    def _resolve_model(self) -> str | None:
        """解析本轮对话模型：显式指定优先，否则按「需要工具调用」意图走路由。"""
        if self._model is not None:
            return self._model
        if self._router is not None:
            return RouteIntent.TOOLS.resolve(self._router)
        return None

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