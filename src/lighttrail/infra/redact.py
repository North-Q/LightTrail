"""敏感信息出口过滤（B3-4，v4 §3 D7「防日志泄漏」）。

设计要点：
- **统一出口**：trace / SSE 事件、错误信息等一切"要进日志或前端"的文本，先过 `redact()`；
- 配置/客户端登记服务端凭据的确切值及 URL/JSON 转义形式；公开 SDK 标识除外。
- 已知前缀形态提供兜底；日志在 handler 捕获前过滤消息及格式化异常链。
- 说明：本模块是**兜底**，不是许可证——业务代码仍然不得把凭据塞进事件载荷。
"""

from __future__ import annotations

import json
import logging
import re
import threading
import traceback
from typing import Any
from urllib.parse import quote

_SECRETS: frozenset[str] = frozenset()
_LOCK = threading.Lock()
_LOG_FILTER_INSTALLED = False


def register_secrets(*values: str) -> None:
    """登记服务端凭据，并在日志记录创建时过滤消息与异常栈。

    Args:
        *values: 已知服务端凭据；公开浏览器 SDK 标识不在此登记。
    """
    global _SECRETS, _LOG_FILTER_INSTALLED
    with _LOCK:
        _SECRETS = _SECRETS | frozenset(
            variant
            for value in values
            if value
            for variant in (
                value,
                quote(value, safe=""),
                json.dumps(value, ensure_ascii=False)[1:-1],
            )
        )
        if _LOG_FILTER_INSTALLED:
            return
        previous = logging.getLogRecordFactory()

        def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
            """在所有日志 handler 和测试捕获之前移除凭据。"""
            record = previous(*args, **kwargs)
            if (
                record.name == "uvicorn.access"
                and isinstance(record.args, tuple)
                and len(record.args) == 5
            ):
                # Uvicorn 的访问日志 formatter 会拆五个参数；净化后保留协议结构。
                record.msg = redact(str(record.msg))
                record.args = tuple(
                    redact(item) if isinstance(item, str) else item for item in record.args
                )
            else:
                record.msg = redact(record.getMessage())
                record.args = ()
            if record.exc_info:
                record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)))
                record.exc_info = None
            if record.stack_info:
                record.stack_info = redact(record.stack_info)
            return record

        logging.setLogRecordFactory(factory)
        _LOG_FILTER_INSTALLED = True


# 已知密钥形态：sk- 前缀（OpenAI 兼容平台通用）、Bearer 头、以及 32+ 位的十六进制/URL-safe 随机串
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(sk-[A-Za-z0-9_\-]{4})[A-Za-z0-9_\-]{4,}"), r"\1***"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}"), r"\1***"),
    (re.compile(r"(?i)(api[_-]?key[\"'=:\s]+)[A-Za-z0-9._\-]{8,}"), r"\1***"),
)

# 掩码替换文本
MASK = "***"


def redact(text: str) -> str:
    """对文本做敏感信息掩码（保留前缀便于排查，其余打码）。

    Args:
        text: 原始文本（可能含密钥/凭据）。

    Returns:
        掩码后的文本；无敏感形态时原样返回。
    """
    if not text:
        return text
    result = text
    for secret in sorted(_SECRETS, key=len, reverse=True):
        result = result.replace(secret, MASK)
    for pattern, replacement in _PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def contains_secret(text: str) -> bool:
    """判断文本是否仍含未掩码的敏感形态（供测试与出口断言使用）。"""
    return any(secret in text for secret in _SECRETS) or any(
        pattern.search(text) for pattern, _ in _PATTERNS
    )


__all__ = ["MASK", "contains_secret", "redact"]
