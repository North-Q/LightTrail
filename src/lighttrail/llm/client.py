"""LLM 客户端封装：OpenAI 兼容接口的薄封装。

设计要点：
- 并发策略可配置：是否串行由 `serial_llm` 开关控制（默认开，适配 ECNU
  平台「避免并行请求」的建议；接入支持并发的 API 时设为 False 即可）。
  串行锁是**实例级**且只包住单次 API 往返（`chat.completions.create`），
  重试循环在锁外——关闭串行后并发、重试、缓存策略相互独立、互不掣肘；
- 容错：对限流（429）与服务器错误（5xx）做指数退避重试；
- 超时：连接 30s、读取 120s，容忍模型 thinking 模式下的长响应。

并发策略属于「平台适配」而非业务逻辑：产品层面默认关闭并行是 ECNU 的
建议，不是 LightTrail 的需求。换 API 时通过 Settings.serial_llm 调整，
业务层（Agent/管线）不感知。
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any

from openai import OpenAI

from lighttrail.config import DEFAULT_MODEL
from lighttrail.infra.quota import QuotaLedger

logger = logging.getLogger("lighttrail.llm")

# 重试策略：最多 3 次，基础退避 1s（含随机抖动）
_MAX_RETRIES = 3
_BASE_BACKOFF_SEC = 1.0

# 可重试的错误码（openai SDK 通常已映射为 APIConnectionError / RateLimitError 等）
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class LLMError(Exception):
    """LLM 调用失败的统一异常。"""


class ChatClient:
    """OpenAI 兼容对话客户端（并发策略可配置、带重试）。

    Args:
        api_key: API 密钥。
        base_url: OpenAI 兼容接口地址。
        timeout: (连接超时, 读取超时)。
        serial_llm: 是否串行调用（默认 True，适配 ECNU「避免并行请求」建议；
            接入支持并发的 API 时设 False）。
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        timeout: tuple[float, float] = (30.0, 120.0),
        serial_llm: bool = True,
        quota: QuotaLedger | None = None,
    ) -> None:
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self._serial_llm = serial_llm
        # 实例级串行锁：仅当 serial_llm=True 时启用，且只保护单次 API 往返
        self._lock = threading.Lock()
        # 配额账本（E4-2）：成功响应后按 usage 记账；未注入时零开销不记账
        self._quota = quota

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
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

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                # 串行开关只包住单次往返；重试在锁外，因此并发模式下
                # 多个调用各自重试互不阻塞
                resp = self._request_once(payload)
                self._record_usage(payload, resp)
                return self._to_message_dict(resp)
            except Exception as exc:  # noqa: BLE001 - 需要统一判定可重试性
                last_exc = exc
                if not self._should_retry(exc) or attempt == _MAX_RETRIES - 1:
                    break
                backoff = _BASE_BACKOFF_SEC * (2**attempt) + random.uniform(0, 0.5)
                logger.warning("LLM 调用失败（第 %d 次），%.1fs 后重试：%s", attempt + 1, backoff, exc)
                time.sleep(backoff)
        raise LLMError(f"LLM 调用失败：{last_exc}") from last_exc

    def _record_usage(self, payload: dict[str, Any], resp: Any) -> None:
        """成功响应后把 usage 折算为 credits 记账（配额账本，E4-2）。"""
        if self._quota is None:
            return
        usage = getattr(resp, "usage", None)
        if usage is None:
            return
        details = getattr(usage, "prompt_tokens_details", None)
        cached_tokens = int(getattr(details, "cached_tokens", 0) or 0)
        self._quota.record(
            str(payload.get("model") or DEFAULT_MODEL),
            int(usage.prompt_tokens or 0),
            int(usage.completion_tokens or 0),
            cached_input_tokens=cached_tokens,
        )

    def _request_once(self, payload: dict[str, Any]):
        """发送单次请求：serial_llm=True 时经串行锁，否则直接调用。"""
        if self._serial_llm:
            with self._lock:
                return self._client.chat.completions.create(**payload)
        return self._client.chat.completions.create(**payload)

    @staticmethod
    def _should_retry(exc: Exception) -> bool:
        """判定异常是否值得重试（限流 / 服务器错误 / 连接问题）。

        匹配策略：
        - openai SDK 将 429/5xx 映射为特定异常（RateLimitError / APIStatusError），
          通过响应状态码或类型名识别；
        - 连接层异常（APIConnectionError / ConnectionError / OSError / httpx 超时）
          也视为可重试（网络瞬时抖动）。
        """
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if isinstance(status, int) and status in _RETRYABLE_STATUS:
            return True
        type_name = type(exc).__name__
        module = type(exc).__module__
        if "RateLimit" in type_name or "APIConnection" in type_name or "Timeout" in type_name:
            return True
        # 兜底：连接层/传输层异常（含 SDK 包装后的 ConnectionError、httpx 异常）
        return isinstance(exc, (ConnectionError, OSError)) or "httpx" in module

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
