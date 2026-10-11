"""天气查询工具：接入 Open-Meteo 免费预报接口（无需 API Key）。

设计要点：
- 数据源可替换：Base URL 可通过环境变量 OPEN_METEO_BASE_URL 覆盖（测试/自建镜像）；
- Open-Meteo 请求 GMT 时间，本模块按绝对时刻转换到请求时区；
- 火烧云启发式评分综合高云占比、云量区间、能见度、降水与风速，
  输出依据字段保证可解释性（决策地基 M2）。

对外工具：
- weather_forecast：未来 1-7 天每日云量 / 能见度 / 降水概率 / 风速摘要；
- sunset_glow_score：指定日期傍晚火烧云启发式评分（结合天文日落时刻）。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from lighttrail.contracts.tool import Confidence, Tool, ToolSpec
from lighttrail.infra.http import DataSourceError, get_json_sync
from lighttrail.tools._base import PureTool
from lighttrail.tools.astronomy import _parse_date, _parse_tz_offset, sun_times

logger = logging.getLogger("lighttrail.tools.weather")

# Open-Meteo 免费接口（可通过环境变量替换数据源）
_DEFAULT_BASE_URL = "https://api.open-meteo.com/v1/forecast"
# 网络超时（秒）
_REQUEST_TIMEOUT = 15.0
# 请求失败重试次数（不含首次）
_MAX_RETRIES = 2


class WeatherError(Exception):
    """天气数据获取或解析失败。"""


def _base_url() -> str:
    """返回 Open-Meteo Base URL（支持环境变量覆盖）。"""
    return os.getenv("OPEN_METEO_BASE_URL", _DEFAULT_BASE_URL)


def _fetch_json(url: str) -> dict[str, Any]:
    """GET 请求并解析 JSON（B3-2：httpx + tenacity 统一重试，与 llm/client 语义对齐）。

    Raises:
        WeatherError: 请求失败（含 4xx 参数错误与重试耗尽的 5xx/网络错误）。
    """
    try:
        return get_json_sync(
            url,
            headers={"User-Agent": "lighttrail/0.1"},
            timeout=_REQUEST_TIMEOUT,
        )
    except DataSourceError as exc:
        raise WeatherError(f"天气数据获取失败：{exc}") from exc


_WEATHER_FIELDS = (
    "cloud_cover",
    "cloud_cover_high",
    "cloud_cover_low",
    "visibility",
    "precipitation_probability",
    "wind_speed_10m",
)


def _now() -> datetime:
    """返回含时区的采集时刻，测试可替换。"""
    return datetime.now(timezone.utc)


def _hourly_rows(data: dict[str, Any], tz_offset: str) -> list[dict[str, Any]]:
    """依据供应商偏移解析绝对时间，再转换到请求时区。

    Args:
        data: 供应商小时预报对象；GMT 请求缺省偏移为零。
        tz_offset: 请求的显示及目标日期时区。

    Returns:
        含绝对时刻及可空数值的小时列表。
    """
    hourly = data.get("hourly", {})
    source_tz = timezone(timedelta(seconds=data.get("utc_offset_seconds", 0)))
    target_tz = _parse_tz_offset(tz_offset)
    rows: list[dict[str, Any]] = []
    for index, raw_time in enumerate(hourly.get("time", [])):
        parsed = datetime.fromisoformat(raw_time)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=source_tz)
        row: dict[str, Any] = {"时间": parsed.astimezone(target_tz).isoformat()}
        for field in _WEATHER_FIELDS:
            values = hourly.get(field, [])
            row[field] = values[index] if index < len(values) else None
        rows.append(row)
    return rows


def _metadata(
    rows: list[dict[str, Any]],
    latitude: float,
    longitude: float,
    tz_offset: str,
    fetched_at: datetime,
) -> dict[str, Any]:
    """记录采集时间与预报有效覆盖；采集时间不冒充供应商发布时间。"""
    times = sorted(datetime.fromisoformat(row["时间"]) for row in rows)
    return {
        "位置": f"{latitude},{longitude}",
        "时区": tz_offset,
        "获取时间": fetched_at.isoformat(),
        "有效开始时间": times[0].isoformat() if times else None,
        "有效结束时间": (times[-1] + timedelta(hours=1)).isoformat() if times else None,
        "数据来源": "Open-Meteo",
    }


# ------------------------------------------------------------------
# 预报查询工具
# ------------------------------------------------------------------
_SPEC_WEATHER_FORECAST = ToolSpec(
    name="weather_forecast",
    description=(
        "查询指定位置未来 1-7 天的逐日天气摘要：平均云量、傍晚云量、最低能见度、"
        "最高降水概率、最大风速，并附每日傍晚（16-20 点）的小时级云量明细，"
        "用于拍摄计划编排（风光 / 日落 / 星空前的空气通透度与云况预判）。"
        "数据来自 Open-Meteo 免费接口，无需 API Key。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "latitude": {
                "type": "number",
                "description": "纬度（度，北纬为正）",
            },
            "longitude": {
                "type": "number",
                "description": "经度（度，东经为正）",
            },
            "days": {
                "type": "integer",
                "description": "预报天数（1-7），默认 3",
                "default": 3,
            },
            "tz_offset": {
                "type": "string",
                "description": "时区偏移，如 +08:00（默认，北京时间）",
                "default": "+08:00",
            },
        },
        "required": ["latitude", "longitude"],
    },
    capabilities=frozenset(["tools"]),
    main_field="每日预报",
    confidence=Confidence.MEDIUM,
    confidence_rule="forecast",
)


def weather_forecast(
    latitude: float, longitude: float, days: int = 3, tz_offset: str = "+08:00"
) -> dict:
    """获取未来若干天的天气摘要。

    Args:
        latitude: 纬度（度）。
        longitude: 经度（度）。
        days: 预报天数（1-7）。
        tz_offset: 时区偏移字符串。

    Returns:
        逐日天气摘要与数据来源说明。
    """
    if not 1 <= days <= 7:
        return {"error": "days 须在 1-7 之间"}
    if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
        return {"error": "经纬度超出范围"}

    url = (
        f"{_base_url()}?latitude={latitude}&longitude={longitude}"
        f"&hourly=cloud_cover,cloud_cover_high,cloud_cover_low,"
        f"visibility,precipitation_probability,wind_speed_10m"
        f"&timezone=GMT&forecast_days={days}"
    )
    try:
        data = _fetch_json(url)
    except WeatherError as exc:
        return {"error": str(exc)}

    fetched_at = _now()
    rows = _hourly_rows(data, tz_offset)
    if not rows:
        return {"error": "预报数据为空（可能超出接口可用范围）"}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["时间"][:10], []).append(row)
    daily: list[dict[str, Any]] = []
    for day_key, day_rows in sorted(grouped.items()):
        clouds = [row["cloud_cover"] for row in day_rows if row["cloud_cover"] is not None]
        visibility = [row["visibility"] for row in day_rows if row["visibility"] is not None]
        precip = [
            row["precipitation_probability"]
            for row in day_rows
            if row["precipitation_probability"] is not None
        ]
        winds = [row["wind_speed_10m"] for row in day_rows if row["wind_speed_10m"] is not None]
        daily.append(
            {
                "日期": day_key,
                "平均云量（%）": round(sum(clouds) / len(clouds)) if clouds else None,
                "云量范围（%）": f"{min(clouds)}-{max(clouds)}" if clouds else None,
                "最低能见度（km）": round(min(visibility) / 1000, 1) if visibility else None,
                "最高降水概率（%）": max(precip) if precip else None,
                "最大风速（km/h）": round(max(winds)) if winds else None,
                "傍晚云量序列": [
                    {
                        "时间": row["时间"][11:16],
                        "云量（%）": row["cloud_cover"],
                        "高云（%）": row["cloud_cover_high"],
                        "能见度（km）": round(row["visibility"] / 1000, 1)
                        if row["visibility"] is not None
                        else None,
                    }
                    for row in day_rows
                    if 16 <= int(row["时间"][11:13]) < 20
                ],
            }
        )
    return {
        **_metadata(rows, latitude, longitude, tz_offset, fetched_at),
        "预报天数": len(daily),
        "每日预报": daily,
        "逐小时预报": rows,
        "说明": "时间按请求时区显示；缺失值保留为空；获取时间不是供应商发布时间。",
    }


# ------------------------------------------------------------------
# 火烧云启发式评分（D3.1-01 启发式模型）
# ------------------------------------------------------------------
_SPEC_SUNSET_GLOW_SCORE = ToolSpec(
    name="sunset_glow_score",
    description=(
        "评估指定日期傍晚的火烧云（晚霞）启发式条件，返回 0-100 分与等级。"
        "综合日落前后三小时的高云占比、云量区间、能见度、降水概率与风速，"
        "并附每项依据与置信度（越临近日落越可信）。"
        "注意：这是面向摄影决策的经验启发式模型，非气象学精确概率；"
        "与晴天/阴天判断不同，完全晴朗或完全阴天都不利于火烧云，"
        "『适中云量 + 高云为主 + 高能见度』才是理想条件。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "latitude": {
                "type": "number",
                "description": "纬度（度，北纬为正）",
            },
            "longitude": {
                "type": "number",
                "description": "经度（度，东经为正）",
            },
            "date": {
                "type": "string",
                "description": "目标日期（本地时区，须为今天起 7 天内），如 2026-08-16",
            },
            "tz_offset": {
                "type": "string",
                "description": "时区偏移，如 +08:00（默认，北京时间）",
                "default": "+08:00",
            },
        },
        "required": ["latitude", "longitude", "date"],
    },
    capabilities=frozenset(["tools"]),
    main_field="评分",
    confidence=Confidence.MEDIUM,
)


def sunset_glow_score(
    latitude: float, longitude: float, date: str, tz_offset: str = "+08:00"
) -> dict:
    """计算指定日期傍晚的火烧云启发式评分。

    Args:
        latitude: 纬度（度）。
        longitude: 经度（度）。
        date: 目标日期（YYYY-MM-DD）。
        tz_offset: 时区偏移字符串。

    Returns:
        含评分、等级、依据与置信度的字典。
    """
    day = _parse_date(date)
    if day is None:
        return {"error": "日期格式须为 YYYY-MM-DD"}
    if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
        return {"error": "经纬度超出范围"}

    now = _now()
    tz = _parse_tz_offset(tz_offset)
    today = now.astimezone(tz).date()
    days_ahead = (day - today).days
    if not 0 <= days_ahead <= 6:
        return {"error": "仅支持今天起 7 天内的火烧云评估"}
    sun_result = sun_times(latitude, longitude, day.isoformat(), tz_offset)
    sunset_time = sun_result.get("日落")
    if "error" in sun_result or not sunset_time:
        return {"error": sun_result.get("error", "缺少日落时间")}
    sunset_at = datetime.fromisoformat(f"{day.isoformat()}T{sunset_time}").replace(tzinfo=tz)
    start, end = sunset_at - timedelta(hours=3), sunset_at + timedelta(hours=2)
    url = (
        f"{_base_url()}?latitude={latitude}&longitude={longitude}"
        "&hourly=cloud_cover,cloud_cover_high,cloud_cover_low,"
        "visibility,precipitation_probability,wind_speed_10m"
        "&timezone=GMT&forecast_days=7"
    )
    try:
        data = _fetch_json(url)
    except WeatherError as exc:
        return {"error": str(exc)}
    rows = _hourly_rows(data, tz_offset)
    fetched_at = _now()
    window = [row for row in rows if start <= datetime.fromisoformat(row["时间"]) <= end]
    if not window:
        return {"error": f"未获取到 {day.isoformat()} 日落窗口的逐小时数据"}
    cloud = _mean([row["cloud_cover"] for row in window])
    high = _mean([row["cloud_cover_high"] for row in window])
    visibility = _mean([row["visibility"] for row in window])
    precip = _max([row["precipitation_probability"] for row in window])
    wind = _max([row["wind_speed_10m"] for row in window])
    ratio = (
        (min(1.0, high / cloud) if cloud > 0 else 0.0)
        if cloud is not None and high is not None
        else None
    )
    components = [
        (0.45, max(0.0, 1.0 - abs(cloud - 50) / 50) if cloud is not None else None),
        (0.25, min(1.0, ratio / 0.4) if ratio is not None else None),
        (
            0.15,
            max(0.0, min(1.0, (visibility / 1000 - 5) / 15)) if visibility is not None else None,
        ),
        (0.10, max(0.0, min(1.0, (70 - precip) / 50)) if precip is not None else None),
        (0.05, max(0.0, min(1.0, (80 - wind) / 55)) if wind is not None else None),
    ]
    # 缺失分量不加分，也不将已有分量重归一化为满分。
    score = round(100 * sum(weight * value for weight, value in components if value is not None))
    fields = {
        "云量": "cloud_cover",
        "高云": "cloud_cover_high",
        "能见度": "visibility",
        "降水概率": "precipitation_probability",
        "风速": "wind_speed_10m",
    }
    missing = [
        label for label, field in fields.items() if any(row[field] is None for row in window)
    ]
    first_hour = start.replace(minute=0, second=0, microsecond=0)
    if first_hour < start:
        first_hour += timedelta(hours=1)
    expected = int((end - first_hour).total_seconds() // 3600) + 1
    if len({row["时间"] for row in window}) < expected:
        missing.append("日落窗口小时覆盖不完整")
    hours_to_sunset = (sunset_at - now).total_seconds() / 3600
    confidence = (
        "低"
        if missing or hours_to_sunset < 0 or hours_to_sunset > 72
        else ("中" if hours_to_sunset > 24 else "高")
    )
    level = (
        "数据不足"
        if missing
        else (
            "高（值得赌一把）"
            if score >= 75
            else "中等（可看趋势再定）"
            if score >= 50
            else "偏低（建议观望）"
            if score >= 25
            else "低（不建议专程前往）"
        )
    )
    values = {
        "云量（%）": cloud,
        "高云（%）": high,
        "能见度（km）": visibility / 1000 if visibility is not None else None,
        "降水概率（%）": precip,
        "风速（km/h）": wind,
    }
    return {
        **_metadata(rows, latitude, longitude, tz_offset, fetched_at),
        "日期": day.isoformat(),
        "日落时间": sunset_time,
        "目标时刻": sunset_at.isoformat(),
        "评分（0-100）": score if any(value is not None for _, value in components) else None,
        "等级": level,
        "置信度": confidence,
        "缺失字段": missing,
        "窗口样本数": len(window),
        "依据": [
            f"{label}：{round(value, 1) if value is not None else '缺失'}"
            for label, value in values.items()
        ],
        "说明": "摄影启发式评分，未经概率校准；缺失分量不加分，数据不足须补齐后重查。",
    }


def _mean(values: list[float | None]) -> float | None:
    """忽略空值计算均值，无有效值时保留 None。"""
    valid = [value for value in values if value is not None]
    return sum(valid) / len(valid) if valid else None


def _max(values: list[float | None]) -> float | None:
    """忽略空值求最大值，无有效值时保留 None。"""
    valid = [value for value in values if value is not None]
    return max(valid) if valid else None


# ------ 声明式收集点（ToolSpec 真源；新增工具 = 加一行）------
TOOLS: tuple[Tool, ...] = (
    PureTool(spec=_SPEC_WEATHER_FORECAST, func=weather_forecast),
    PureTool(spec=_SPEC_SUNSET_GLOW_SCORE, func=sunset_glow_score),
)
