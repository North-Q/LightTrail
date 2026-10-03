"""L2/L3 共用的离线语义断言：把请求事实、工具实参和卡片结论逐项对账。"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from lighttrail.contracts.models import DecisionCard

_ISO_DATE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")


def _path_value(data: dict[str, Any], path: list[str]) -> Any:
    """读取夹具中指定的工具事实路径。"""
    current: Any = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            raise KeyError(".".join(path))
        current = current[key]
    return current


def _request_day(case: dict[str, Any]) -> str:
    """按用例固定请求时刻与时区得出本地日期。"""
    context = case["request_context"]
    offset = context["tz_offset"]
    sign = 1 if offset[0] == "+" else -1
    zone = timezone(sign * timedelta(hours=int(offset[1:3]), minutes=int(offset[4:6])))
    return datetime.fromisoformat(context["requested_at"]).astimezone(zone).date().isoformat()


def assert_semantics(
    card: DecisionCard,
    case: dict[str, Any],
    cassette: dict[str, Any],
    tool_calls: list[tuple[str, str]],
    model_calls: list[dict[str, Any]],
) -> list[str]:
    """核对目标、工具选择与参数、关键事实、降级和个性化来源。

    Args:
        card: 生产管线的实际输出。
        case: 与 cassette 分离的黄金期望和固定请求上下文。
        cassette: 本用例独占的合成离线响应与工具数据。
        tool_calls: 实际工具名和 JSON 参数。
        model_calls: 本用例实际模型调用记录，仅用于核查记忆注入。

    Returns:
        可定位的违规清单；空列表表示本用例语义断言全部满足。
    """
    expected = case.get("semantic") or {}
    if not expected:
        return []
    problems: list[str] = []
    target_date = expected["target_date"]
    location = expected.get("location", "")
    subject = expected["subject_type"]
    if card.target_date != target_date:
        problems.append(f"目标日期 {card.target_date!r} != {target_date!r}")
    if card.subject_type != subject:
        problems.append(f"题材 {card.subject_type!r} != {subject!r}")
    names = [item.name for item in card.locations]
    if names != ([location] if location else []):
        problems.append(f"机位 {names!r} != {[location] if location else []!r}")
    if card.degraded != expected.get("degraded", ""):
        problems.append(f"降级 {card.degraded!r} != {expected.get('degraded', '')!r}")
    if card.verdict != expected["verdict"]:
        problems.append(f"行动结论 {card.verdict!r} != {expected['verdict']!r}")

    visible = f"{card.conclusion} {card.time_window}"
    if expected.get("require_action"):
        for label, value in (("地点", location), ("日期", target_date), ("题材", subject)):
            if value and value not in card.conclusion:
                problems.append(f"结论缺少本次{label}：{value}")
    for wrong in expected.get("forbidden_locations", []):
        if wrong in visible:
            problems.append(f"结论出现错误地点：{wrong}")
    for wrong in expected.get("forbidden_subjects", []):
        if wrong in visible:
            problems.append(f"结论出现错误题材：{wrong}")
    for mentioned in _ISO_DATE.findall(visible):
        if mentioned != target_date:
            problems.append(f"结论出现错误日期：{mentioned}")
    for field in ("window_start", "window_end"):
        value = getattr(card, field)
        if value:
            try:
                window_day = datetime.fromisoformat(value).date().isoformat()
            except ValueError:
                problems.append(f"{field} 时间格式错误：{value}")
                continue
            allowed = {target_date}
            if field == "window_end":
                allowed.add(
                    (datetime.fromisoformat(target_date).date() + timedelta(days=1)).isoformat()
                )
            if window_day not in allowed:
                problems.append(f"{field} 出现错误日期：{value}")
    request_day = _request_day(case)
    if "今晚" in visible and target_date != request_day:
        problems.append("结论把非当日目标写成今晚")
    tomorrow = (datetime.fromisoformat(request_day).date() + timedelta(days=1)).isoformat()
    if "明天" in visible and target_date != tomorrow:
        problems.append("结论把非次日目标写成明天")

    actual_tools = [name for name, _ in tool_calls]
    required = expected.get("tools", [])
    if sorted(actual_tools) != sorted(required):
        problems.append(f"工具选择 {sorted(actual_tools)!r} != {sorted(required)!r}")
    for name, raw in tool_calls:
        args = json.loads(raw)
        if "date" in args and args["date"] != target_date:
            problems.append(f"{name} 工具日期 {args['date']} != {target_date}")
        if args.get("tz_offset") != case["request_context"]["tz_offset"]:
            problems.append(f"{name} 工具时区错误")
        if name == "match_sites":
            wanted = expected.get("match_subject")
            if wanted and any(site.get("题材") != wanted for site in args.get("sites", [])):
                problems.append(f"match_sites 未使用本次题材 {wanted}")
        coords = expected.get("coordinates")
        if (
            coords
            and "latitude" in args
            and "longitude" in args
            and (args["latitude"], args["longitude"]) != tuple(coords)
        ):
            problems.append(f"{name} 使用了错误坐标")

    payloads = cassette.get("tool_results") or {}
    for tool in actual_tools:
        payload = payloads.get(tool)
        if not isinstance(payload, dict):
            problems.append(f"{tool} 缺少本用例独立的工具响应")
            continue
        if "error" in payload and tool not in expected.get("unavailable_tools", []):
            problems.append(f"{tool} 意外失效：{payload['error']}")
    weather = payloads.get("weather_forecast", {})
    if "weather_forecast" in actual_tools and "error" not in weather:
        forecast_days = [row.get("日期") for row in weather.get("每日预报", [])]
        if target_date not in forecast_days:
            problems.append(f"天气结果不包含目标日期：{target_date}")
    for item in card.evidence:
        if item.tool == "user_profile":
            if not case.get("profile"):
                problems.append("卡片引用了不存在的用户档案")
        elif item.tool not in actual_tools:
            problems.append(f"卡片依据引用未调用的工具：{item.tool}")
        elif item.field not in payloads.get(item.tool, {}):
            problems.append(f"卡片依据字段不在工具结果中：{item.tool}/{item.field}")
    for fact in expected.get("facts", []):
        tool = fact["tool"]
        try:
            actual = _path_value(payloads[tool], fact["path"])
        except (KeyError, TypeError):
            problems.append(f"缺少可核查的工具事实：{tool}/{'.'.join(fact['path'])}")
            continue
        if actual != fact["value"]:
            problems.append(
                f"工具事实 {tool}/{'.'.join(fact['path'])} = {actual!r}，预期 {fact['value']!r}"
            )
        if tool not in actual_tools:
            problems.append(f"卡片引用未调用的工具事实：{tool}")
        if not any(
            item.tool == tool
            and item.field == fact["path"][-1]
            and str(fact["value"]) in (item.note or "")
            for item in card.evidence
        ):
            problems.append(f"卡片缺少与工具结果一致的依据：{tool}/{fact['path'][-1]}")
        if fact.get("claim_in_conclusion") and str(fact["value"]) not in card.conclusion:
            problems.append(f"结论未引用核查过的工具事实：{tool}/{fact['path'][-1]}")
    for tool in expected.get("unavailable_tools", []):
        if "error" not in payloads.get(tool, {}):
            problems.append(f"失效场景未注入 {tool} 错误")
        if any(item.tool == tool for item in card.evidence):
            problems.append(f"卡片把失效工具 {tool} 当作有效依据")

    camera = expected.get("profile_camera")
    if camera:
        profile = case.get("profile") or {}
        if profile.get("camera_body") != camera:
            problems.append("个性化器材与档案夹具不一致")
        prompts = "\n".join(
            str(message.get("content", ""))
            for call in model_calls
            for message in call.get("messages", [])
        )
        if camera not in prompts:
            problems.append("模型提示词未注入当前用户器材")
        if not any(
            item.tool == "user_profile" and camera in (item.note or "") for item in card.evidence
        ):
            problems.append("卡片没有可追溯的用户器材依据")
    return problems


__all__ = ["assert_semantics"]
