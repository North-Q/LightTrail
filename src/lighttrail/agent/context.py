"""上下文构建的迁移期 shim（B2-6）：真源已迁至 `lighttrail.runtime.context`。

TODO(B2-7): 删除本 shim——调用方改 `from lighttrail.runtime.context import ContextBuilder`。
本文件只做 re-export，不含任何逻辑（shim 带批次豁免，到期不删即批次不通过）。
"""

from __future__ import annotations

from lighttrail.runtime.context import (
    DEFAULT_CONDUCT_PROMPT,
    DEFAULT_ROLE_PROMPT,
    DEFAULT_SYSTEM_PROMPT,
    LAYER_CONDUCT,
    LAYER_PROFILE,
    LAYER_ROLE,
    LAYER_TOOLS,
    LAYER_TRACE,
    ContextBuilder,
)

__all__ = [
    "DEFAULT_CONDUCT_PROMPT",
    "DEFAULT_ROLE_PROMPT",
    "DEFAULT_SYSTEM_PROMPT",
    "LAYER_CONDUCT",
    "LAYER_PROFILE",
    "LAYER_ROLE",
    "LAYER_TOOLS",
    "LAYER_TRACE",
    "ContextBuilder",
]