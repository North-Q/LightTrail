"""内置工具：摄影曝光计算。

覆盖曝光三角的三个核心场景：
- 等效曝光换算（equivalent_exposure）：曝光总量不变时的光圈 / 快门 / ISO 三向换算；
- 星空曝光（star_shutter_rule）：500 法则与 NPF 法则计算最大不拖线快门；
- 长曝光 ND 减光（nd_long_exposure）：基准曝光 + ND 滤镜档位 -> 长曝光快门。
"""

from __future__ import annotations

import math

from lighttrail.agent.tools import registry

# 常见快门速度档位（秒），供就近取值参考
_STOPS_ISO = [50, 100, 200, 400, 800, 1600, 3200, 6400, 12800, 25600, 51200]

# 常见 ND 滤镜 -> 减光档数（+1 档 = 进光量减半）
_ND_FILTER_STOPS: dict[str, float] = {
    "ND4": 2.0,
    "ND8": 3.0,
    "ND16": 4.0,
    "ND32": 5.0,
    "ND64": 6.0,
    "ND128": 7.0,
    "ND256": 8.0,
    "ND400": 8.66,
    "ND1000": 10.0,
}


@registry.tool(
    name="equivalent_exposure",
    description=(
        "计算等效曝光：给定当前光圈、快门、ISO，在目标调整档数下，返回分别调整"
        "光圈 / 快门 / ISO 三种方式的等效参数组合。适用于星空、长曝光、风光等场景"
        "在保证曝光总量不变（或按目标增减）的前提下换算参数。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "f_stop": {
                "type": "number",
                "description": "当前光圈值，如 2.8 表示 f/2.8",
            },
            "shutter_speed": {
                "type": "number",
                "description": "当前快门速度（秒），如 30 表示 30s，0.5 表示 1/2s",
            },
            "iso": {
                "type": "integer",
                "description": "当前 ISO，如 400",
            },
            "adjust_stops": {
                "type": "number",
                "description": "目标曝光调整档数：正数增加进光量，负数减少；0 表示曝光总量不变",
                "default": 0,
            },
        },
        "required": ["f_stop", "shutter_speed", "iso"],
    },
)
def equivalent_exposure(
    f_stop: float, shutter_speed: float, iso: int, adjust_stops: float = 0
) -> dict:
    """按目标档数计算三种等效调整方案。

    换算依据（+1 档 = 进光量 ×2）：
    - 光圈：进光 ∝ 1/f^2，故 f 值变化因子为 1/sqrt(2**adjust_stops)；
    - 快门：曝光时间变化因子为 2**adjust_stops；
    - ISO：感光度变化因子为 2**adjust_stops。
    """
    if f_stop <= 0 or shutter_speed <= 0 or iso <= 0:
        return {"error": "光圈、快门、ISO 均须为正数"}
    factor = 2.0**adjust_stops
    plans = {
        "光圈优先": {
            "f_stop": round(f_stop / math.sqrt(factor), 2),
            "shutter_speed": shutter_speed,
            "iso": iso,
        },
        "快门优先": {
            "f_stop": f_stop,
            "shutter_speed": round(shutter_speed * factor, 2),
            "iso": iso,
        },
        "ISO 优先": {
            "f_stop": f_stop,
            "shutter_speed": shutter_speed,
            "iso": round(iso * factor),
        },
    }
    return {
        "adjust_stops": adjust_stops,
        "曝光总量变化": "不变" if adjust_stops == 0 else f"{adjust_stops:+.1f} 档",
        "等效方案": plans,
        "提示": "计算值为连续档换算，实际使用请就近取相机档位（光圈 1/3 档、快门整档或 1/2 档、ISO 整档）",
    }


# ------------------------------------------------------------------
# 星空 / 长曝光（决策主线 D3.2-02 / D3.2-03）
# ------------------------------------------------------------------
def _format_shutter(seconds: float) -> str:
    """把秒数格式化为易读快门描述（1s 以上显示 8s，短于 1s 显示 1/125s）。"""
    if seconds >= 1.0:
        value = round(seconds, 1)
        return f"{value:g}s"
    reciprocal = round(1.0 / seconds)
    return f"1/{reciprocal}s"


