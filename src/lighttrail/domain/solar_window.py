"""太阳拍摄窗口的纯规则：晨暮以明确事件选择，保留缺失和绝对时间。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


def _time(value: Any) -> datetime | None:
    """只接受包含时区的 ISO 时间。"""
    try:
        result = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return result if result.tzinfo is not None else None


def resolve_solar_window(
    sun: dict[str, Any], date_iso: str, tz_offset: str, event: str, phase: str
) -> dict[str, Any]:
    """按实际目标日天文事件确定蓝调或跨日出/日落的拍摄区间。

    Args:
        sun: 本次完整太阳工具结果。
        date_iso: 请求时区中的目标日期。
        tz_offset: 请求固定偏移。
        event: sunrise 或 sunset，不从蓝调题材猜测。
        phase: blue 或 blue_through_event。

    Returns:
        含事件、阶段、绝对区间和缺失原因的目标事实，不造替代时刻。
    """
    label = {"sunrise": "日出", "sunset": "日落"}.get(event, "")
    result: dict[str, Any] = {
        "事件": label,
        "阶段": phase,
        "日期": date_iso,
        "时区": tz_offset,
        "事件时刻": None,
        "开始": None,
        "结束": None,
        "缺失原因": [],
        "近似": "太阳高度角窗口按5分钟采样，非精确边界",
    }
    missing: list[str] = result["缺失原因"]
    if phase not in {"blue", "blue_through_event"}:
        missing.append("拍摄阶段未明确或不支持")
        return result
    if not label:
        missing.append("蓝调未明确晨/暮事件或晨暮要求冲突")
        return result
    if sun.get("日期") != date_iso or sun.get("时区") != tz_offset or sun.get("error"):
        missing.append("太阳数据日期、时区不一致或计算失败")
        return result
    moment = _time(sun.get(f"{label}_iso"))
    if moment is None:
        state = sun.get("事件状态", {}).get(label, {})
        missing.append(f"{label}：{state.get('状态', '缺少事件')}；{state.get('原因', '')}")
        return result
    local_midnight = _time(f"{date_iso}T00:00:00{tz_offset}")
    if (
        local_midnight is None
        or moment.astimezone(local_midnight.tzinfo).date().isoformat() != date_iso
    ):
        missing.append(f"{label}绝对时刻不属于目标本地日期")
        return result
    result["事件时刻"] = moment.isoformat()
    period = "晨" if event == "sunrise" else "暮"

    def bounds(name: str) -> tuple[datetime, datetime] | None:
        """校验真实窗口，事件缺失或单窗口归属不确定时不猜测。"""
        windows = sun.get(f"{name}（{period}）")
        if not isinstance(windows, list) or len(windows) != 1 or not isinstance(windows[0], dict):
            state = sun.get("事件状态", {}).get(name, {})
            missing.append(
                f"{name}（{period}）：{state.get('状态', '缺少窗口')}；{state.get('原因', '')}"
            )
            return None
        start, end = _time(windows[0].get("开始_iso")), _time(windows[0].get("结束_iso"))
        if start is None or end is None or not start < end:
            missing.append(f"{name}（{period}）缺少有效绝对区间")
            return None
        if name == "蓝调时刻" and not (end <= moment if event == "sunrise" else start >= moment):
            missing.append(f"{name}窗口与{label}晨暮事件不一致")
            return None
        if name == "黄金时刻" and not start < moment < end:
            missing.append(f"{name}窗口未覆盖{label}")
            return None
        if min(abs(start - moment), abs(end - moment)) > timedelta(hours=3):
            missing.append(f"{name}窗口离目标事件过远，无法核对归属")
            return None
        return start, end

    blue = bounds("蓝调时刻")
    golden = bounds("黄金时刻") if phase == "blue_through_event" else None
    if blue is not None and (phase == "blue" or golden is not None):
        start, end = blue
        if golden is not None:
            start, end = (blue[0], golden[1]) if event == "sunrise" else (golden[0], blue[1])
        result.update({"开始": start.isoformat(), "结束": end.isoformat()})
    return result
