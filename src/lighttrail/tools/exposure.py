"""内置工具：摄影曝光计算（演示用领域工具）。

当前实现为最小链路演示工具，后续将在独立模块中扩展为完整的
曝光组合推荐（结合天文 / 气象数据）。
"""

from __future__ import annotations

import math

from lighttrail.agent.tools import registry

# 常见快门速度档位（秒），供就近取值参考
_STOPS_ISO = [50, 100, 200, 400, 800, 1600, 3200, 6400, 12800, 25600, 51200]


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
def equivalent_exposure(f_stop: float, shutter_speed: float, iso: int, adjust_stops: float = 0) -> dict:
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
