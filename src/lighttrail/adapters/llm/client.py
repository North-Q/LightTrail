"""LLM 客户端实现：OpenAI 兼容接口的 async-first 薄封装（B3-1 重写；B3-5 迁入适配层）。

归属：本文件是 LLM 客户端的**实现真源**（适配层 L4）。

设计要点（v4 §3 D5 / ADR-004；B3-1 落地）：
- **单一并发机制**：只保留一个跨越同步/异步调用方的限流器（`_ConcurrencyLimiter`，上限
  `LLM_CONCURRENCY`，默认 4）；旧的三套机制（threading.Lock + 自研 `_AsyncGate` FIFO 闸门 +
  call_soon_threadsafe 桥）已删除；
- **单一实现**：真正的调用逻辑只在 `acall`（async-first）；同步 `chat` 只是「后台共享事件循环 +
  run_coroutine_threadsafe」的门面，不再逐行复制；
- 限流只包住单次 API 往返，重试在限流之外（并发模式下多个调用各自重试互不阻塞）；
- 容错：外层最多 3 次底层尝试（首次 + 2 次重试），SDK 重试关闭；退避在许可之外；
- 超时：连接/读取配置传入 httpx.Timeout；默认 30s/120s，并非整条管线总时限；
- 平台扩展参数（ADR-003）：thinking / reasoning_effort 由 SDK 原生参数或 extra_body 双路径承载
  （`_NATIVE_REASON_PARAMS` 运行时探测），业务代码不感知。

并发是配置不是架构（ADR-004）：`LLM_CONCURRENCY=1` 即可采用串行执行。
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

import httpx
from openai import APIError, AsyncOpenAI, OpenAI
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
from lighttrail.infra.async_runner import run_sync
from lighttrail.infra.redact import redact, register_secrets

if TYPE_CHECKING:  # 仅类型注解：运行时不 import quota，避免 client ↔ quota 循环依赖
    from lighttrail.infra.quota import QuotaLedger

logger = logging.getLogger("lighttrail.llm")


def _safe_error(exc: BaseException | None) -> str:
    """保留 SDK 错误类型、状态及请求编号，移除原始凭据和异常链。"""
    return redact(
        f"LLM 调用失败：{type(exc).__name__} "
        f"status={getattr(exc, 'status_code', None)} "
        f"request_id={getattr(exc, 'request_id', None)} {exc}"
    )


# 每次 acall 的底层总尝试上限，包含首次发送；SDK 不再额外重试。
_MAX_ATTEMPTS = 3
_BASE_BACKOFF_SEC = 1.0
_LIMITER_POLL_SEC = 0.01

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

    def __init__(self, message: str, *, observation: dict[str, Any] | None = None) -> None:
        """保存安全用量摘要，使输出截断的已观测费用仍可追溯。"""
        super().__init__(message)
        self.observation = observation


@dataclass(frozen=True)
class UsageStats:
    """单次 LLM 调用的 token 用量（供应商未返回 usage 时不存在该对象）。

    Attributes:
        prompt_tokens: 输入 token 数。
        completion_tokens: 输出 token 数。
        cached_input_tokens: 缓存输入子集；缺失拆分时以零用于未缓存价估算。
        cache_known: 是否收到有效缓存拆分，False 时费用未确认。
        reasoning_tokens: 输出中的思考子集，缺少拆分为 None，不额外累加。
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_input_tokens: int = 0
    cache_known: bool = True
    reasoning_tokens: int | None = None

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
      非阻塞获取 + 短暂异步等待，避免跨 loop 绑定及取消后后台线程偷取许可；
      同步门面走后台共享事件循环、Web 走 uvicorn 事件循环，仍共用同一信号量；
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
        """可取消地等待许可；获取成功到 finally 建立之间没有 await。

        排队取消不会遗留阻塞线程。轮询不承诺 FIFO；已获取许可的请求在退出时归还。
        """
        while not self._semaphore.acquire(blocking=False):
            await asyncio.sleep(_LIMITER_POLL_SEC)
        with self._lock:
            self._active += 1
        try:
            yield
        finally:
            with self._lock:
                self._active -= 1
            self._semaphore.release()


