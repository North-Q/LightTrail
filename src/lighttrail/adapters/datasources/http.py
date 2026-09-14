"""适配层对外门面：外部 HTTP 取数（B3-2）。

实现位于 lighttrail.infra.http（迁移期：域工具可直接复用），本模块只做 re-export；
TODO(B3-5/B5-4): 目标分层落地后把实现移入本包，域工具改经 ToolContext.datasource 端口注入。
"""

from __future__ import annotations

from lighttrail.infra.http import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_TIMEOUT,
    DataSourceError,
    get_json,
    get_json_sync,
)

__all__ = ["DEFAULT_MAX_ATTEMPTS", "DEFAULT_TIMEOUT", "DataSourceError", "get_json", "get_json_sync"]
