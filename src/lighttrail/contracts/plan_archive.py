"""S1-5 脱敏计划快照契约；保留行动与证据，不携带结构化经纬度字段。"""

from __future__ import annotations

from datetime import date, timedelta
from itertools import pairwise
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lighttrail.contracts.spot_plan import (
    CoordinateSystem,
    DateRange,
    PlanDirection,
    PlanEvidenceRef,
    PlanParameter,
    PlanRisk,
    PlanStatus,
    PlanWindow,
    SpotSelector,
)


class ArchivedArea(BaseModel):
    """去掉中心经纬度后的待选区域。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=120)
    radius_km: float = Field(..., gt=0, le=100)
    coordinate_system: CoordinateSystem


class ArchivedPlanningRequest(BaseModel):
    """保留题材、日期、时区及位置语义的脱敏规划请求。"""

    model_config = ConfigDict(extra="forbid")

    subject_type: str = Field(..., min_length=1, max_length=40)
    target_date: date | None = None
    date_range: DateRange | None = None
    tz_offset: str = Field(..., pattern=r"^[+-]\d{2}:\d{2}$")
    spot: SpotSelector | None = None
    area: ArchivedArea | None = None

    @model_validator(mode="after")
    def _valid_choices(self) -> ArchivedPlanningRequest:
        """继承规划请求的日期、位置和时区选择约束。"""
        if (self.target_date is None) == (self.date_range is None):
            raise ValueError("必须且只能指定拍摄日期或日期范围")
        if (self.spot is None) == (self.area is None):
            raise ValueError("必须且只能指定机位或区域")
        hours, minutes = (int(part) for part in self.tz_offset[1:].split(":"))
        if minutes > 59 or hours > 14 or (hours == 14 and minutes != 0):
            raise ValueError("拍摄地时区偏移无效")
        if not self.subject_type.strip():
            raise ValueError("拍摄题材不能为空")
        return self


class ArchivedPlanSite(BaseModel):
    """计划机位的非坐标属性；坐标系保留以防误解。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    site_id: str | None = None
    coordinate_system: CoordinateSystem
    source: str = Field(..., min_length=1)


class ArchivedSpotPlan(BaseModel):
    """完整行动与证据快照，刻意不包含请求副本和经纬度。"""

    model_config = ConfigDict(extra="forbid")

    status: PlanStatus
    selected_date: date | None
    site: ArchivedPlanSite | None
    direction: PlanDirection | None
    arrival_window: PlanWindow | None
    setup_window: PlanWindow | None
    shoot_window: PlanWindow | None
    departure_window: PlanWindow | None
    focal_length_mm: float | None = Field(..., gt=0)
    gear: list[str]
    parameters: list[PlanParameter]
    risks: list[PlanRisk]
    evidence_refs: list[PlanEvidenceRef]


class PlanArchive(BaseModel):
    """版本化的离线计划包；导入不调用任何实时数据源。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    format: Literal["lighttrail.spot-plan"] = "lighttrail.spot-plan"
    privacy: Literal["coordinates_removed"] = "coordinates_removed"
    request: ArchivedPlanningRequest
    plan: ArchivedSpotPlan

    @model_validator(mode="after")
    def _consistent_plan(self) -> PlanArchive:
        """拒绝把日期、机位或时间窗不一致的文件标为可执行计划。"""
        request = self.request
        plan = self.plan
        if plan.selected_date is not None:
            if request.target_date is not None and plan.selected_date != request.target_date:
                raise ValueError("快照日期与请求日期不一致")
            if request.date_range is not None and not (
                request.date_range.start <= plan.selected_date <= request.date_range.end
            ):
                raise ValueError("快照日期不在请求范围内")
        if plan.site is not None and request.spot is not None:
            if request.spot.site_id is not None and plan.site.site_id != request.spot.site_id:
                raise ValueError("快照机位 ID 与请求不一致")
            if request.spot.site_id is None and plan.site.name != request.spot.name:
                raise ValueError("快照机位名称与请求不一致")
        if plan.status != "ready":
            return self
        required = (
            plan.selected_date,
            plan.site,
            plan.direction,
            plan.arrival_window,
            plan.setup_window,
            plan.shoot_window,
            plan.departure_window,
            plan.focal_length_mm,
        )
        if any(value is None for value in required):
            raise ValueError("可执行快照缺少行动要素")
        if not plan.gear or not plan.parameters or not plan.evidence_refs:
            raise ValueError("可执行快照缺少器材、参数或证据")
        windows = (
            plan.arrival_window,
            plan.setup_window,
            plan.shoot_window,
            plan.departure_window,
        )
        sign = 1 if request.tz_offset.startswith("+") else -1
        hours, minutes = (int(part) for part in request.tz_offset[1:].split(":"))
        offset = sign * timedelta(hours=hours, minutes=minutes)
        if any(
            value.utcoffset() != offset
            for window in windows
            for value in (window.start, window.end)
        ):
            raise ValueError("快照时间窗与拍摄地时区不一致")
        if any(earlier.end > later.start for earlier, later in pairwise(windows)):
            raise ValueError("快照行动窗口顺序无效")
        return self


__all__ = [
    "ArchivedArea",
    "ArchivedPlanSite",
    "ArchivedPlanningRequest",
    "ArchivedSpotPlan",
    "PlanArchive",
]
