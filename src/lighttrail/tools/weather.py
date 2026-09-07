"""天气查询工具：接入 Open-Meteo 免费预报接口（无需 API Key）。

设计要点：
- 数据源可替换：Base URL 可通过环境变量 OPEN_METEO_BASE_URL 覆盖（测试/自建镜像）；
- Open-Meteo 返回当地时间（timezone=auto），本模块按请求时区偏移解释显示；
- 火烧云概率为启发式评分模型（高云占比 / 云量区间 / 能见度 / 降水 / 风速），
  输出依据字段保证可解释性（决策地基 M2）。

对外工具：
- weather_forecast：未来 1-7 天每日云量 / 能见度 / 降水概率 / 风速摘要；
- sunset_glow_score：指定日期傍晚火烧云概率评分（结合天文日落时刻）。
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from typing import Any

from lighttrail.agent.tools import registry
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
    """GET 请求并解析 JSON；5xx/网络错误按退避重试，4xx 立即失败并带服务端原因。

    Raises:
        WeatherError: 请求失败（含 4xx 参数错误与重试耗尽的 5xx/网络错误）。
    """
    request = urllib.request.Request(url, headers={"User-Agent": "lighttrail/0.1"})
    last_error: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # 4xx 为请求/参数错误，重试无意义：立即失败并携带服务端原因（如变量名失效）
            status = exc.code
            reason = ""
            try:
                reason = exc.read().decode("utf-8", "replace")[:200]
            except Exception:  # noqa: BLE001 - 错误体解析失败不影响主错误
                reason = ""
            if 400 <= status < 500:
                detail = f"HTTP {status}" + (f"：{reason}" if reason else "")
                raise WeatherError(f"天气接口请求无效：{detail}") from exc
            last_error = exc
            logger.warning("天气请求失败（第 %d 次）：HTTP %s", attempt + 1, status)
        except Exception as exc:  # noqa: BLE001 - 网络层各类异常统一处理
            last_error = exc
            logger.warning("天气请求失败（第 %d 次）：%s", attempt + 1, exc)
    raise WeatherError(f"天气数据获取失败：{last_error}")


def _hourly_to_iso(value: str, tz_offset: str) -> str:
    """把 Open-Meteo 的本地时间戳（无时区）按请求时区偏移重新标注。"""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    tz = _parse_tz_offset(tz_offset)
    return parsed.replace(tzinfo=tz).isoformat(timespec="minutes")


# ------------------------------------------------------------------
# 预报查询工具
# ------------------------------------------------------------------
@registry.tool(
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
        f"&timezone=auto&forecast_days={days}"
    )
    try:
        data = _fetch_json(url)
    except WeatherError as exc:
        return {"error": str(exc)}

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    fields = [
        "cloud_cover",
        "cloud_cover_high",
        "cloud_cover_low",
        "visibility",
        "precipitation_probability",
        "wind_speed_10m",
    ]
    if not times:
        return {"error": "预报数据为空（可能超出接口可用范围）"}

    grouped: dict[str, list[dict[str, Any]]] = {}
    for index, raw_time in enumerate(times):
        day_key = raw_time[:10]
        entry = {"时间": _hourly_to_iso(raw_time, tz_offset)}
        for field in fields:
            values = hourly.get(field, [])
            entry[field] = values[index] if index < len(values) else None
        grouped.setdefault(day_key, []).append(entry)

    daily: list[dict[str, Any]] = []
    for day_key in sorted(grouped):
        rows = grouped[day_key]
        clouds = [row["cloud_cover"] or 0 for row in rows]
        evening = [row for row in rows if 16 <= int(row["时间"][11:13]) < 20]
        visibilities = [row["visibility"] for row in rows if row["visibility"] is not None]
        precip = [row["precipitation_probability"] or 0 for row in rows]
        winds = [row["wind_speed_10m"] or 0 for row in rows]
        summary = {
            "日期": day_key,
            "平均云量（%）": round(sum(clouds) / len(clouds)) if clouds else None,
            "云量范围（%）": f"{min(clouds)}-{max(clouds)}" if clouds else None,
            "最低能见度（km）": round(min(visibilities) / 1000, 1) if visibilities else None,
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
                for row in evening
            ],
        }
        daily.append(summary)

    return {
        "位置": f"{latitude},{longitude}",
        "时区": tz_offset,
        "预报天数": len(daily),
        "每日预报": daily,
        "数据来源": "Open-Meteo（免费）",
        "说明": "傍晚云量序列为 16:00-19:00 当地小时数据；预测越接近当下越可靠，供规划参考",
    }


# ------------------------------------------------------------------
# 火烧云概率评分（D3.1-01 启发式模型）
# ------------------------------------------------------------------
@registry.tool(
    name="sunset_glow_score",
    description=(
        "评估指定日期傍晚的火烧云（晚霞）爆发概率，返回 0-100 分与等级。"
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
)
def sunset_glow_score(
    latitude: float, longitude: float, date: str, tz_offset: str = "+08:00"
) -> dict:
    """计算指定日期傍晚的火烧云概率评分。

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

    today = datetime.now(_parse_tz_offset(tz_offset)).date()
    if day < today:
        return {"error": "仅支持今天起 7 天内的火烧云评估（历史日期无预报数据）"}
    days_ahead = (day - today).days
    if days_ahead > 6:
        return {"error": "仅支持今天起 7 天内的火烧云评估"}

    # 日落时刻（复用天文模块）
    sun_result = sun_times(latitude, longitude, day.isoformat(), tz_offset)
    if "error" in sun_result:
        return {"error": sun_result["error"]}
    sunset_time = sun_result["日落"]
    sunset_minutes = _hhmm_to_minutes(sunset_time)

    # 拉取预报（含目标日，多取 1 天以防时区边界）
    url = (
        f"{_base_url()}?latitude={latitude}&longitude={longitude}"
        f"&hourly=cloud_cover,cloud_cover_high,cloud_cover_low,"
        f"visibility,precipitation_probability,wind_speed_10m"
        f"&timezone=auto&forecast_days={days_ahead + 2 if days_ahead < 6 else 7}"
    )
    try:
        data = _fetch_json(url)
    except WeatherError as exc:
        return {"error": str(exc)}

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    window_start = (
        (day - timedelta(days=1)).isoformat() if sunset_minutes < 720 else day.isoformat()
    )
    window = []
    for index, raw_time in enumerate(times):
        if not raw_time.startswith(window_start):
            continue
        minute_of_day = int(raw_time[11:13]) * 60 + int(raw_time[14:16])
        if sunset_minutes - 180 <= minute_of_day <= sunset_minutes + 120:
            window.append(
                {
                    "cloud": hourly.get("cloud_cover", [])[index]
                    if index < len(hourly.get("cloud_cover", []))
                    else None,
                    "high": hourly.get("cloud_cover_high", [])[index]
                    if index < len(hourly.get("cloud_cover_high", []))
                    else None,
                    "visibility": hourly.get("visibility", [])[index]
                    if index < len(hourly.get("visibility", []))
                    else None,
                    "precip": hourly.get("precipitation_probability", [])[index]
                    if index < len(hourly.get("precipitation_probability", []))
                    else None,
                    "wind": hourly.get("wind_speed_10m", [])[index]
                    if index < len(hourly.get("wind_speed_10m", []))
                    else None,
                }
            )
    if not window:
        return {"error": f"未获取到 {day.isoformat()} 日落前后三小时的逐小时数据"}

    # ---- 启发式评分（各分量 0~1，加权求和后映射到 0-100）----
    avg_cloud = _mean([row["cloud"] for row in window])
    avg_high = _mean([row["high"] for row in window])
    avg_visibility_km = (
        _mean([row["visibility"] for row in window]) / 1000.0
        if any(row["visibility"] is not None for row in window)
        else 50.0
    )
    max_precip = _max([row["precip"] for row in window])
    max_wind = _max([row["wind"] for row in window])

    # 云量形态：总量 30-70% 最佳，过高过低都扣分
    cloud_score = max(0.0, 1.0 - abs(avg_cloud - 50.0) / 50.0)
    # 高云占比：高云（卷云）被夕阳染红最明显，40% 以上理想
    high_ratio = 0.0 if not avg_cloud else min(1.0, avg_high / avg_cloud)
    high_score = min(1.0, high_ratio / 0.4) if avg_cloud > 0 else 0.0
    # 能见度：>=20km 满分，<=5km 归零
    visibility_score = max(0.0, min(1.0, (avg_visibility_km - 5.0) / 15.0))
    # 降水：<=20% 满分，>=70% 归零
    precip_score = max(0.0, min(1.0, (70.0 - max_precip) / 50.0))
    # 风速：<=25km/h 满分，>=80km/h 归零（大风会吹散云层形态）
    wind_score = max(0.0, min(1.0, (80.0 - max_wind) / 55.0))

    score = round(
        100.0
        * (
            0.45 * cloud_score
            + 0.25 * high_score
            + 0.15 * visibility_score
            + 0.10 * precip_score
            + 0.05 * wind_score
        )
    )
    if score >= 75:
        level = "高（值得赌一把）"
    elif score >= 50:
        level = "中等（可看趋势再定）"
    elif score >= 25:
        level = "偏低（建议观望）"
    else:
        level = "低（不建议专程前往）"

    now_local = datetime.now(_parse_tz_offset(tz_offset))
    now_minutes = now_local.hour * 60 + now_local.minute
    hours_to_sunset = (sunset_minutes - now_minutes) / 60.0
    if hours_to_sunset < 0:
        hours_to_sunset += 24.0
    if hours_to_sunset <= 24:
        confidence = "高"
    elif hours_to_sunset <= 72:
        confidence = "中"
    else:
        confidence = "低"

    return {
        "日期": day.isoformat(),
        "日落时间": sunset_time,
        "评分（0-100）": score,
        "等级": level,
        "依据": [
            f"日落前后平均云量 {round(avg_cloud)}%（30-70% 为理想区间）",
            f"高云占比约 {round(high_ratio * 100)}%（高云利于染红）",
            f"平均能见度 {round(avg_visibility_km, 1)} km",
            f"最大降水概率 {round(max_precip)}%",
            f"最大风速 {round(max_wind)} km/h",
        ],
        "置信度": confidence,
        "说明": "经验启发式模型（非气象学精确概率），建议结合实时天空观察；反烧（日落后 20-40 分钟）可小幅加分参考同模型偏移",
    }


def _hhmm_to_minutes(value: str) -> int:
    """把 HH:MM 转为当日分钟数。"""
    hours, minutes = (int(part) for part in value.split(":"))
    return hours * 60 + minutes


def _mean(values: list[float | None]) -> float:
    """数值列表均值（忽略 None）。"""
    valid = [value for value in values if value is not None]
    return sum(valid) / len(valid) if valid else 0.0


def _max(values: list[float | None]) -> float:
    """数值列表最大值（忽略 None，空表返回 0）。"""
    valid = [value for value in values if value is not None]
    return max(valid) if valid else 0.0