@registry.tool(
    name="star_shutter_rule",
    description=(
        "计算星空摄影的最大不拖线快门时间，支持 500 法则与 NPF 法则。"
        "500 法则：最大快门约等于 500 / 等效焦距，简单但偏保守；"
        "NPF 法则额外考虑像素间距与光圈，更适合高像素机身与画面边缘星点要求。"
        "适用场景：银河、星野、星轨单张、流星雨等需要固定机位长曝的星空题材。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "focal_length": {
                "type": "number",
                "description": "镜头焦距（毫米），如 14 表示 14mm",
            },
            "aperture": {
                "type": "number",
                "description": "光圈值，如 2.8 表示 f/2.8",
            },
            "crop_factor": {
                "type": "number",
                "description": "画幅裁切系数：全画幅 1.0，APS-C 约 1.5，M43 约 2.0",
                "default": 1.0,
            },
            "pixel_pitch": {
                "type": "number",
                "description": "传感器像素间距（微米）。松下 S5M2（2400 万像素全画幅）约 6.0；"
                "APS-C 2400 万像素约 3.9。不填则只给 500 法则结果",
                "default": None,
            },
            "declination": {
                "type": "number",
                "description": "拍摄目标赤纬（度）。银心约 -29，使用 NPF 时按此修正；不填按 0 度（天赤道）",
                "default": 0.0,
            },
        },
        "required": ["focal_length", "aperture"],
    },
)
def star_shutter_rule(
    focal_length: float,
    aperture: float,
    crop_factor: float = 1.0,
    pixel_pitch: float | None = None,
    declination: float = 0.0,
) -> dict:
    """按 500 / NPF 法则计算最大不拖线快门并给出推荐。

    Args:
        focal_length: 镜头焦距（毫米）。
        aperture: 光圈 f 值。
        crop_factor: 画幅裁切系数。
        pixel_pitch: 传感器像素间距（微米），None 时不做 NPF 计算。
        declination: 目标赤纬（度），用于 NPF 的赤纬修正。

    Returns:
        包含 500 法则 / NPF 法则结果与使用建议的字典。
    """
    if focal_length <= 0 or aperture <= 0 or crop_factor <= 0:
        return {"error": "焦距、光圈、裁切系数均须为正数"}
    if pixel_pitch is not None and pixel_pitch <= 0:
        return {"error": "像素间距须为正数"}

    eff_focal = focal_length * crop_factor
    rule_500 = 500.0 / eff_focal

    npf_result: float | None = None
    if pixel_pitch is not None:
        declination_correction = max(math.cos(math.radians(declination)), 0.1)
        npf_result = (35.0 * aperture + 30.0 * pixel_pitch) / eff_focal / declination_correction

    return {
        "等效焦距（毫米）": round(eff_focal, 1),
        "500 法则最大快门（秒）": round(rule_500, 1),
        "500 法则快门": _format_shutter(rule_500),
        "NPF 法则最大快门（秒）": round(npf_result, 1) if npf_result is not None else None,
        "NPF 法则快门": _format_shutter(npf_result) if npf_result is not None else None,
        "采用建议": (
            "有像素间距数据，优先按 NPF 法则（更精确）；500 法则作为保守参考"
            if npf_result is not None
            else "无像素间距数据，按 500 法则"
        ),
        "提示": (
            "NPF 会随像素密度收紧快门：高像素机身建议就近向下取整档位；"
            "实际使用请结合现场星点放大检查微调"
        ),
    }


