"""适配层：外部数据源（httpx + tenacity 统一重试，B3-2）。

设计要点：
- 所有外部 HTTP 取数走本包：唯一 HTTP 客户端（httpx）、唯一定义的退避策略（tenacity）；
- 实现 `contracts.DataSource` 语义（`get(name, params) -> dict`）的 HTTP 基件在此；
- 只依赖 contracts 与第三方库，不被 domain/runtime 反向依赖。
"""

from __future__ import annotations

from lighttrail.adapters.datasources.http import DataSourceError, get_json, get_json_sync

__all__ = ["DataSourceError", "get_json", "get_json_sync"]