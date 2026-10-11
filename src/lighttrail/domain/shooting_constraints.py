"""从本轮明确声明提取拍摄前提，不从模型输出或摄影偏好推断器材。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

_NUMBER = r"(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百半]+)"
_DURATION = rf"{_NUMBER}\s*(?:小时|分钟|分)"
_CLOCK = r"(?:[01]?\d|2[0-3]):[0-5]\d"
_FOCAL = r"\d+(?:\s*[-–—]\s*\d+)?\s*mm"
_APERTURE = r"[fF]\s*/?\s*(\d+(?:\.\d+)?)"
_UNCONFIRMED_APERTURE = (
    r"未知|未确认|不确定|没确认|尚未确认|待确认|可能|如果|假如|假设|若|"
    r"不是|并非|不为|不能确认|无法确认"
)
_DIGITS = {char: value for value, chars in enumerate("零一二三四五六七八九") for char in chars}
_DIGITS.update({"〇": 0, "两": 2})


def _number(text: str) -> float:
    """转换有限的中文时长数字；不认识的表达保持不可解析。"""
    if text == "半":
        return 0.5
    if text.isascii():
        return float(text)
    for unit, scale in (("百", 100), ("十", 10)):
        if unit in text:
            left, right = text.split(unit, 1)
            return (float(_DIGITS[left]) if left else 1) * scale + (_number(right) if right else 0)
    return float(_DIGITS[text])


def _minutes(text: str, label: str) -> tuple[float | None, list[str]]:
    """读取标签前后明确时长；多值冲突或提及但未明确时要求确认。"""
    pattern = rf"(?:{label})\s*(?:时间|需要|耗时|至少|约|为|[:：])?\s*((?:{_DURATION}\s*)+)"
    reverse = rf"((?:{_DURATION}\s*)+)\s*(?:的)?(?:{label})"
    values: set[float] = set()
    for clause in re.split(r"[，,。；;\n]", text):
        if re.search(label, clause) and re.search(rf"(?:或|至|到|[-–~～])\s*{_DURATION}", clause):
            return None, ["通勤或布置存在多个可能时长，请确认保守分钟数"]
    for expression in (pattern, reverse):
        for match in re.finditer(expression, text):
            try:
                value = sum(
                    _number(number) * (60 if unit == "小时" else 1)
                    for number, unit in re.findall(rf"({_NUMBER})\s*(小时|分钟|分)", match[1])
                )
            except (KeyError, ValueError):
                continue
            if 0 <= value <= 24 * 60:
                values.add(value)
    if len(values) == 1:
        return values.pop(), []
    if re.search(label, text):
        return None, ["通勤或布置时长未明确、冲突或超出一天，请确认分钟数"]
    return None, []


def _equipment_state(text: str, label: str) -> bool | None:
    """只接受本轮明确持有/携带；否定与不确定声明优先。"""
    clauses = re.split(r"[，,。；;\n]", text)
    relevant = [clause for clause in clauses if re.search(label, clause, re.IGNORECASE)]
    if any(
        re.search(r"未知|未确认|不确定|没确认|尚未确认|待确认|可能|如果|假设|若", c)
        for c in relevant
    ):
        return None
    if any(re.search(r"没有|没带|不带|不携带|无|不具备", c) for c in relevant):
        return False
    if any(
        re.search(
            rf"(?:已确认|确认)?(?:携带|带了|带|有|持有)\s*(?:了)?(?:{label})", c, re.IGNORECASE
        )
        or re.search(
            rf"(?:{label})\s*(?:[:：]\s*)?(?:已确认携带|已携带|已具备|有)", c, re.IGNORECASE
        )
        for c in relevant
    ):
        return True
    return None


def focal_key(text: str) -> str:
    """统一焦段标识，保持14mm与24–105mm的最大光圈分别归属。"""
    return re.sub(r"\s+", "", text.lower().replace("–", "-").replace("—", "-"))


def normalize_clock_text(text: str) -> str:
    """归一数字或简单中文钟点；无法解析的钟点由最终护栏拒绝。"""

    def replace_clock(match: re.Match[str]) -> str:
        """将四点三十二分等表达转为可核验钟点。"""
        try:
            hour = _number(match[1])
            minute = _number(match[2]) if match[2] else 0
        except (KeyError, ValueError):
            return match[0]
        if hour.is_integer() and minute.is_integer() and 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{int(hour):02d}:{int(minute):02d}"
        return match[0]

    normalized = re.sub(
        rf"({_NUMBER})[点时](?:({_NUMBER})分?)?", replace_clock, text.replace("：", ":")
    )
    return re.sub(r"(?<!\d)(\d):([0-5]\d)", r"0\1:\2", normalized)


def _request_aperture_limits(text: str) -> dict[str, float | None]:
    """核验本轮有限的最大光圈声明；条件、否定和冲突不认证为规格。

    Args:
        text: 本轮原文，不采用模型或历史消息提供的确认。

    Returns:
        按焦段归属的本轮规格；不确定或多值冲突为None，优先于旧档案。
    """
    values: dict[str, set[float]] = {}
    unknown: set[str] = set()
    for clause in re.split(r"[，,。；;\n]", text):
        if "光圈" not in clause:
            continue
        if re.search(_UNCONFIRMED_APERTURE, clause):
            unknown.update(
                focal_key(match[0]) for match in re.finditer(_FOCAL, clause, re.IGNORECASE)
            )
            continue
        for match in re.finditer(
            rf"({_FOCAL})\s*(?:镜头)?\s*最大光圈\s*(?:为|是|[:：])?\s*{_APERTURE}",
            clause,
            re.IGNORECASE,
        ):
            values.setdefault(focal_key(match[1]), set()).add(float(match[2]))
    return {
        focal: next(iter(values[focal]))
        if focal not in unknown and len(values[focal]) == 1
        else None
        for focal in values.keys() | unknown
    }


def ground_shooting_constraints(
    request: str, target_date: str, tz_offset: str, lenses: list[str]
) -> dict[str, Any]:
    """提取本轮行程和镜头事实，未知字段保留空值。

    Args:
        request: 本轮原始请求，不采用模型提取的额外事实。
        target_date: 已核验的本地目标日。
        tz_offset: 已核验的固定时区偏移。
        lenses: 本人档案中的原始镜头声明，不采用相机规格推断光圈。

    Returns:
        可保存至完整快照的中文键前提；解析不明时附确认原因。
    """
    text = normalize_clock_text(request)
    commute, problems = _minutes(text, "单程|通勤|路程")
    setup, setup_problems = _minutes(text, "布置|准备")
    problems.extend(setup_problems)
    earliest = None
    clocks = re.findall(rf"最早\s*(?:可|能|可以)?\s*({_CLOCK})\s*出发", text)
    clocks.extend(re.findall(rf"最早\s*(?:可|能|可以)?\s*出发\s*:?\s*({_CLOCK})", text))
    if len(set(clocks)) == 1:
        earliest = datetime.fromisoformat(f"{target_date}T{clocks[0]}:00{tz_offset}")
    elif "最早" in text and "出发" in text:
        problems.append("最早出发时刻未明确或冲突，请提供目标日的HH:MM")
    if commute is not None and setup is None:
        problems.append("已知通勤时长，但布置时间未确认，不按零分钟处理")
    if setup is not None and commute is None:
        problems.append("已知布置时间，但单程通勤时长未确认，不按零分钟处理")
    ready = (
        earliest + timedelta(minutes=commute + setup)
        if earliest is not None and commute is not None and setup is not None
        else None
    )
    limits: dict[str, float | None] = {}
    for lens in lenses:
        focal = re.search(_FOCAL, lens, re.IGNORECASE)
        aperture = re.search(_APERTURE, lens)
        if aperture and (
            re.search(r"未知|未确认|不确定|待确认|可能|假设|\?", lens)
            or re.match(r"\s*[-–—]\s*\d", lens[aperture.end() :])
        ):
            aperture = None  # 变光圈镜头不能按整段焦距沿用最小f值。
        if focal:
            limits[focal_key(focal[0])] = float(aperture[1]) if aperture else None
    limits.update(_request_aperture_limits(text))
    return {
        "单程通勤分钟": commute,
        "布置分钟": setup,
        "最早出发": earliest.isoformat() if earliest else None,
        "最早开拍": ready.isoformat() if ready else None,
        "三脚架": _equipment_state(text, "三脚架"),
        "ND": _equipment_state(text, r"ND(?:\d+)?|减光镜"),
        "ND型号": sorted(set(re.findall(r"(?<![A-Za-z])ND\d+(?![A-Za-z\d])", text, re.IGNORECASE))),
        "镜头最大光圈": limits,
        "待确认": list(dict.fromkeys(problems)),
    }
