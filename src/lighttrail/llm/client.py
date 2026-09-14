"""LLM 客户端封装：OpenAI 兼容接口的 async-first 薄封装（B3-1 重写）。

设计要点（v4 §3 D5 / ADR-004；B3-1 落地）：
- **单一并发机制**：只保留一个跨越同步/异步调用方的限流器（`_ConcurrencyLimiter`，上限
  `LLM_CONCURRENCY`，默认 4）；旧的三套机制（threading.Lock + 自研 `_AsyncGate` FIFO 闸门 +
  call_soon_threadsafe 桥）已删除；
- **单一实现**：真正的调用逻辑只在 `acall`（async-first）；同步 `chat` 只是「后台共享事件循环 +
  run_coroutine_threadsafe」的门面，不再逐行复制；
- 限流只包住单次 API 往返，重试在限流之外（并发模式下多个调用各自重试互不阻塞）；
- 容错：对限流（429）与服务器错误（5xx）做指数退避重试（最多 3 次，基础 1s + 抖动）；
- 超时：连接 30s、读取 120s，容忍 thinking 模式下的长响应；
- 平台扩展参数（ADR-003）：thinking / reasoning_effort 由 SDK 原生参数或 extra_body 双路径承载
  （`_NATIVE_REASON_PARAMS` 运行时探测），业务代码不感知。

并发是配置不是架构（ADR-004）：`LLM_CONCURRENCY=1` 即等价旧「串行」；`serial_llm` 仅作
只读兼容别名过渡一版（=True → concurrency=1），B5 批次随旧开关一并删除。
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from openai import AsyncOpenAI, OpenAI
from openai.resources.chat.completions import Completions as _OpenAICompletions
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    wait_random,
)

from lighttrail.config import DEFAULT_MODEL

if TYPE_CHECKING:  # 仅类型注解：运行时不 import quota，避免 client ↔ quota 循环依赖
    from lighttrail.infra.quota import QuotaLedger

logger = logging.getLogger("lighttrail.llm")

# 重试策略：最多 3 次，基础退避 1s（含随机抖动）
_MAX_RETRIES = 3
_BASE_BACKOFF_SEC = 1.0

# 可重试的错误码（openai SDK 通常已映射为 APIConnectionError / RateLimitError 等）
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# 默认并发上限（settings.LLM_CONCURRENCY 的模型侧默认，B1-5 起配置为唯一真源）
_DEFAULT_CONCURRENCY = 4

# 运行时探测：openai SDK 是否原生支持 thinking / reasoning_effort 命名参数。
# 不同 SDK 版本签名不同（如 openai 3.1 无 thinking，须经 extra_body 携带），
# 探测一次即可——这是对「平台/版本能力」的适配，业务代码不感知。
_NATIVE_REASON_PARAMS = (
    "thinking" in inspect.signature(_OpenAICompletions.create).parameters
    and "reasoning_effort" in inspect.signature(_OpenAICompletions.create).parameters
)


class LLMError(Exception):
    """LLM 调用失败的统一异常。"""


@dataclass(frozen=True)
class UsageStats:
    """单次 LLM 调用的 token 用量（供应商未返回 usage 时不存在该对象）。

    Attributes:
        prompt_tokens: 输入 token 数。
        completion_tokens: 输出 token 数。
        cached_input_tokens: 命中缓存的输入 token 数（供应商支持时，否则 0）。
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """输入 + 输出 token 总数。"""
        return self.prompt_tokens + self.completion_tokens


# token 用量回调：供应商返回 usage 时调用一次（供上层 trace 记账，B0-2）
UsageCallback = Callable[[UsageStats], None]


class _ConcurrencyLimiter:
    """跨事件循环的 LLM 并发限流器（B3-1：单一并发机制）。

    设计要点：
    - 上限同时约束同步与异步调用方：内部用 `threading.BoundedSemaphore`，异步侧经
      `asyncio.to_thread` 获取——避免 `asyncio.Semaphore` 绑 loop 的问题（同步门面走后台共享
      事件循环、Web 走 uvicorn 事件循环，是两个 loop）；
    - 只包住单次 API 往返，重试在限流之外（ADR-004：并发是纯配置）；
    - `in_flight()` 供 SSE queued 事件展示「排队第 N 位」。

    Args:
        limit: 在途请求上限（>=1；1 即等价旧「串行」语义）。
    """

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise LLMError("LLM 并发上限必须 >= 1")
        self._limit = limit
        self._semaphore = threading.BoundedSemaphore(limit)
        self._lock = threading.Lock()
        self._active = 0

    @property
    def limit(self) -> int:
        """配置的并发上限。"""
        return self._limit

    def in_flight(self) -> int:
        """当前在途（已获取配额）的请求数。"""
        with self._lock:
            return self._active

    @contextlib.contextmanager
    def sync(self) -> Iterator[None]:
        """同步调用方进入临界区（阻塞直到有空位）。"""
        self._semaphore.acquire()
        with self._lock:
            self._active += 1
        try:
            yield
        finally:
            with self._lock:
                self._active -= 1
            self._semaphore.release()

    @contextlib.asynccontextmanager
    async def acquire(self) -> Any:
        """异步调用方进入临界区（在线程池里阻塞获取，不卡事件循环）。"""
        await asyncio.to_thread(self._semaphore.acquire)
        with self._lock:
            self._active += 1
        try:
            yield
        finally:
            with self._lock:
                self._active -= 1
            self._semaphore.release()


