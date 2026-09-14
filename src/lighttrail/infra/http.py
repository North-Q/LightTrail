"""httpx JSON 取数基件：统一超时、重试与错误语义（B3-2 三套重试收敛）。

位置说明（迁移期）：实现放在 infra（域工具可直接复用），adapters/datasources 包对其
re-export 作为适配层对外门面；目标分层（B3-5/B5-4）落地后把实现移入 adapters，
届时域工具改经 ToolContext.datasource 端口注入。

设计要点：
- **统一重试**：退避策略只在 tenacity 一处定义（指数退避 + 抖动，最多 N 次）；
  429/5xx/网络错误可重试，4xx（参数错误）立即失败并带服务端原因——与 llm/client 的重试语义对齐；
- **统一 HTTP 客户端**：httpx 取代 weather 原先的 urllib 手写重试；
- 同时提供 async `get_json` 与 sync `get_json_sync`（同步工具在 worker 线程里调用，
  与管线并发采集的 `asyncio.to_thread` 模式契合）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_random,
)

logger = logging.getLogger("lighttrail.infra.http")

# 默认超时与重试次数（与 llm/client 的重试次数一致：3 次）
DEFAULT_TIMEOUT = 15.0
DEFAULT_MAX_ATTEMPTS = 3
# 可重试的状态码（与 llm/client 的 _RETRYABLE_STATUS 对齐）
_RETRY_STATUS = {429, 500, 502, 503, 504}


class DataSourceError(Exception):
    """外部数据源取数失败（含 4xx 参数错误与重试耗尽的 5xx/网络错误）。"""


class _RetryableStatus(Exception):
    """内部信号：可重试的状态码（交由 tenacity 决定退避重试）。"""

    def __init__(self, status: int, detail: str = "") -> None:
        super().__init__(f"HTTP {status}" + (f"：{detail}" if detail else ""))
        self.status = status


async def get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """GET 请求并解析 JSON（异步；429/5xx/网络错误按指数退避重试）。

    Args:
        url: 请求地址。
        params: 查询参数。
        headers: 附加请求头。
        timeout: 单次请求超时（秒）。
        max_attempts: 最多尝试次数（默认 3）。
        client: 复用的 httpx 客户端（None 时临时创建并在结束时关闭）。

    Returns:
        解析后的 JSON 字典。

    Raises:
        DataSourceError: 4xx 参数错误（立即失败）或重试耗尽的 5xx/网络错误。
    """
    owns_client = client is None
    active = client or httpx.AsyncClient(timeout=timeout, headers=headers)
    retryer = AsyncRetrying(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4) + wait_random(0, 0.3),
        retry=retry_if_exception_type((_RetryableStatus, httpx.HTTPError)),
        reraise=True,
    )
    try:
        async for attempt in retryer:
            with attempt:
                response = await active.get(url, params=params)
                if response.status_code in _RETRY_STATUS:
                    raise _RetryableStatus(response.status_code, response.text[:200])
                if response.status_code >= 400:
                    raise DataSourceError(
                        f"HTTP {response.status_code}：{response.text[:200]}"
                    )
                return response.json()
    except _RetryableStatus as exc:
        raise DataSourceError(f"取数失败（已重试 {max_attempts} 次）：{exc}") from exc
    except httpx.HTTPError as exc:
        raise DataSourceError(
            f"取数失败（已重试 {max_attempts} 次）：{type(exc).__name__}: {exc}"
        ) from exc
    finally:
        if owns_client:
            await active.aclose()
    raise DataSourceError("取数失败：未产生响应")


def get_json_sync(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> dict[str, Any]:
    """同步门面：内部 `asyncio.run` 执行 `get_json`。

    Args:
        url: 请求地址。
        params: 查询参数。
        headers: 附加请求头。
        timeout: 单次请求超时（秒）。
        max_attempts: 最多尝试次数。

    Returns:
        解析后的 JSON 字典。

    Raises:
        DataSourceError: 取数失败，或在事件循环内调用（应改用 get_json）。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            get_json(
                url,
                params=params,
                headers=headers,
                timeout=timeout,
                max_attempts=max_attempts,
            )
        )
    raise DataSourceError("同步取数不能在事件循环内调用，请改用 get_json()")


__all__ = ["DEFAULT_MAX_ATTEMPTS", "DEFAULT_TIMEOUT", "DataSourceError", "get_json", "get_json_sync"]