"""天气证据时效规则：获取时间、预报覆盖与目标绝对时刻分别核验。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

# 本地决策的新鲜度护栏，工程阈值不代表概率校准。
MAX_FORECAST_AGE = timedelta(hours=6)


def aware_time(value: Any) -> datetime | None:
    """解析必须带时区的时间，无效值保留为缺失。"""
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def forecast_problem(result: dict[str, Any], now: datetime) -> str:
    """检查获取时间及有效覆盖范围，冻结数据不得因为重查而续期。

    Args:
        result: 实际工具结果。
        now: 含时区的当前请求时间。

    Returns:
        缺失或过期原因；合格时为空字符串。
    """
    fetched = aware_time(result.get("获取时间"))
    start = aware_time(result.get("有效开始时间"))
    end = aware_time(result.get("有效结束时间"))
    if fetched is None or start is None or end is None:
        return "缺少含时区的获取时间或预报有效范围"
    if fetched > now + timedelta(minutes=5):
        return "获取时间晚于请求时间"
    if now - fetched > MAX_FORECAST_AGE:
        return "预报获取已超过 6 小时，请更新数据"
    if end <= start or end <= now:
        return "预报有效范围已过期或无效"
    target = aware_time(result.get("目标时刻"))
    if target is not None and not start <= target < end:
        return "目标时刻不在预报覆盖范围"
    return ""
