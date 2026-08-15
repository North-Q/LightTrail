"""LLM 客户端封装：OpenAI 兼容接口的薄封装。

设计要点：
- 全程串行：模块级锁保证同一进程内不会并发调用平台 API（平台建议避免并行请求）；
- 容错：对限流（429）与服务器错误（5xx）做指数退避重试；
- 超时：连接 30s、读取 120s，容忍模型 thinking 模式下的长响应。
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any, Optional

from openai import OpenAI

logger = logging.getLogger("lighttrail.llm")

# 模块级串行锁：同一进程内所有 LLM 调用排队执行，杜绝并发请求
_SERIAL_LOCK = threading.Lock()

# 重试策略：最多 3 次，基础退避 1s（含随机抖动）
_MAX_RETRIES = 3
_BASE_BACKOFF_SEC = 1.0

# 可重试的错误码（openai SDK 通常已映射为 APIConnectionError / RateLimitError 等）
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class LLMError(Exception):
    """LLM 调用失败的统一异常。"""


class ChatClient:
    """OpenAI 兼容对话客户端（串行、带重试）。"""

    def __init__(self, api_key: str, base_url: str, *, timeout: tuple[float, float] = (30.0, 120.0)):
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: Optional[str] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        """发起一次对话补全，返回消息字典（兼容 tool_calls 字段）。

        Args:
            messages: OpenAI 格式消息列表。
            model: 模型名，缺省由上层（Agent）决定。
            tools: OpenAI 格式工具定义列表，可为 None。
            temperature: 采样温度，工具调用链路使用较低值保证稳定。

        Returns:
            助手消息字典，含 role/content，可能含 tool_calls。

        Raises:
            LLMError: 重试耗尽或参数错误。
        """
        payload: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
        if tools:
            payload["tools"] = tools

        with _SERIAL_LOCK:  # 串行执行，符合平台「避免并行请求」的建议
            last_exc: Optional[Exception] = None
            for attempt in range(_MAX_RETRIES):
                try:
                    resp = self._client.chat.completions.create(**payload)
                    return self._to_message_dict(resp)
                except Exception as exc:  # noqa: BLE001 - 需要统一判定可重试性
                    last_exc = exc
                    if not self._should_retry(exc) or attempt == _MAX_RETRIES - 1:
                        break
                    backoff = _BASE_BACKOFF_SEC * (2**attempt) + random.uniform(0, 0.5)
                    logger.warning("LLM 调用失败（第 %d 次），%.1fs 后重试：%s", attempt + 1, backoff, exc)
                    time.sleep(backoff)
        raise LLMError(f"LLM 调用失败：{last_exc}") from last_exc

    @staticmethod
    def _should_retry(exc: Exception) -> bool:
        """判定异常是否值得重试（限流 / 服务器错误 / 连接问题）。"""
        # openai SDK 将 429/5xx 映射为特定异常类型；此处按状态码与常见类型双保险
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if isinstance(status, int) and status in _RETRYABLE_STATUS:
            return True
        type_name = type(exc).__name__
        return "RateLimit" in type_name or "APIConnection" in type_name or "Timeout" in type_name

    @staticmethod
    def _to_message_dict(resp) -> dict[str, Any]:
        """把 SDK 响应对象转换为纯字典消息（含可选 tool_calls）。"""
        msg = resp.choices[0].message
        out: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
        if getattr(msg, "tool_calls", None):
            out["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]
        return out