# ------ 同步门面共用基础设施执行器 ------
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
    return run_sync(coro)


class ChatClient:
    """OpenAI 兼容对话客户端（async-first、可配置并发、带重试）。

    Args:
        api_key: API 密钥。
        base_url: OpenAI 兼容接口地址。
        timeout: (连接超时, 读取超时)。
        concurrency: 在途 LLM 请求上限（默认 4，见 ADR-004；1 等价旧「串行」）。
        quota: 配额账本（成功响应后按 usage 记账；未注入时不记账）。
        max_output_tokens: 每个请求的输出限制值；默认 4096，供应商执行待核验。
        output_limit_field: 接口限制字段；ECNU 官方示例为 max_tokens。
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        timeout: tuple[float, float] = (30.0, 120.0),
        concurrency: int = _DEFAULT_CONCURRENCY,
        quota: QuotaLedger | None = None,
        max_output_tokens: int = 4096,
        output_limit_field: str = "max_tokens",
    ) -> None:
        if max_output_tokens < 1 or output_limit_field not in {
            "max_tokens",
            "max_completion_tokens",
        }:
            raise LLMError("输出上限须为正整数，字段须为 max_tokens 或 max_completion_tokens")
        self._max_output_tokens = max_output_tokens
        self._output_limit_field = output_limit_field
        register_secrets(api_key)
        self._timeout = httpx.Timeout(
            timeout[1], connect=timeout[0], write=timeout[0], pool=timeout[0]
        )
        self._client = OpenAI(
            api_key=api_key, base_url=base_url, timeout=self._timeout, max_retries=0
        )
        self._quota = quota
        self._limiter = _ConcurrencyLimiter(concurrency)
        self._api_key = api_key
        self._base_url = base_url
        # 注入替身保留原有测试入口；真实连接池按所属 loop 缓存，限流器始终唯一。
        self._async_client: AsyncOpenAI | None = None
        self._async_clients: dict[asyncio.AbstractEventLoop, AsyncOpenAI] = {}
        self._clients_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._closed = False

    # ------ 对外接口 ------
    @property
    def concurrency(self) -> int:
        """当前并发上限（供诊断/测试断言）。"""
        return self._limiter.limit

    def queue_position(self) -> int:
        """当前在途（执行中）的 LLM 请求数（0 = 空闲；供 SSE queued 事件）。"""
        return self._limiter.in_flight()

    @property
    def quota(self) -> QuotaLedger | None:
        """暴露已装配账本，供上层在调用前做准入和可解释降级。"""
        return self._quota

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
        if self._closed:
            raise LLMError("LLM 客户端已关闭")
        payload: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
        payload[self._output_limit_field] = self._max_output_tokens
        if self._quota is not None and not self._quota.check():
            raise LLMError("本应用已观测用量达到本地窗口上限，本次请求未发送；非平台余额判断")
        if tools:
            payload["tools"] = tools
        self._attach_reason_params(payload, thinking, reasoning_effort)

        retryer = AsyncRetrying(
            stop=stop_after_attempt(_MAX_ATTEMPTS),
            wait=wait_exponential(multiplier=_BASE_BACKOFF_SEC, min=_BASE_BACKOFF_SEC, max=8)
            + wait_random(0, 0.5),
            retry=retry_if_exception(self._should_retry),
            reraise=False,
        )
        try:
            async for attempt in retryer:
                with attempt:
                    async with self._limiter.acquire():
                        logger.debug(
                            "LLM SDK 尝试 %d/%d", attempt.retry_state.attempt_number, _MAX_ATTEMPTS
                        )
                        sdk = self._get_async_client()
                        try:
                            resp = await sdk.chat.completions.create(**payload)
                        except (Exception, asyncio.CancelledError):
                            if self._quota is not None:
                                self._quota.record_unknown("请求异常或取消，供应商扣费未知")
                            raise
                    observation = self._record_usage(payload, resp, usage_callback)
                    if getattr(resp.choices[0], "finish_reason", None) == "length":
                        raise LLMError(
                            "模型输出被截断（finish_reason=length），不能接受不完整结果",
                            observation=observation,
                        )
                    reply = self._to_message_dict(resp)
                    reply["usage_observation"] = observation
                    return reply
        except RetryError as exc:
            last = exc.last_attempt.exception()
            raise LLMError(_safe_error(last)) from None
        except APIError as exc:
            raise LLMError(_safe_error(exc)) from None
        except Exception as exc:  # 非 SDK 异常仅在含凭据时净化，保持原重试语义
            if redact(str(exc)) != str(exc):
                if type(exc) in {ValueError, TypeError, RuntimeError}:
                    raise type(exc)(redact(str(exc))) from None
                raise LLMError(_safe_error(exc)) from None
            raise
        raise LLMError("LLM 调用失败：未产生响应")

    # ------ 内部实现 ------
    def _get_async_client(self) -> AsyncOpenAI:
        """按事件循环复用连接池，防止同步门面与 Web 互用绑定另一 loop 的连接。"""
        if self._async_client is not None:
            return self._async_client
        loop = asyncio.get_running_loop()
        with self._clients_lock:
            if self._closed:
                raise LLMError("LLM 客户端已关闭")
            if loop not in self._async_clients:
                self._async_clients[loop] = AsyncOpenAI(
                    api_key=self._api_key,
                    base_url=self._base_url,
                    timeout=self._timeout,
                    max_retries=0,
                )
            return self._async_clients[loop]

    async def aclose(self) -> None:
        """停止新请求，在每个连接池的所属循环内关闭；宿主须先结束所有请求。

        异步宿主必须在自身循环停止前 await aclose；同步宿主使用 close。
        注入的 _async_client 由注入方持有，不在此关闭。关闭失败保留句柄并显式报告，
        不因停止接收请求的标志将后续清理误判为成功。

        Raises:
            LLMError: 所属循环已停止，或 SDK 资源清理失败。
        """
        while not self._close_lock.acquire(blocking=False):
            await asyncio.sleep(_LIMITER_POLL_SEC)
        try:
            with self._clients_lock:
                self._closed = True
                clients = list(self._async_clients.items())
            errors: list[str] = []
            try:
                self._client.close()
            except Exception as exc:  # noqa: BLE001 继续清理其他资源，最后显式报告。
                errors.append(_safe_error(exc))
            current = asyncio.get_running_loop()
            for loop, sdk in clients:
                if loop is not current and not loop.is_running():
                    errors.append(
                        "连接池的所属事件循环已停止；异步宿主必须在循环停止前 await aclose()"
                    )
                    continue
                try:
                    if loop is current:
                        await sdk.close()
                    else:
                        await asyncio.wrap_future(
                            asyncio.run_coroutine_threadsafe(sdk.close(), loop)
                        )
                except Exception as exc:  # noqa: BLE001 保留失败句柄，不吞掉清理异常。
                    errors.append(_safe_error(exc))
                else:
                    with self._clients_lock:
                        self._async_clients.pop(loop, None)
            if errors:
                raise LLMError("LLM 资源清理失败：" + "；".join(errors)) from None
        finally:
            self._close_lock.release()

    def close(self) -> None:
        """同步宿主释放客户端；异步宿主应 await aclose()。"""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise LLMError("异步宿主请 await aclose()，不能同步关闭客户端")
        _run_sync(self.aclose())

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
    ) -> dict[str, Any]:
        """回传有效用量、估算费用；缺失或不确定的扣费明确标为未知。

        Args:
            payload: 本次请求体（取模型名记账）。
            resp: SDK 响应对象；缺少有效 usage 时登记未知。
            usage_callback: 上层 token 记账回调；None 时不回调。

        Returns:
            安全用量摘要，供现有 trace 与历史快照保存。
        """
        stats = self._extract_usage(resp)
        observation: dict[str, Any] = {
            "范围": "本应用已观测用量，非平台余额",
            "费用状态": "未知",
            "估算_credits": None,
        }
        if stats is None:
            if self._quota is not None:
                self._quota.record_unknown("usage 缺失或无效")
            observation["原因"] = "usage 缺失或无效；不能确认零费用"
            return observation
        if usage_callback is not None:
            usage_callback(stats)
        observation.update(
            {
                "输入_tokens": stats.prompt_tokens,
                "缓存输入_tokens": stats.cached_input_tokens if stats.cache_known else None,
                "未缓存输入_tokens": stats.prompt_tokens - stats.cached_input_tokens
                if stats.cache_known
                else None,
                "输出含思考_tokens": stats.completion_tokens,
                "其中思考_tokens": stats.reasoning_tokens,
            }
        )
        if self._quota is not None:
            from lighttrail.infra.quota import QuotaError

            try:
                observation["估算_credits"] = self._quota.record(
                    str(payload.get("model") or DEFAULT_MODEL),
                    stats.prompt_tokens,
                    stats.completion_tokens,
                    cached_input_tokens=stats.cached_input_tokens,
                )
            except QuotaError:
                self._quota.record_unknown("响应用量已观测，但缺少有效计价配置")
                observation["原因"] = "响应用量已观测，但缺少有效计价配置"
                return observation
            observation["费用状态"] = (
                "按返回用量估算" if stats.cache_known else "缓存拆分未知，按未缓存价估算"
            )
            if not stats.cache_known:
                self._quota.record_unknown("usage 缺少缓存拆分，费用仅按未缓存价估算")
            snapshot = self._quota.usage()
            observation["账本未知次数"] = snapshot.unknown_attempts
            observation["本应用估算合计_credits"] = snapshot.total_credits
        return observation

    @staticmethod
    def _extract_usage(resp: Any) -> UsageStats | None:
        """从 SDK 响应中提取 token 用量（供应商未返回 usage 时返回 None）。"""
        usage = getattr(resp, "usage", None)
        if usage is None:
            return None
        details = getattr(usage, "prompt_tokens_details", None)
        prompt = getattr(usage, "prompt_tokens", None)
        output = getattr(usage, "completion_tokens", None)
        cached = getattr(details, "cached_tokens", None)
        flat_cached = getattr(usage, "prompt_cache_hit_tokens", None)
        if cached is None:
            cached = flat_cached
        elif flat_cached is not None and flat_cached != cached:
            return None
        reasoning = getattr(
            getattr(usage, "completion_tokens_details", None), "reasoning_tokens", None
        )
        if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in (prompt, output)):
            return None
        if cached is not None and (
            not isinstance(cached, int) or isinstance(cached, bool) or not 0 <= cached <= prompt
        ):
            return None
        missed = getattr(usage, "prompt_cache_miss_tokens", None)
        if missed is not None and (cached is None or missed != prompt - cached):
            return None
        if reasoning is not None and (
            not isinstance(reasoning, int)
            or isinstance(reasoning, bool)
            or not 0 <= reasoning <= output
        ):
            return None
        return UsageStats(
            prompt_tokens=prompt,
            completion_tokens=output,
            cached_input_tokens=cached or 0,
            cache_known=cached is not None,
            reasoning_tokens=reasoning,
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
        if isinstance(status, int):
            return status in _RETRYABLE_STATUS
        type_name = type(exc).__name__
        if "RateLimit" in type_name or "APIConnection" in type_name or "Timeout" in type_name:
            return True
        # 兜底：连接层/传输层异常（含 SDK 包装后的 ConnectionError、httpx 异常）
        return isinstance(exc, (ConnectionError, OSError, httpx.TransportError)) and not isinstance(
            exc, httpx.UnsupportedProtocol
        )

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
