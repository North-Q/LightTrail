"""数据源端口契约（B1-4，§2.3）。

设计要点：
- 所有外部数据（天气 / 天文 / 地图 / 光污染…）统一经本端口进出：实现放 adapters
  （httpx，B3-2 落地），业务层不直接触网；
- 返回 dict 而非类型化模型：数据源返回的是**原始数据**（需要留痕、需要展示来源），
  领域侧的判据计算在工具层完成；
- async-first：端口即异步（B3 起管线并行取数用 asyncio.TaskGroup）。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DataSource(Protocol):
    """外部数据源端口（天气 / 天文 / 地图等）。"""

    async def get(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        """取一份外部数据。

        Args:
            name: 数据源名称（如 weather / astronomy / map）。
            params: 数据源参数（经纬度、日期、时区…）。

        Returns:
            数据字典（含数据来源标注键，供可解释性展示）。

        Raises:
            实现自定义异常：取数失败（上层按管线降级策略处理）。
        """
        ...


__all__ = ["DataSource"]