"""天文计算工具：太阳 / 月亮 / 银心的时刻与方位。

依赖 astral 库提供太阳与月亮历表；银心方位使用 J2000 赤道坐标 + 一阶岁差
近似 + 时角坐标转换的自研算法（精度约 ±2 度，仅作规划参考，详见函数说明）。

对外暴露的模型工具：
- sun_times：日出日落 / 晨昏蒙影 / 蓝调黄金窗口；
- sun_position：指定时刻太阳高度角与方位角；
- moon_phase：月相、月龄与照亮比例；
- moon_events：月升月落时刻与方位；
- galaxy_visibility：银心（银河核心）可见窗口。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date as date_cls
from datetime import datetime, time, timedelta, timezone
from functools import partial
from math import asin, atan2, cos, degrees, pi, radians, sin, tan
from typing import Any, TypeVar

from astral import Observer, moon, sun
from astral.sun import Depression

from lighttrail.contracts.tool import Confidence, Tool, ToolSpec
from lighttrail.tools._base import PureTool

# 银心 J2000.0 赤道坐标（国际天文联合会定义，单位：度）
_GALACTIC_CENTER_RA_J2000 = 266.4051
_GALACTIC_CENTER_DEC_J2000 = -28.9362

# 太阳高度角区间采样步长（分钟），用于求蓝调 / 黄金时刻窗口
_SAMPLE_MINUTES = 5
_EVENT_VALUE = TypeVar("_EVENT_VALUE")
# 银心可见性扫描步长（分钟）
_GALAXY_SCAN_MINUTES = 10

# 方位角（自北顺时针）→ 16 方位中文描述
_DIRECTION_NAMES = [
    "北",
    "北北东",
    "东北",
    "东东北",
    "东",
    "东东南",
    "东南",
    "南东南",
    "南",
    "南西南",
    "西南",
    "西西南",
    "西",
    "西西北",
    "西北",
    "北西北",
]


def _parse_tz_offset(value: str) -> timezone:
    """解析时区偏移（如 +08:00 / UTC）为 timezone 对象，非法输入回退 UTC。"""
    text = value.strip().upper()
    if text in ("UTC", "Z"):
        return timezone.utc
    try:
        sign = 1 if text.startswith("+") else -1
        hours, minutes = (int(part) for part in text.lstrip("+-").split(":"))
        return timezone(sign * timedelta(hours=hours, minutes=minutes))
    except (ValueError, AttributeError):
        return timezone.utc


def _parse_date(value: str) -> date_cls | None:
    """解析 YYYY-MM-DD 日期，非法输入返回 None。"""
    try:
        return date_cls.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        return None


def _validate_latlon(latitude: float, longitude: float) -> str | None:
    """校验经纬度范围，合法返回 None，否则返回错误描述。"""
    if not (-90.0 <= latitude <= 90.0):
        return "纬度范围须在 [-90, 90]"
    if not (-180.0 <= longitude <= 180.0):
        return "经度范围须在 [-180, 180]"
    return None


def _make_observer(latitude: float, longitude: float) -> Observer:
    """构造 astral 观测点。"""
    return Observer(latitude=latitude, longitude=longitude, elevation=0.0)


def _fmt_hm(value: datetime) -> str:
    """格式化时刻为 HH:MM。"""
    return value.strftime("%H:%M")


def _fmt_iso(value: datetime) -> str:
    """格式化时刻为 ISO 8601（分钟精度）。"""
    return value.isoformat(timespec="minutes")


def _window(start: datetime, end: datetime) -> dict:
    """构造时间窗口字典（含本地时间与 ISO 两种表示）。"""
    return {
        "开始": _fmt_hm(start),
        "结束": _fmt_hm(end),
        "开始_iso": _fmt_iso(start),
        "结束_iso": _fmt_iso(end),
    }


def _direction_name(azimuth_deg: float) -> str:
    """把方位角（自北顺时针）映射为 16 方位中文描述。"""
    index = int((azimuth_deg + 11.25) % 360.0 // 22.5)
    return _DIRECTION_NAMES[index]


def _elevation_windows(
    observer: Observer, day: date_cls, tz: timezone, low: float, high: float
) -> list[dict]:
    """扫描一天内太阳高度角处于 [low, high) 区间的连续时段。

    用于蓝调（-6~-4 度）与黄金（-4~+6 度）时刻窗口，按《拍摄决策》常用
    阈值定义；采样步长 5 分钟，边界误差不超过步长。

    Args:
        observer: astral 观测点。
        day: 本地日期。
        tz: 本地时区。
        low: 高度角下限（度）。
        high: 高度角上限（度）。

    Returns:
        连续窗口列表（按时间正序）。
    """
    step = timedelta(minutes=_SAMPLE_MINUTES)
    cursor = datetime.combine(day, time.min, tzinfo=tz)
    day_end = cursor + timedelta(days=1)
    windows: list[dict] = []
    start: datetime | None = None
    while cursor < day_end:
        elevation = sun.elevation(observer, cursor)
        inside = low <= elevation < high
        if inside and start is None:
            start = cursor
        elif not inside and start is not None:
            windows.append(_window(start, cursor))
            start = None
        cursor += step
    if start is not None:
        windows.append(_window(start, day_end))
    return windows


# ------------------------------------------------------------------
# 太阳工具
# ------------------------------------------------------------------
_SPEC_SUN_TIMES = ToolSpec(
    name="sun_times",
    description=(
        "查询指定地点与日期的太阳关键时刻：日出日落、民用/航海/天文晨昏蒙影、"
        "蓝调时刻（太阳高度 -6~-4 度）与黄金时刻（-4~+6 度）窗口。"
        "用于判断『几点去拍日出/日落/蓝调/黄金』与机位规划。经纬度可用"
        "地名近似（如上海市区约 31.23, 121.47），有精确机位坐标时请用精确值。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "latitude": {
                "type": "number",
                "description": "纬度（度，北纬为正），如上海 31.23",
            },
            "longitude": {
                "type": "number",
                "description": "经度（度，东经为正），如上海 121.47",
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
        "required": ["latitude", "longitude", "date"],
    },
    capabilities=frozenset(["tools"]),
    main_field="太阳时刻",
    confidence=Confidence.HIGH,
)


def sun_times(latitude: float, longitude: float, date: str, tz_offset: str = "+08:00") -> dict:
    """计算指定地点与日期的太阳关键时刻。

    Args:
        latitude: 纬度（度）。
        longitude: 经度（度）。
        date: 本地日期字符串（YYYY-MM-DD）。
        tz_offset: 时区偏移字符串。

    Returns:
        含日出日落、晨昏蒙影与蓝调/黄金窗口的字典。
    """
    tz = _parse_tz_offset(tz_offset)
    day = _parse_date(date)
    if day is None:
        return {"error": "日期格式须为 YYYY-MM-DD"}
    lat_error = _validate_latlon(latitude, longitude)
    if lat_error:
        return {"error": lat_error}

    observer = _make_observer(latitude, longitude)
    statuses: dict[str, dict[str, str]] = {}
    events: dict[str, datetime | None] = {}
    for label, compute in (
        ("日出", lambda: sun.sunrise(observer, day, tzinfo=tz)),
        ("日落", lambda: sun.sunset(observer, day, tzinfo=tz)),
        ("正午", lambda: sun.noon(observer, day, tzinfo=tz)),
    ):
        events[label] = _solar_calculation(label, compute, statuses)
    for depression, dawn_label, dusk_label in (
        (Depression.CIVIL, "民用晨光始", "民用昏影终"),
        (Depression.NAUTICAL, "航海晨光始", "航海昏影终"),
        (Depression.ASTRONOMICAL, "天文晨光始", "天文昏影终"),
    ):
        events[dawn_label] = _solar_calculation(
            dawn_label, partial(sun.dawn, observer, day, depression=depression, tzinfo=tz), statuses
        )
        events[dusk_label] = _solar_calculation(
            dusk_label, partial(sun.dusk, observer, day, depression=depression, tzinfo=tz), statuses
        )
    result: dict[str, Any] = {"日期": day.isoformat(), "时区": tz_offset, "事件状态": statuses}
    for label, moment in events.items():
        result[label] = _fmt_hm(moment) if moment is not None else None
    for label in ("日出", "日落"):
        moment = events[label]
        key = f"{label}方位（度）"
        result[key] = (
            _solar_calculation(key, partial(_solar_azimuth, observer, moment), statuses)
            if moment is not None
            else None
        )
        result[f"{label}_iso"] = _fmt_iso(moment) if moment is not None else None
    sunrise, sunset = events["日出"], events["日落"]
    result["昼长（时分）"] = None
    if sunrise is not None and sunset is not None:
        seconds = (sunset - sunrise).total_seconds()
        result["昼长（时分）"] = f"{int(seconds // 3600)}小时{int(seconds % 3600 // 60)}分"
    for label, low, high in (("黄金时刻", -4.0, 6.0), ("蓝调时刻", -6.0, -4.0)):
        windows = _solar_calculation(
            label, partial(_elevation_windows, observer, day, tz, low, high), statuses
        )
        if windows == []:
            statuses[label] = {"状态": "事件不存在", "原因": "当日太阳未进入指定高度区间"}
        result[f"{label}（晨）"] = windows[:1] or None if windows is not None else None
        result[f"{label}（暮）"] = windows[1:2] or None if windows is not None else None
    result["提示"] = (
        "每个事件独立计算；null 须结合事件状态区分不存在与计算失败。"
        "蓝调/黄金窗口按太阳高度角阈值采样 5 分钟估算，非精确边界；"
        "晨/暮沿用当日第一个/第二个连续窗口，高纬单窗口不保证对应日出/日落。"
        "时区为固定偏移，不自动处理夏令时。"
    )
    return result


def _solar_azimuth(observer: Observer, moment: datetime) -> float:
    """计算指定事件的太阳方位，保留一位小数用于显示。"""
    return round(sun.azimuth(observer, moment), 1)


def _solar_calculation(
    label: str, compute: Callable[[], _EVENT_VALUE], statuses: dict[str, dict[str, str]]
) -> _EVENT_VALUE | None:
    """隔离单个计算，只有 Astral 明确的物理缺失消息才判为不存在。

    Args:
        label: 事件或派生计算名称。
        compute: 单个计算回调。
        statuses: 输出状态及失败原因。

    Returns:
        可用计算值；不存在或失败时为 None，不影响其他计算。
    """
    try:
        value = compute()
    except Exception as exc:  # noqa: BLE001  逐事件边界保留其他可用计算
        message = str(exc)
        absent = isinstance(exc, ValueError) and (
            message
            in {
                "Sun is always above the horizon on this day, at this location.",
                "Sun is always below the horizon on this day, at this location.",
            }
            or (
                message.startswith("Sun never reaches ")
                and message.endswith(" degrees below the horizon, at this location.")
            )
        )
        statuses[label] = {"状态": "事件不存在" if absent else "计算失败", "原因": message}
        return None
    statuses[label] = {"状态": "可用"}
    return value


_SPEC_SUN_POSITION = ToolSpec(
    name="sun_position",
    description=(
        "查询指定时刻太阳的高度角与方位角，用于判断光线角度：顺光/侧光/逆光、"
        "是否处于蓝调/黄金窗口、是否已落入地平线以下。配合机位朝向做构图判断。"
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
            "date_time": {
                "type": "string",
                "description": "本地日期时间，如 2026-08-16 18:30 或 2026-08-16T18:30:00",
            },
            "tz_offset": {
                "type": "string",
                "description": "时区偏移，如 +08:00（默认，北京时间）",
                "default": "+08:00",
            },
        },
        "required": ["latitude", "longitude", "date_time"],
    },
    capabilities=frozenset(["tools"]),
    main_field="太阳方位",
    confidence=Confidence.HIGH,
)


def sun_position(
    latitude: float, longitude: float, date_time: str, tz_offset: str = "+08:00"
) -> dict:
    """计算指定时刻的太阳高度角与方位角。

    Args:
        latitude: 纬度（度）。
        longitude: 经度（度）。
        date_time: 本地日期时间字符串。
        tz_offset: 时区偏移字符串。

    Returns:
        含高度角、方位角与光线场景判断的字典。
    """
    tz = _parse_tz_offset(tz_offset)
    lat_error = _validate_latlon(latitude, longitude)
    if lat_error:
        return {"error": lat_error}
    try:
        moment = datetime.fromisoformat(date_time.strip().replace(" ", "T"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=tz)
    except ValueError:
        return {"error": "日期时间格式无法解析，示例：2026-08-16 18:30"}

    observer = _make_observer(latitude, longitude)
    elevation = sun.elevation(observer, moment)
    azimuth = sun.azimuth(observer, moment)

    if elevation < 0:
        scene = "太阳已在地平线以下；若处于 -4~-6 度为蓝调窗口，-6 度以下接近黑夜"
    elif elevation < 6:
        scene = "低角度暖光（黄金窗口），适合逆光剪影、侧光塑形"
    elif elevation < 30:
        scene = "中低角度光线，阴影较长，适合风光层次与质感表现"
    else:
        scene = "高角度顶光，对比强烈，风光/人像需注意阴影与高光"

    return {
        "时间": _fmt_iso(moment),
        "太阳高度角（度）": round(elevation, 1),
        "太阳方位角（度）": round(azimuth, 1),
        "方位": _direction_name(azimuth),
        "场景判断": scene,
    }


# ------------------------------------------------------------------
# 月亮工具
# ------------------------------------------------------------------
_SPEC_MOON_PHASE = ToolSpec(
    name="moon_phase",
    description=(
        "查询指定本地日期的月相：Astral 相位索引、估算月龄与估算照亮比例，并给出月光对星空摄影的"
        "干扰程度建议。新月前后月光干扰最小，是银河/星野的最佳窗口；满月前后适合"
        "月景与月光补光题材。"
    ),
    parameters={
        "type": "object",
        "properties": {
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
        "required": ["date"],
    },
    capabilities=frozenset(["tools"]),
    main_field="月相",
    confidence=Confidence.HIGH,
)


def moon_phase(date: str, tz_offset: str = "+08:00") -> dict:
    """计算指定日期的月相与月光影响。

    Args:
        date: 本地日期字符串（YYYY-MM-DD）。
        tz_offset: 时区偏移字符串。

    Returns:
        含月相名称、月龄、照亮比例与月光影响的字典。
    """
    tz = _parse_tz_offset(tz_offset)
    day = _parse_date(date)
    if day is None:
        return {"error": "日期格式须为 YYYY-MM-DD"}

    # Astral 按 UTC 日期返回 0..28 相位索引，而非真实月龄。
    # 本地午间仅用于选择日级 UTC 样本，不代表精确瞬时月相。
    reference = datetime.combine(day, time(12), tzinfo=tz)
    utc_day = reference.astimezone(timezone.utc).date()
    phase_index = moon.phase(utc_day)
    illumination = (1.0 - cos(2.0 * pi * phase_index / 28.0)) / 2.0
    illumination_percent = round(illumination * 100)

    if phase_index < 1.75 or phase_index >= 26.25:
        name = "新月"
    elif phase_index < 5.25:
        name = "娥眉月（朔后）"
    elif phase_index < 8.75:
        name = "上弦月"
    elif phase_index < 12.25:
        name = "盈凸月"
    elif phase_index < 15.75:
        name = "满月"
    elif phase_index < 19.25:
        name = "亏凸月"
    elif phase_index < 22.75:
        name = "下弦月"
    else:
        name = "残月（下弦后）"

    if illumination_percent < 10:
        advice = "估算月面较暗；月光是否干扰仍需结合月升月落和目标时段判断"
    elif illumination_percent < 40:
        advice = "估算月面逐渐变亮；结合月亮高度安排银河窗口，避开月亮方向"
    elif illumination_percent < 70:
        advice = "估算月面较亮，月亮升起时可能降低星空对比度；可考虑月景或补光"
    else:
        advice = "估算月面接近满月亮度，月亮在地平线上时可能明显干扰银河；可考虑月景"

    return {
        "日期": day.isoformat(),
        "时区": tz_offset,
        "参考时刻": reference.isoformat(timespec="minutes"),
        "计算日期（UTC）": utc_day.isoformat(),
        "月相名称": name,
        "相位索引（0-28）": round(phase_index, 2),
        "月龄（天）": None,
        "估算月龄（天）": round(phase_index / 28.0 * 29.5306, 1),
        "照亮比例（%）": illumination_percent,
        "照亮比例方法": "估算：(1-cos(2π×相位索引/28))/2；并非精确地月日几何计算",
        "来源": "Astral moon.phase，https://astral.readthedocs.io/en/latest/package.html#astral.moon.phase",
        "月光影响建议": advice,
        "提示": (
            "本地午间映射到 UTC 日期的日级月相样本，不表示该时刻的精确月相。"
            "真实月龄未计算；估算月龄以相位索引线性映射平均周期 29.5306 天，非距上次新月的实测天数；"
            "照亮比例与月相名称均为近似估算，不保证日内精度。月光干扰还受月亮高度、天气影响，"
            "需配合 moon_events 判断月升月落。"
        ),
    }


_SPEC_MOON_EVENTS = ToolSpec(
    name="moon_events",
    description=(
        "查询指定日期、地点的月升月落时刻与方位，并判断月亮整夜/整日在地平线上的情况。"
        "用于评估月光对特定时段（如银河窗口、蓝调时段）是否重叠，配合 moon_phase 使用。"
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
                "description": "日期（本地时区），如 2026-08-16",
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
    main_field="月升月落",
    confidence=Confidence.HIGH,
)


def moon_events(latitude: float, longitude: float, date: str, tz_offset: str = "+08:00") -> dict:
    """计算指定日期与地点的月升月落。

    Args:
        latitude: 纬度（度）。
        longitude: 经度（度）。
        date: 本地日期字符串（YYYY-MM-DD）。
        tz_offset: 时区偏移字符串。

    Returns:
        含月升月落时刻与方位的字典（无月升/月落时给出说明）。
    """
    tz = _parse_tz_offset(tz_offset)
    day = _parse_date(date)
    if day is None:
        return {"error": "日期格式须为 YYYY-MM-DD"}
    lat_error = _validate_latlon(latitude, longitude)
    if lat_error:
        return {"error": lat_error}

    observer = _make_observer(latitude, longitude)
    rise = moon.moonrise(observer, day, tzinfo=tz)
    set_time = moon.moonset(observer, day, tzinfo=tz)

    result: dict = {"日期": day.isoformat(), "时区": tz_offset}
    if rise is not None:
        result["月升"] = _fmt_hm(rise)
        result["月升方位（度）"] = round(moon.azimuth(observer, rise), 1)
    else:
        result["月升"] = None
        result["说明"] = result.get("说明", "") + "当日无月升（月亮整日在地平线以上或以下）；"
    if set_time is not None:
        result["月落"] = _fmt_hm(set_time)
        result["月落方位（度）"] = round(moon.azimuth(observer, set_time), 1)
    else:
        result["月落"] = None
        result["说明"] = result.get("说明", "") + "当日无月落（月亮整日在地平线以上或以下）。"
    return result


# ------------------------------------------------------------------
# 银心（银河核心）方位工具
# ------------------------------------------------------------------
def _galactic_center_ra_dec(utc: datetime) -> tuple[float, float]:
    """计算银心在指定时刻的赤经赤纬（度）。

    采用 J2000.0 赤道坐标 + 一阶岁差近似（Meeus 低精度公式），忽略章动与
    自行；对 2020-2030 年计算精度约 ±0.1 度，配合仰角换算后整体误差
    约 ±2 度，可满足拍摄规划需求。

    Returns:
        (赤经, 赤纬)，单位度。
    """
    if utc.tzinfo is not None:
        utc = utc.astimezone(timezone.utc).replace(tzinfo=None)
    years = (utc - datetime(2000, 1, 1, 12)).total_seconds() / (365.25 * 86400.0)  # noqa: DTZ001 - J2000 历元常量
    ra0 = radians(_GALACTIC_CENTER_RA_J2000)
    dec0 = radians(_GALACTIC_CENTER_DEC_J2000)
    m = 3.0749 + 1.3362 * sin(ra0) * tan(dec0)  # 秒(时)/年
    n = 20.0426 * cos(ra0)  # 角秒/年
    delta_ra_sec = years * (m + n * sin(ra0) * tan(dec0))  # 秒(时)
    delta_dec_arcsec = years * n * cos(ra0)  # 角秒
    ra = degrees(ra0) + delta_ra_sec / 240.0  # 秒(时) -> 度
    dec = degrees(dec0) + delta_dec_arcsec / 3600.0  # 角秒 -> 度
    return ra % 360.0, dec


def _galactic_alt_az(utc: datetime, latitude: float, longitude: float) -> tuple[float, float]:
    """计算银心在指定时刻的高度角与方位角（度）。

    Args:
        utc: 时刻（与 UTC 等价的 naive datetime 亦可）。
        latitude: 观测纬度（度）。
        longitude: 观测经度（度，东经为正）。

    Returns:
        (高度角, 方位角自北顺时针)。
    """
    if utc.tzinfo is not None:
        utc = utc.astimezone(timezone.utc).replace(tzinfo=None)
    jd = utc.replace(tzinfo=timezone.utc).timestamp() / 86400.0 + 2440587.5
    gmst = (
        280.46061837
        + 360.98564736629 * (jd - 2451545.0)
        + 0.000387933 * ((jd - 2451545.0) / 36525.0) ** 2
    ) % 360.0
    lst = (gmst + longitude) % 360.0

    ra, dec = _galactic_center_ra_dec(utc)
    hour_angle = radians(lst - ra)
    lat = radians(latitude)
    dec_rad = radians(dec)
    altitude = asin(sin(lat) * sin(dec_rad) + cos(lat) * cos(dec_rad) * cos(hour_angle))
    azimuth = atan2(
        sin(hour_angle),
        cos(hour_angle) * sin(lat) - tan(dec_rad) * cos(lat),
    )
    return degrees(altitude), (degrees(azimuth) + 180.0) % 360.0


_SPEC_GALAXY_VISIBILITY = ToolSpec(
    name="galaxy_visibility",
    description=(
        "查询指定日期夜晚银心（银河核心方向）的可见窗口：升出地平面的时间段、"
        "整夜最高高度角与时刻、以及对应方位。银心高度越高、月亮干扰越小，越适合"
        "拍摄银河拱桥/银心。天文昏影终到次日天文晨光始之间为完全黑暗时段。"
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
                "description": "日期（本地时区，该日夜晚），如 2026-08-16",
            },
            "tz_offset": {
                "type": "string",
                "description": "时区偏移，如 +08:00（默认，北京时间）",
                "default": "+08:00",
            },
            "min_altitude": {
                "type": "number",
                "description": "最低可见高度角（度），默认 5：低于此高度受地平线薄雾/光害影响大",
                "default": 5.0,
            },
        },
        "required": ["latitude", "longitude", "date"],
    },
    capabilities=frozenset(["tools"]),
    main_field="银心可见窗口",
    confidence=Confidence.HIGH,
)


def galaxy_visibility(
    latitude: float,
    longitude: float,
    date: str,
    tz_offset: str = "+08:00",
    min_altitude: float = 5.0,
) -> dict:
    """计算银心可观测窗口。

    Args:
        latitude: 纬度（度）。
        longitude: 经度（度）。
        date: 本地日期（该日夜晚）。
        tz_offset: 时区偏移字符串。
        min_altitude: 最低可见高度角（度）。

    Returns:
        含可见窗口、全天最高高度与提示的字典。
    """
    tz = _parse_tz_offset(tz_offset)
    day = _parse_date(date)
    if day is None:
        return {"error": "日期格式须为 YYYY-MM-DD"}
    lat_error = _validate_latlon(latitude, longitude)
    if lat_error:
        return {"error": lat_error}
    if not min_altitude:
        return {"error": "min_altitude 须为正数"}

    observer = _make_observer(latitude, longitude)
    dusk_astro = sun.dusk(observer, day, depression=Depression.ASTRONOMICAL, tzinfo=tz)
    dawn_astro = sun.dawn(
        observer, day + timedelta(days=1), depression=Depression.ASTRONOMICAL, tzinfo=tz
    )

    step = timedelta(minutes=_GALAXY_SCAN_MINUTES)
    cursor = dusk_astro
    windows: list[dict] = []
    current: dict | None = None
    best: dict | None = None
    while cursor <= dawn_astro:
        altitude, azimuth = _galactic_alt_az(cursor, latitude, longitude)
        if current is None and altitude >= min_altitude:
            current = {
                "开始": cursor,
                "最高高度角": altitude,
                "最高时刻": cursor,
                "最高时方位": azimuth,
            }
        elif current is not None and altitude < min_altitude:
            current["结束"] = cursor
            windows.append(_close_galaxy_window(current))
            current = None
        elif current is not None and altitude >= min_altitude and altitude > current["最高高度角"]:
            current["最高高度角"] = altitude
            current["最高时刻"] = cursor
            current["最高时方位"] = azimuth
        if best is None or altitude > best["高度角"]:
            best = {"高度角": altitude, "时刻": cursor, "方位角": azimuth}
        cursor += step
    if current is not None:
        current["结束"] = dawn_astro
        windows.append(_close_galaxy_window(current))

    ra_now, dec_now = _galactic_center_ra_dec(
        dusk_astro.astimezone(timezone.utc).replace(tzinfo=None)
    )
    result: dict = {
        "日期": day.isoformat(),
        "时区": tz_offset,
        "夜晚黑暗时段": f"{_fmt_hm(dusk_astro)} - {_fmt_hm(dawn_astro)}（次日晨）",
        "银心赤经/赤纬（约）": f"{round(ra_now, 1)}° / {round(dec_now, 1)}°",
    }
    if best is not None:
        result["整夜最高高度角（度）"] = round(best["高度角"], 1)
        result["整夜最高时刻"] = _fmt_hm(best["时刻"])
        result["整夜最高方位（度）"] = round(best["方位角"], 1)
    result["可见窗口"] = windows or []
    result["提示"] = (
        "银心方位为近似计算（±2 度），规划用；月光影响请配合 moon_phase / moon_events 判断；"
        "银心季为北半球 3-10 月，春秋为南天银心（核心）露出最佳季节"
    )
    return result


def _close_galaxy_window(window: dict) -> dict:
    """收尾银心可见窗口：输出可读字段。"""
    return {
        "开始": _fmt_hm(window["开始"]),
        "结束": _fmt_hm(window.get("结束", window["开始"])),
        "最高高度角（度）": round(window["最高高度角"], 1),
        "最高时刻": _fmt_hm(window["最高时刻"]),
        "最高时方位（度）": round(window["最高时方位"], 1),
    }


# ------ 声明式收集点（ToolSpec 真源；新增工具 = 加一行）------
TOOLS: tuple[Tool, ...] = (
    PureTool(spec=_SPEC_SUN_TIMES, func=sun_times),
    PureTool(spec=_SPEC_SUN_POSITION, func=sun_position),
    PureTool(spec=_SPEC_MOON_PHASE, func=moon_phase),
    PureTool(spec=_SPEC_MOON_EVENTS, func=moon_events),
    PureTool(spec=_SPEC_GALAXY_VISIBILITY, func=galaxy_visibility),
)
