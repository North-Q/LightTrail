"""内置工具：基础通用工具。"""

from __future__ import annotations

from datetime import datetime, timezone

from lighttrail.agent.tools import registry


@registry.tool(
    name="get_current_time",
    description=(
        "获取当前日期时间。当用户询问『现在几点』『今天几号』『还剩多久』等时间相关问题，"
        "或需要基于当前时间做判断（如日出日落时刻是否已过）时必须调用本工具。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "tz": {
                "type": "string",
                "description": "时区偏移，如 +08:00（默认，北京时间）或 UTC；缺省返回本地时间",
            }
        },
    },
)
def get_current_time(tz: str = "+08:00") -> dict:
    """返回指定时区的当前日期时间（ISO 8601 格式）。"""
    try:
        sign = 1 if tz.startswith("+") else -1
        hours, minutes = map(int, tz.lstrip("+-").split(":"))
        tzinfo = timezone(sign * (hours * 3600 + minutes * 60))
    except Exception:  # noqa: BLE001  时区解析失败回退 UTC
        tzinfo = timezone.utc
    now = datetime.now(tzinfo)
    return {
        "iso": now.isoformat(timespec="seconds"),
        "weekday": now.strftime("%A"),
        "note": "如需换算其他时区请说明具体偏移",
    }
