"""将已确认的用户机位与本地天文结果组装为可核验的单机位计划。

天气、通行和镜头能力没有证据时只给出缺口，不让模型猜测关键事实。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from typing import Any

from lighttrail.contracts.favorite_spot import FavoriteSpotRecord
from lighttrail.contracts.spot_plan import (
    GeoPoint,
    PlanDirection,
    PlanEvidenceRef,
    PlanningRequest,
    PlanParameter,
    PlanRisk,
    PlanSite,
    PlanWindow,
    SpotPlan,
)

ToolDispatch = Callable[[str, str], str]

_SOLAR_SUBJECTS: dict[str, tuple[str, str]] = {
    "日出": ("黄金时刻（晨）", "日出方位（度）"),
    "日落": ("黄金时刻（暮）", "日落方位（度）"),
    "蓝调": ("蓝调时刻（暮）", "日落方位（度）"),
}
_WEATHER_SUBJECTS = {"火烧云", "晚霞"}


class SpotPlanInputError(ValueError):
    """请求的机位 ID、名称或时区与当前用户档案不一致。"""


def _risk(code: str, detail: str, severity: str = "medium") -> PlanRisk:
    """构造稳定风险代码和用户可读说明。"""
    return PlanRisk(code=code, severity=severity, detail=detail)


def _incomplete(
    request: PlanningRequest,
    risks: list[PlanRisk],
    *,
    selected_date: date | None = None,
    site: PlanSite | None = None,
    direction: PlanDirection | None = None,
    shoot_window: PlanWindow | None = None,
    evidence_refs: list[PlanEvidenceRef] | None = None,
) -> SpotPlan:
    """将缺失关键事实的结果显式降级为 needs_data。"""
    return SpotPlan(
        status="needs_data",
        request=request,
        selected_date=selected_date,
        site=site,
        direction=direction,
        arrival_window=None,
        setup_window=None,
        shoot_window=shoot_window,
        departure_window=None,
        focal_length_mm=None,
        gear=[],
        parameters=[],
        risks=risks,
        evidence_refs=evidence_refs or [],
    )


def _tool_data(dispatch: ToolDispatch, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """通过请求作用域工具分发读取结构化结果，拒绝文本猜测。"""
    try:
        data = json.loads(dispatch(name, json.dumps(args, ensure_ascii=False)))
    except (TypeError, ValueError):
        return {"error": f"{name} 结果不是 JSON 对象"}
    return data if isinstance(data, dict) else {"error": f"{name} 结果不是 JSON 对象"}


def _solar_window(data: dict[str, Any], field: str) -> PlanWindow | None:
    """只接纳工具明确提供的带时区 ISO 窗口。"""
    windows = data.get(field)
    if not isinstance(windows, list) or not windows or not isinstance(windows[0], dict):
        return None
    first = windows[0]
    try:
        return PlanWindow(
            start=datetime.fromisoformat(first["开始_iso"]),
            end=datetime.fromisoformat(first["结束_iso"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _solar_direction(data: dict[str, Any], field: str) -> PlanDirection | None:
    """从天文结果获取目标方位，不从机位名称或自由文本推断。"""
    bearing = data.get(field)
    if not isinstance(bearing, (int, float)) or isinstance(bearing, bool):
        return None
    try:
        return PlanDirection(bearing_deg=float(bearing), reason=f"sun_times 计算的{field}")
    except ValueError:
        return None


def _surrounding_windows(shoot: PlanWindow) -> tuple[PlanWindow, PlanWindow, PlanWindow]:
    """按拍摄窗口反推到达和准备时段；缓冲是计划规则而非道路通勤估算。"""
    arrival = PlanWindow(
        start=shoot.start - timedelta(minutes=60),
        end=shoot.start - timedelta(minutes=40),
    )
    setup = PlanWindow(start=arrival.end, end=shoot.start)
    departure = PlanWindow(start=shoot.end, end=shoot.end + timedelta(minutes=20))
    return arrival, setup, departure


def _direction_conflict(spot: FavoriteSpotRecord, bearing: float) -> bool:
    """已记录主朝向背离目标天体超过 90 度时要求现场核实视线。"""
    if spot.direction_deg is None:
        return False
    difference = abs((spot.direction_deg - bearing + 180) % 360 - 180)
    return difference > 90


def build_spot_plan(
    request: PlanningRequest,
    favorites: Sequence[FavoriteSpotRecord],
    dispatch: ToolDispatch,
    *,
    camera_body: str = "",
) -> SpotPlan:
    """按当前用户的机位和确定性天文证据生成单机位计划。

    Args:
        request: 含题材、日期、时区和机位 ID 的结构化请求。
        favorites: 仅由当前请求用户档案读取的收藏机位。
        dispatch: 请求作用域的工具分发函数；测试可注入离线替身。
        camera_body: 当前用户明确保存的机身型号。

    Returns:
        完整太阳题材计划，或标明缺失事实的 needs_data 结果。

    Raises:
        SpotPlanInputError: 机位 ID、名称或时区与当前用户档案不一致。
    """
    if request.area is not None:
        return _incomplete(
            request,
            [_risk("site_unselected", "请先从区域中确认具体机位；系统不会凭地名猜坐标。")],
        )
    selector = request.spot
    if selector is None or not selector.site_id:
        raise SpotPlanInputError("请选择带稳定 ID 的收藏机位")
    spot = next((item for item in favorites if item.id == selector.site_id), None)
    if spot is None:
        raise SpotPlanInputError("当前用户没有该收藏机位")
    if spot.name != selector.name:
        raise SpotPlanInputError("机位 ID 与名称不一致，请刷新列表后重试")
    if spot.tz_offset != request.tz_offset:
        raise SpotPlanInputError("请求时区与收藏机位的拍摄地时区不一致")
    if request.date_range is not None:
        return _incomplete(
            request,
            [_risk("date_unselected", "请先从日期范围中确认单个拍摄日期。")],
        )
    selected_date = request.target_date
    if spot.coordinate_state != "ready" or spot.coordinate_system != "WGS84":
        return _incomplete(
            request,
            [_risk("spot_coordinates_unusable", "机位无已确认的 WGS84 坐标，不能参与天文计算。")],
            selected_date=selected_date,
        )
    if spot.latitude is None or spot.longitude is None or selected_date is None:
        return _incomplete(
            request,
            [_risk("spot_coordinates_missing", "机位坐标或拍摄日期缺失。")],
            selected_date=selected_date,
        )
    site = PlanSite(
        name=spot.name,
        site_id=spot.id,
        point=GeoPoint(
            latitude=spot.latitude,
            longitude=spot.longitude,
            coordinate_system="WGS84",
        ),
        source=spot.source,
    )
    evidence = [PlanEvidenceRef(source="favorite_spots", field="source")]
    subject = request.subject_type
    if subject not in _SOLAR_SUBJECTS and subject not in _WEATHER_SUBJECTS:
        return _incomplete(
            request,
            [_risk("target_unresolved", f"「{subject}」还需确认目标与参数，不能生成完整计划。")],
            selected_date=selected_date,
            site=site,
            evidence_refs=evidence,
        )

    data = _tool_data(
        dispatch,
        "sun_times",
        {
            "latitude": spot.latitude,
            "longitude": spot.longitude,
            "date": selected_date.isoformat(),
            "tz_offset": request.tz_offset,
        },
    )
    if "error" in data:
        return _incomplete(
            request,
            [_risk("astronomy_unavailable", "天文计算失败，请核对机位坐标与日期。")],
            selected_date=selected_date,
            site=site,
            evidence_refs=evidence,
        )
    if data.get("日期") != selected_date.isoformat() or data.get("时区") != request.tz_offset:
        return _incomplete(
            request,
            [_risk("astronomy_mismatch", "天文结果的日期或时区与本次选择不一致，已停止生成计划。")],
            selected_date=selected_date,
            site=site,
            evidence_refs=evidence,
        )
    window_field, direction_field = _SOLAR_SUBJECTS.get(
        subject, ("黄金时刻（暮）", "日落方位（度）")
    )
    shoot = _solar_window(data, window_field)
    direction = _solar_direction(data, direction_field)
    if shoot is None or direction is None:
        return _incomplete(
            request,
            [_risk("astronomy_unavailable", "该机位和日期没有可用的天文窗口，请核对日期与坐标。")],
            selected_date=selected_date,
            site=site,
            evidence_refs=evidence,
        )
    evidence.extend(
        [
            PlanEvidenceRef(source="sun_times", field=window_field),
            PlanEvidenceRef(source="sun_times", field=direction_field),
        ]
    )
    if _direction_conflict(spot, direction.bearing_deg):
        return _incomplete(
            request,
            [
                _risk(
                    "direction_conflict", "机位记录的主朝向背离目标方位，请现场确认视线或更换机位。"
                )
            ],
            selected_date=selected_date,
            site=site,
            direction=direction,
            shoot_window=shoot,
            evidence_refs=evidence,
        )
    if subject in _WEATHER_SUBJECTS:
        return _incomplete(
            request,
            [_risk("weather_unverified", "尚无目标日云量与降水实测/预报，不能判定晚霞或火烧云。")],
            selected_date=selected_date,
            site=site,
            direction=direction,
            shoot_window=shoot,
            evidence_refs=evidence,
        )

    arrival, setup, departure = _surrounding_windows(shoot)
    return SpotPlan(
        status="ready",
        request=request,
        selected_date=selected_date,
        site=site,
        direction=direction,
        arrival_window=arrival,
        setup_window=setup,
        shoot_window=shoot,
        departure_window=departure,
        focal_length_mm=24.0,
        gear=[camera_body.strip() or "相机", "可覆盖 24mm 的镜头", "三脚架"],
        parameters=[
            PlanParameter(
                name="焦距", value="24mm 起步", reason="保留风光取景空间，现场按构图调整"
            ),
            PlanParameter(name="光圈", value="f/8 起步", reason="风光景深基线，按镜头和前景调整"),
            PlanParameter(name="ISO", value="100 起步", reason="优先保留高光与动态范围"),
            PlanParameter(name="快门", value="现场测光", reason="光照和滤镜未知，不能预填精确秒数"),
        ],
        risks=[
            _risk("weather_unverified", "天气与现场可达性未核验，出发前请查实况。"),
            _risk("travel_time_unverified", "到达窗口未包含道路通勤，请自行预留路程。", "low"),
        ],
        evidence_refs=evidence,
    )


__all__ = ["SpotPlanInputError", "build_spot_plan"]