@registry.tool(
    name="nd_long_exposure",
    description=(
        "ND 滤镜长曝光换算：给定基准快门与 ND 减光档数（或滤镜型号），计算加滤镜后的长曝光快门；"
        "若同时给出目标快门，则反推所需 ND 档数并推荐最近滤镜型号。"
        "适用场景：水流雾化、海面拉丝、云层流动、车轨、人流虚化等慢门长曝光题材。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "base_shutter": {
                "type": "number",
                "description": "基准快门时间（秒），如 0.008 表示 1/125s，0.5 表示 1/2s，30 表示 30s",
            },
            "nd_stops": {
                "type": "number",
                "description": "ND 减光档数，如 ND8 为 3 档、ND64 为 6 档、ND1000 为 10 档",
                "default": None,
            },
            "nd_filter": {
                "type": "string",
                "description": "ND 滤镜型号（与 nd_stops 二选一），支持 ND4/8/16/32/64/128/256/400/1000",
                "default": None,
            },
            "target_shutter": {
                "type": "number",
                "description": "目标长曝光快门（秒），如 2 表示希望最终快门 2s；提供后反向推荐所需 ND",
                "default": None,
            },
        },
        "required": ["base_shutter"],
    },
)
def nd_long_exposure(
    base_shutter: float,
    nd_stops: float | None = None,
    nd_filter: str | None = None,
    target_shutter: float | None = None,
) -> dict:
    """ND 长曝光正反向换算。

    Args:
        base_shutter: 不加滤镜时的基准快门（秒）。
        nd_stops: ND 减光档数，与 nd_filter 二选一。
        nd_filter: ND 滤镜型号，如 ND64。
        target_shutter: 可选目标快门（秒），给出后反推所需 ND。

    Returns:
        包含档数、结果快门与建议的字典。
    """
    if base_shutter <= 0:
        return {"error": "基准快门须为正数"}
    if target_shutter is not None and target_shutter <= 0:
        return {"error": "目标快门须为正数"}

    if nd_filter:
        normalized = str(nd_filter).strip().upper()
        if normalized not in _ND_FILTER_STOPS:
            return {
                "error": f"未知 ND 滤镜：{nd_filter}，支持：{', '.join(sorted(_ND_FILTER_STOPS))}"
            }
        nd_stops = _ND_FILTER_STOPS[normalized]

    if target_shutter is not None:
        if nd_stops is not None:
            return {
                "error": "反推模式请只提供 base_shutter 与 target_shutter，勿同时给定 nd_stops/nd_filter"
            }
        required_stops = math.log2(target_shutter / base_shutter)
        if required_stops <= 0:
            return {
                "error": f"目标快门 {_format_shutter(target_shutter)} 不慢于基准快门，无需 ND 减光"
            }
        closest_filter = min(
            _ND_FILTER_STOPS, key=lambda name: abs(_ND_FILTER_STOPS[name] - required_stops)
        )
        actual_stops = _ND_FILTER_STOPS[closest_filter]
        actual_shutter = base_shutter * (2.0**actual_stops)
        return {
            "模式": "反推所需 ND",
            "基准快门": _format_shutter(base_shutter),
            "目标快门": _format_shutter(target_shutter),
            "所需减光档数": round(required_stops, 1),
            "推荐滤镜": closest_filter,
            "推荐后实际快门": _format_shutter(actual_shutter),
            "偏差说明": (
                "推荐滤镜档位大于所需，实际快门会慢于目标（可用等效曝光换算微调 ISO 补偿）"
                if actual_stops > required_stops + 0.2
                else "档位匹配"
            ),
        }

    if nd_stops is None:
        return {"error": "须提供 nd_stops 或 nd_filter（二选一）"}
    if nd_stops <= 0:
        return {"error": "ND 档数须为正数"}

    long_shutter = base_shutter * (2.0**nd_stops)
    return {
        "模式": "正向换算",
        "基准快门": _format_shutter(base_shutter),
        "ND 减光档数": round(nd_stops, 2),
        "长曝光快门": _format_shutter(long_shutter),
        "长曝光快门（秒）": round(long_shutter, 2),
        "提示": "长曝光优先用快门线 / 机内延时或 B 门遥控，避免按快门产生机震",
    }