# ------ 同步门面用的后台共享事件循环 ------
_sync_loop: asyncio.AbstractEventLoop | None = None
_sync_loop_lock = threading.Lock()


def _shared_loop() -> asyncio.AbstractEventLoop:
    """返回同步门面共用的后台事件循环（首次调用时创建守护线程）。"""
    global _sync_loop
    with _sync_loop_lock:
        if _sync_loop is None or _sync_loop.is_closed():
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=loop.run_forever, name="lighttrail-llm-sync", daemon=True
            )
            thread.start()
            _sync_loop = loop
        return _sync_loop


def _run_sync(coro: Any) -> Any:
    """在后台共享事件循环里执行协程并等待结果（同步门面唯一入口）。

    Args:
        coro: 待执行协程。

    Returns:
        协程结果。

    Raises:
        LLMError: 调用方已在事件循环内（应改用 acall 而非 chat）。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        coro.close()
        raise LLMError("同步门面不能在事件循环内调用，请改用 acall()")
    future = asyncio.run_coroutine_threadsafe(coro, _shared_loop())
    return future.result()


class ChatClient:
    """OpenAI 兼容对话客户端（async-first、可配置并发、带重试）。

    Args:
        api_key: API 密钥。
        base_url: OpenAI 兼容接口地址。
        timeout: (连接超时, 读取超时)。
        concurrency: 在途 LLM 请求上限（默认 4，见 ADR-004；1 等价旧「串行」）。
        serial_llm: 只读兼容别名（True → concurrency=1）；B5 批次删除。
        quota: 配额账本（成功响应后按 usage 记账；未注入时不记账）。
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        timeout: tuple[float, float] = (30.0, 120.0),
        concurrency: int = _DEFAULT_CONCURRENCY,
        serial_llm: bool | None = None,
        quota: QuotaLedger | None = None,
    ) -> None:
        if serial_llm:
            # 兼容映射：旧开关 =True 等价 concurrency=1（ADR-004；B5 删除该别名）
            concurrency = 1
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self._quota = quota
        self._limiter = _ConcurrencyLimiter(concurrency)
        self._api_key = api_key
        self._base_url = base_url
        self._timeout = timeout
        self._async_client: AsyncOpenAI | None = None

    # ------ 对外接口 ------
    @property
    def concurrency(self) -> int:
        """当前并发上限（供诊断/测试断言）。"""
        return self._limiter.limit

    def queue_position(self) -> int:
        """当前在途（执行中）的 LLM 请求数（0 = 空闲；供 SSE queued 事件）。"""
        return self._limiter.in_flight()

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        thinking: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
        usage_callback: UsageCallback | None = None,
    ) -> dict[str, Any]:
        """同步门面：在后台共享事件循环里执行 `acall`（B3-1 起不再另写一份实现）。

        Args:
            messages: OpenAI 格式消息列表。
            model: 模型名，缺省由上层（Agent/管线）决定。
            tools: OpenAI 格式工具定义列表，可为 None。
            temperature: 采样温度，工具调用链路使用较低值保证稳定。
            thinking: 思考模式扩展参数（如 {"type": "enabled"}），None 时不携带。
            reasoning_effort: 推理强度（low/medium/high），None 时不携带。
            usage_callback: token 用量回调（供应商返回 usage 时调用一次）。

        Returns:
            助手消息字典，含 role/content；可能含 tool_calls 与 thinking 摘要。

        Raises:
            LLMError: 重试耗尽、参数错误，或在事件循环内调用（应改用 acall）。
        """
        return _run_sync(
            self.acall(
                messages,
                model=model,
                tools=tools,
                temperature=temperature,
                thinking=thinking,
                reasoning_effort=reasoning_effort,
                usage_callback=usage_callback,
            )
        )

    async def acall(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        thinking: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
        usage_callback: UsageCallback | None = None,
    ) -> dict[str, Any]:
        """async 调用通道（唯一实现）：限流只包单次往返，重试在限流之外。

        Args:
            messages: OpenAI 格式消息列表。
            model: 模型名，缺省由上层决定。
            tools: OpenAI 格式工具定义列表，可为 None。
            temperature: 采样温度。
            thinking: 思考模式扩展参数，None 时不携带。
            reasoning_effort: 推理强度，None 时不携带。
            usage_callback: token 用量回调。

        Returns:
            助手消息字典（与同步门面统一转换逻辑）。

        Raises:
            LLMError: 重试耗尽或参数错误。
        """
        payload: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
        if tools:
            payload["tools"] = tools
        self._attach_reason_params(payload, thinking, reasoning_effort)

        retryer = AsyncRetrying(
            stop=stop_after_attempt(_MAX_RETRIES),
            wait=wait_exponential(multiplier=_BASE_BACKOFF_SEC, min=_BASE_BACKOFF_SEC, max=8)
            + wait_random(0, 0.5),
            retry=retry_if_exception(self._should_retry),
            reraise=False,
        )
        try:
            async for attempt in retryer:
                with attempt:
                    async with self._limiter.acquire():
                        resp = await self._get_async_client().chat.completions.create(**payload)
                    self._record_usage(payload, resp, usage_callback)
                    return self._to_message_dict(resp)
        except RetryError as exc:
            last = exc.last_attempt.exception()
            raise LLMError(f"LLM 调用失败：{last}") from last
        raise LLMError("LLM 调用失败：未产生响应")

    # ------ 内部实现 ------
    def _get_async_client(self) -> AsyncOpenAI:
        """惰性创建 AsyncOpenAI 客户端（首次调用时建立，避免占用无用连接）。"""
        if self._async_client is None:
            self._async_client = AsyncOpenAI(
                api_key=self._api_key, base_url=self._base_url, timeout=self._timeout
            )
        return self._async_client

    @staticmethod
    def _attach_reason_params(
        payload: dict[str, Any],
        thinking: dict[str, Any] | None,
        reasoning_effort: str | None,
    ) -> None:
        """把深推理扩展参数附加到请求体。

        SDK 原生支持时用命名参数（规范路径）；否则经 extra_body 合并——
        OpenAI 兼容代理从请求体中读取 thinking/reasoning_effort 字段，
        因此 extra_body 与命名参数等价，且兼容任意 SDK 版本。
        """
        if _NATIVE_REASON_PARAMS:
            if thinking is not None:
                payload["thinking"] = thinking
            if reasoning_effort is not None:
                payload["reasoning_effort"] = reasoning_effort
            return
        extra: dict[str, Any] = {}
        if thinking is not None:
            extra["thinking"] = thinking
        if reasoning_effort is not None:
            extra["reasoning_effort"] = reasoning_effort
        if extra:
            payload["extra_body"] = extra

    def _record_usage(
        self,
        payload: dict[str, Any],
        resp: Any,
        usage_callback: UsageCallback | None = None,
    ) -> None:
        """成功响应后回传 usage 给调用方，并折算 credits 记账（配额账本，E4-2）。

        Args:
            payload: 本次请求体（取模型名记账）。
            resp: SDK 响应对象（可能不含 usage，此时两项都不做）。
            usage_callback: 上层 token 记账回调；None 时不回调。
        """
        stats = self._extract_usage(resp)
        if stats is None:
            return
        if usage_callback is not None:
            usage_callback(stats)
        if self._quota is None:
            return
        self._quota.record(
            str(payload.get("model") or DEFAULT_MODEL),
            stats.prompt_tokens,
            stats.completion_tokens,
            cached_input_tokens=stats.cached_input_tokens,
        )

    @staticmethod
    def _extract_usage(resp: Any) -> UsageStats | None:
        """从 SDK 响应中提取 token 用量（供应商未返回 usage 时返回 None）。"""
        usage = getattr(resp, "usage", None)
        if usage is None:
            return None
        details = getattr(usage, "prompt_tokens_details", None)
        return UsageStats(
            prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            cached_input_tokens=int(getattr(details, "cached_tokens", 0) or 0),
        )

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
    def _to_message_dict(resp: Any) -> dict[str, Any]:
        """把 SDK 响应对象转换为纯字典消息（含可选 tool_calls 与 thinking 摘要）。"""
        msg = resp.choices[0].message
        out: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
        # 深推理通道的思考内容摘要（供应商扩展字段：reasoning_content / thinking）
        for key in ("reasoning_content", "thinking"):
            raw = getattr(msg, key, None)
            if raw:
                out["thinking"] = str(raw)[:2000]
                break
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