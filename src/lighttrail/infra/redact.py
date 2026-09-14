"""敏感信息出口过滤（B3-4，v4 §3 D7「防日志泄漏」）。

设计要点：
- **统一出口**：trace / SSE 事件、错误信息等一切"要进日志或前端"的文本，先过 `redact()`；
- 只做"已知敏感形态"的掩码（API Key 前缀、长随机串），不做启发式猜测——避免把正常数据
  误伤成 `***`（那会让可解释性打折）；
- 说明：本模块是**兜底**，不是许可证——业务代码仍然不得把凭据塞进事件载荷。
"""

from __future__ import annotations

import re

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
    for pattern, replacement in _PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def contains_secret(text: str) -> bool:
    """判断文本是否仍含未掩码的敏感形态（供测试与出口断言使用）。"""
    return any(pattern.search(text) for pattern, _ in _PATTERNS)


__all__ = ["MASK", "contains_secret", "redact"]