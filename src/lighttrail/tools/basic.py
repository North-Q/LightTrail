"""内置工具：基础通用工具。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from lighttrail.contracts.tool import Confidence, Tool, ToolSpec
from lighttrail.tools._base import PureTool

_SPEC_GET_CURRENT_TIME = ToolSpec(
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
                "description": "固定时区偏移，如 +08:00（默认，北京时间）或 UTC；不自动处理夏令时",
            }
        },
    },
    capabilities=frozenset(["tools"]),
    main_field="时间",
    confidence=Confidence.HIGH,
)


def get_current_time(tz: str = "+08:00") -> dict:
    """返回指定时区的当前日期时间（ISO 8601 格式）。"""
    text = tz.strip().upper()
    if text in {"UTC", "Z"}:
        tzinfo = timezone.utc
    elif re.fullmatch(r"[+-]\d{2}:\d{2}", text):
        sign = 1 if text.startswith("+") else -1
        hours, minutes = map(int, text[1:].split(":"))
        if hours >= 24 or minutes >= 60:
            return {"error": "时区须为合法的 ±HH:MM 偏移或 UTC"}
        tzinfo = timezone(sign * timedelta(hours=hours, minutes=minutes))
    else:
        return {"error": "时区须为合法的 ±HH:MM 偏移或 UTC"}
    now = datetime.now(tzinfo)
    return {
        "iso": now.isoformat(timespec="seconds"),
        "weekday": now.strftime("%A"),
        "note": "如需换算其他时区请说明具体偏移",
    }


# ------ 声明式收集点（ToolSpec 真源；新增工具 = 加一行）------
TOOLS: tuple[Tool, ...] = (PureTool(spec=_SPEC_GET_CURRENT_TIME, func=get_current_time),)
