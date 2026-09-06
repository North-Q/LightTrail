"""机位 × 天象匹配工具（决策主线 D2.1-03 的轻型实现）。

输入候选机位列表，为每个机位计算相关天象事件，并按「题材朝向 × 天象窗口」
给出 0-5 分匹配度与排序。光污染、可达性、地形遮挡等外部因素由上层
（模型 / 记忆档案）补充，本工具只保证天文部分的透明可解释。

对外工具：
- match_sites：多机位 × 指定日期的天象条件对比排序。
"""

from __future__ import annotations

from typing import Any

from lighttrail.agent.tools import registry
from lighttrail.tools.astronomy import (
    _DIRECTION_NAMES,
    _parse_date,
    _validate_latlon,
    galaxy_visibility,
    moon_phase,
    sun_times,
)

# 支持题材 → 关注的天象事件类型
_THEME_EVENT = {
    "日出": "sunrise",
    "日落": "sunset",
    "蓝调": "sunset",
    "黄金": "sunset",
    "银河": "galaxy",
    "星空": "galaxy",
    "夜景": "night",
}

# 方位差（度）→ 扣分（总分 5）
_PENALTY_BY_DELTA = [
    (15.0, 0),
    (30.0, 1),
    (45.0, 2),
    (float("inf"), 3),
]


def _resolve_azimuth(朝向: Any) -> float | None:
    """把朝向（方位词 / 度数 / None）解析为自北顺时针的方位角，无法解析返回 None。"""
    if 朝向 is None:
        return None
    if isinstance(朝向, (int, float)):
        return float(朝向) % 360.0
    text = str(朝向).strip()
    if text in _DIRECTION_NAMES:
        return _DIRECTION_NAMES.index(text) * 22.5
    try:
        return float(text) % 360.0
    except ValueError:
        return None


def _angular_delta(a: float, b: float) -> float:
    """两个方位角（度）之间的最小夹角。"""
    diff = abs(a - b) % 360.0
    return min(diff, 360.0 - diff)


# ------------------------------------------------------------------
# 对外工具
# ------------------------------------------------------------------
@registry.tool(
    name="match_sites",
    description=(
        "机位 × 天象匹配：给定候选机位列表（名称/经纬度/题材/朝向）与日期，"
        "计算每个机位的日落日出、蓝调黄金窗口、银河可见窗口与月相，并按"
        "『题材朝向与天象方位的一致程度』给出 0-5 分匹配度与排序。"
        "题材支持：日出/日落/蓝调/黄金/银河/星空/夜景；朝向可填方位词"
        "（东南/西南/正西等）或方位角（度）。"
        "用途：多机位挑选『今天这个题材去哪拍最合适』。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "sites": {
                "type": "array",
                "description": "候选机位列表，每项：名称、纬度、经度、题材、朝向（可选）",
                "items": {
                    "type": "object",
                    "properties": {
                        "名称": {"type": "string", "description": "机位名称，如 滨江大道"},
                        "纬度": {"type": "number", "description": "纬度（度，北纬为正）"},
                        "经度": {"type": "number", "description": "经度（度，东经为正）"},
                        "题材": {
                            "type": "string",
                            "description": "拍摄题材：日出/日落/蓝调/黄金/银河/星空/夜景",
                        },
                        "朝向": {
                            "type": "string",
                            "description": "机位视线朝向（方位词或度），如 西南 或 225；不填则只评分不扣朝向分",
                        },
                    },
                    "required": ["名称", "纬度", "经度", "题材"],
                },
            },
            "date": {
                "type": "string",
                "description": "日期（本地时区），如 2026-08-16",
            },
            "tz_offset": {
                "type": "string",
                "description": "时区偏移，如 +08:00（默认，北京时间）",
                "default": "+08:00",
            },
        },
        "required": ["sites", "date"],
    },
)
def match_sites(sites: list[dict[str, Any]], date: str, tz_offset: str = "+08:00") -> dict:
    """对候选机位做天象匹配排序。

    Args:
        sites: 候选机位列表（名称/纬度/经度/题材/可选朝向）。
        date: 本地日期（YYYY-MM-DD）。
        tz_offset: 时区偏移字符串。

    Returns:
        按匹配度降序排列的机位评估列表。
    """
    day = _parse_date(date)
    if day is None:
        return {"error": "日期格式须为 YYYY-MM-DD"}
    if not sites:
        return {"error": "sites 不能为空"}

    ranked = []
    for index, site in enumerate(sites):
        if not isinstance(site, dict):
            return {"error": f"第 {index + 1} 个机位格式不正确（须为对象）"}
        name = site.get("名称", f"机位{index + 1}")
        latitude = site.get("纬度")
        longitude = site.get("经度")
        theme = site.get("题材")
        if latitude is None or longitude is None or theme is None:
            return {"error": f"机位「{name}」缺少纬度/经度/题材字段"}
        if theme not in _THEME_EVENT:
            return {
                "error": f"机位「{name}」题材不支持：{theme}（支持：{'/'.join(sorted(_THEME_EVENT))})"
            }
        lat_error = _validate_latlon(latitude, longitude)
        if lat_error:
            return {"error": f"机位「{name}」{lat_error}"}

        event_type = _THEME_EVENT[theme]
        if event_type == "galaxy":
            entry = _evaluate_galaxy_site(
                name, latitude, longitude, theme, day.isoformat(), tz_offset
            )
        else:
            entry = _evaluate_sun_site(
                name, latitude, longitude, theme, site.get("朝向"), day.isoformat(), tz_offset
            )
        ranked.append(entry)

    ranked.sort(
        key=lambda item: item["匹配度"] if isinstance(item["匹配度"], int) else -1, reverse=True
    )
    return {
        "日期": day.isoformat(),
        "时区": tz_offset,
        "排序结果": ranked,
        "提示": "匹配度仅依据天文条件（题材朝向 × 天象窗口）；光污染/可达性/地形遮挡需结合记忆档案与地图数据进一步判断",
    }


