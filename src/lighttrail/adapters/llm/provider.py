"""LLMProvider 端口实现（迁移期）：桥接既有 ChatClient（B2-5）。

设计要点：
- 端口是 async-first（`complete` 为协程）；既有 `ChatClient.acall` 已是 async 通道，
  本实现直接委托，不再维护第二套并发控制（B3 会把 ChatClient 重写成同层实现）；
- `usage_callback(prompt, completion)` 沿用 ChatClient 的回传机制，供上层记账；
- 品牌与平台扩展参数适配仍在 `lighttrail.llm.client`（ADR-002/003）：本文件只做转发。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from lighttrail.adapters.llm.client import ChatClient, UsageStats


class ChatClientProvider:
    """把 `ChatClient` 适配成 `LLMProvider` 端口（迁移期实现）。

    Args:
        client: 既有 LLM 客户端（同步 chat + async acall 双通道）。
    """

    def __init__(self, client: ChatClient) -> None:
        self._client = client

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        thinking: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
        usage_callback: Callable[[int, int], None] | None = None,
    ) -> dict[str, Any]:
        """异步发起一次对话补全（委托 ChatClient.acall）。

        Args:
            messages: OpenAI 格式消息列表。
            model: 模型名（None 由客户端默认决定）。
            tools: OpenAI 格式工具定义。
            temperature: 采样温度。
            thinking: 思考模式扩展参数。
            reasoning_effort: 推理强度。
            usage_callback: token 用量回调（prompt, completion）。

        Returns:
            助手消息字典。

        Raises:
            LLMError: 调用失败（重试耗尽或参数错误）。
        """
        # 端口契约是 (输入, 输出) 两个整数；ChatClient 回传的是 UsageStats 对象——
        # 适配在这里完成（真实链路只有经适配器才能过，测试 fake 必须镜像真实签名）。
        def _on_usage(stats: UsageStats) -> None:
            if usage_callback is not None:
                usage_callback(stats.prompt_tokens, stats.completion_tokens)

        return await self._client.acall(
            messages,
            model=model,
            tools=tools,
            temperature=temperature,
            thinking=thinking,
            reasoning_effort=reasoning_effort,
            usage_callback=_on_usage if usage_callback is not None else None,
        )

    def queue_position(self) -> int:
        """在途 + 排队的 LLM 请求数（0 = 空闲；供 SSE queued 事件）。"""
        return self._client.queue_position()


__all__ = ["ChatClientProvider"]