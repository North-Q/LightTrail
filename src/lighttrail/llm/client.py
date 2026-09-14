"""LLM 客户端的迁移期 shim（B3-5）：真源已迁至 `lighttrail.adapters.llm.client`。

TODO(B5-4): 删除本 shim——域工具（photo_analysis）与编排层改经 `ToolContext.llm` 端口，
调用方改 `from lighttrail.adapters.llm.client import ChatClient`。

注意：本文件只做 re-export；**monkeypatch 请打在真源模块**
（`lighttrail.adapters.llm.client`），打在本 shim 上不会影响运行中的实现。
"""

from __future__ import annotations

from lighttrail.adapters.llm.client import (
    ChatClient,
    LLMError,
    UsageCallback,
    UsageStats,
)

__all__ = [
    "ChatClient",
    "LLMError",
    "UsageCallback",
    "UsageStats",
]