# ------------------------------------------------------------------
# 内部评估
# ------------------------------------------------------------------
def _minute_of_day(value: str) -> int:
    """HH:MM 转为当日分钟数。"""
    hours, minutes = (int(part) for part in value.split(":"))
    return hours * 60 + minutes


def _window_total_hours(windows: list[dict]) -> float:
    """统计银心窗口合计时长（小时），窗口可跨午夜。"""
    total_minutes = 0
    for window in windows:
        start = _minute_of_day(window["开始"])
        end = _minute_of_day(window["结束"])
        if end < start:
            end += 1440
        total_minutes += end - start
    return total_minutes / 60.0


def _evaluate_sun_site(
    name: str, latitude: float, longitude: float, theme: str, facing: Any, date: str, tz_offset: str
) -> dict:
    """评估日出/日落/蓝调/黄金题材机位：按目标天象方位与拍摄朝向的偏差评分。"""
    st = sun_times(latitude, longitude, date, tz_offset)
    if "error" in st:
        return {"名称": name, "题材": theme, "匹配度": None, "依据": [st["error"]]}

    is_morning = theme == "日出"
    target_azimuth = float(st["日出方位（度）"] if is_morning else st["日落方位（度）"])
    event_label = "日出" if is_morning else "日落"
    event_time = st["日出"] if is_morning else st["日落"]
    preferred = _resolve_azimuth(facing)

    if preferred is None:
        score = 5 if st.get(event_label) else 0
        reasons = ["未提供朝向，仅按当天该天象存在性评分，建议补充朝向字段"]
    else:
        delta = _angular_delta(target_azimuth, preferred)
        score = 5
        for limit, penalty in _PENALTY_BY_DELTA:
            if delta <= limit:
                score -= penalty
                break
        score = max(0, score)
        reasons = [
            f"拍摄朝向 {facing}，与{event_label}方位 {round(target_azimuth)}° 偏差 {round(delta)}°"
        ]

    window_note = ""
    if theme == "蓝调":
        evening_blue = st.get("蓝调时刻（暮）")
        if evening_blue:
            window_note = f"，傍晚蓝调 {evening_blue[0]['开始']}-{evening_blue[0]['结束']}"
    elif theme == "黄金":
        evening_golden = st.get("黄金时刻（暮）")
        if evening_golden:
            window_note = f"，傍晚黄金 {evening_golden[0]['开始']}-{evening_golden[0]['结束']}"
    reasons.append(f"{event_label} {event_time}{window_note}")

    return {
        "名称": name,
        "题材": theme,
        "匹配度": score,
        "关键天象": {
            "日出": st["日出"],
            "日落": st["日落"],
            "日落方位（度）": st["日落方位（度）"],
        },
        "依据": reasons,
    }


def _evaluate_galaxy_site(
    name: str, latitude: float, longitude: float, theme: str, date: str, tz_offset: str
) -> dict:
    """评估银河/星空题材机位：按银心可见时长与月光干扰评分。"""
    galaxy = galaxy_visibility(latitude, longitude, date, tz_offset, min_altitude=5.0)
    moon = moon_phase(date, tz_offset)
    reasons: list[str] = []
    score = 2

    if "error" in galaxy:
        return {"名称": name, "题材": theme, "匹配度": None, "依据": [galaxy["error"]]}
    windows = galaxy.get("可见窗口", [])
    if not windows:
        reasons.append("银心整夜高度不足 5°，不建议安排银河题材")
    else:
        total_hours = _window_total_hours(windows)
        if total_hours >= 3.0:
            score = 5
        elif total_hours >= 1.5:
            score = 4
        elif total_hours >= 0.5:
            score = 3
        reasons.append(
            f"银心可见约 {total_hours:.1f} 小时，最高高度角 {galaxy.get('整夜最高高度角（度）')}°"
        )

    moon_percent = moon.get("照亮比例（%）", 50) or 50
    if moon_percent >= 40:
        score = max(0, score - 1)
        reasons.append(f"月光干扰明显（照亮 {moon_percent}%），银河对比度受影响")
    else:
        reasons.append(f"月光干扰小（照亮 {moon_percent}%）")

    window_desc = "；".join(f"{w['开始']}-{w['结束']}" for w in windows) or "无"
    return {
        "名称": name,
        "题材": theme,
        "匹配度": score,
        "关键天象": {
            "银心窗口": window_desc,
            "月相": moon.get("月相名称"),
            "月光（%）": moon_percent,
        },
        "依据": reasons,
    }
