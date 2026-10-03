"""单机位拍摄计划的结构化输入输出契约（S1-1）。

契约只定义可复现的事实、缺口与输出不变量；机位选择、天象计算和计划生成由应用层负责。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from itertools import pairwise
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CoordinateSystem = Literal["WGS84", "GCJ-02", "BD-09"]
PlanStatus = Literal["ready", "needs_data"]
RiskSeverity = Literal["low", "medium", "high"]


class DateRange(BaseModel):
    """可选拍摄日期范围，边界均包含在内。"""

    model_config = ConfigDict(extra="forbid")

    start: date = Field(..., description="首个候选拍摄日期，如 2026-10-03")
    end: date = Field(..., description="最后一个候选拍摄日期，如 2026-10-05")

    @model_validator(mode="after")
    def _check_order(self) -> DateRange:
        """拒绝结束早于开始的日期范围。"""
        if self.end < self.start:
            raise ValueError("日期范围结束不得早于开始")
        return self


class GeoPoint(BaseModel):
    """携带坐标系的地理坐标；禁止将不同坐标系当作同一数值使用。"""

    model_config = ConfigDict(extra="forbid")

    latitude: float = Field(..., ge=-90, le=90, description="纬度，北纬为正，如 31.52")
    longitude: float = Field(..., ge=-180, le=180, description="经度，东经为正，如 121.95")
    coordinate_system: CoordinateSystem = Field(..., description="坐标系，必须显式标注")


class SpotSelector(BaseModel):
    """用户明确选择的收藏机位；S1-2 将赋予持久化机位标识。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=120, description="收藏机位名称，如崇明东滩")
    site_id: str | None = Field(default=None, description="稳定机位标识，尚未迁移时可为空")

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        """拒绝只有空白字符的机位名称。"""
        name = value.strip()
        if not name:
            raise ValueError("机位名称不能为空")
        return name


class SearchArea(BaseModel):
    """待选区域；规划器须在区域内确定实际机位后才能给出完整计划。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=120, description="区域名称，如崇明东部")
    center: GeoPoint = Field(..., description="区域中心坐标及坐标系")
    radius_km: float = Field(..., gt=0, le=100, description="候选搜索半径，单位公里")

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        """拒绝只有空白字符的待选区域。"""
        name = value.strip()
        if not name:
            raise ValueError("区域名称不能为空")
        return name


class PlanningRequest(BaseModel):
    """结构化拍摄请求：题材、日期、时区和位置选择均为必需语义。"""

    model_config = ConfigDict(extra="forbid")

    subject_type: str = Field(..., min_length=1, max_length=40, description="拍摄题材，如银河")
    target_date: date | None = Field(default=None, description="明确的本地拍摄日期")
    date_range: DateRange | None = Field(default=None, description="候选日期范围")
    tz_offset: str = Field(
        ..., pattern=r"^[+-]\d{2}:\d{2}$", description="拍摄地时区偏移，如 +08:00"
    )
    spot: SpotSelector | None = Field(default=None, description="用户明确选择的收藏机位")
    area: SearchArea | None = Field(default=None, description="仍需挑选机位的区域")

    @field_validator("subject_type")
    @classmethod
    def _valid_subject(cls, value: str) -> str:
        """拒绝空白题材，保留明确的本轮拍摄语义。"""
        subject = value.strip()
        if not subject:
            raise ValueError("拍摄题材不能为空")
        return subject

    @field_validator("tz_offset")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        """限制到真实 UTC 偏移范围，避免无效日期换算。"""
        hours, minutes = (int(part) for part in value[1:].split(":"))
        if minutes > 59:
            raise ValueError("时区偏移分钟必须小于 60")
        offset = timedelta(hours=hours, minutes=minutes)
        if offset > timedelta(hours=14) or (hours == 14 and minutes != 0):
            raise ValueError("时区偏移不得超过 ±14:00")
        return value

    @model_validator(mode="after")
    def _exclusive_choices(self) -> PlanningRequest:
        """日期和位置各须且只能明确一种选择。"""
        if (self.target_date is None) == (self.date_range is None):
            raise ValueError("必须且只能指定 target_date 或 date_range")
        if (self.spot is None) == (self.area is None):
            raise ValueError("必须且只能指定 spot 或 area")
        return self


class PlanSite(BaseModel):
    """本次实际机位与其坐标来源。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="本次拍摄机位名称")
    site_id: str | None = Field(default=None, description="收藏机位稳定标识")
    point: GeoPoint = Field(..., description="机位坐标及坐标系")
    source: str = Field(..., min_length=1, description="坐标来源，如用户收藏机位")

    @field_validator("name", "source")
    @classmethod
    def _nonblank(cls, value: str) -> str:
        """拒绝缺少实际名称或坐标来源的计划机位。"""
        clean = value.strip()
        if not clean:
            raise ValueError("计划机位名称和坐标来源不能为空")
        return clean


class PlanDirection(BaseModel):
    """拍摄朝向；方位角从正北顺时针计算。"""

    model_config = ConfigDict(extra="forbid")

    bearing_deg: float = Field(..., ge=0, lt=360, description="拍摄方位角，单位度")
    reason: str = Field(..., min_length=1, description="朝向依据与目标天体或光线")


class PlanWindow(BaseModel):
    """带时区的行动窗口。"""

    model_config = ConfigDict(extra="forbid")

    start: datetime = Field(..., description="窗口开始，ISO 8601 且含时区")
    end: datetime = Field(..., description="窗口结束，ISO 8601 且含时区")

    @model_validator(mode="after")
    def _check_window(self) -> PlanWindow:
        """时间窗必须带时区且结束晚于开始。"""
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("计划时间窗必须包含时区")
        if self.end <= self.start:
            raise ValueError("时间窗结束必须晚于开始")
        return self


class PlanParameter(BaseModel):
    """可执行拍摄参数及其依据。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="参数名，如光圈")
    value: str = Field(..., min_length=1, description="参数值，如 f/4")
    reason: str = Field(..., min_length=1, description="推荐理由")


class PlanEvidenceRef(BaseModel):
    """计划依据的结构化引用。"""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(..., min_length=1, description="数据源或工具名称")
    field: str = Field(..., min_length=1, description="被引用的结果字段")
    observed_at: datetime | None = Field(default=None, description="数据采集时刻；未知则为空")
    valid_until: datetime | None = Field(default=None, description="数据有效期；未知则为空")


class PlanRisk(BaseModel):
    """失败条件或数据缺口。"""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=1, description="稳定风险代码")
    severity: RiskSeverity = Field(..., description="风险严重程度")
    detail: str = Field(..., min_length=1, description="用户可理解的风险和应对")


class SpotPlan(BaseModel):
    """可执行单机位计划；缺关键字段时必须标为 needs_data。"""

    model_config = ConfigDict(extra="forbid")

    status: PlanStatus = Field(..., description="ready=可执行；needs_data=缺关键事实")
    request: PlanningRequest = Field(..., description="生成本计划的结构化请求快照")
    selected_date: date | None = Field(..., description="最终选择的本地拍摄日期")
    site: PlanSite | None = Field(..., description="本次实际机位与坐标")
    direction: PlanDirection | None = Field(..., description="拍摄朝向")
    arrival_window: PlanWindow | None = Field(..., description="到达窗口")
    setup_window: PlanWindow | None = Field(..., description="准备窗口")
    shoot_window: PlanWindow | None = Field(..., description="拍摄窗口")
    departure_window: PlanWindow | None = Field(..., description="离场窗口")
    focal_length_mm: float | None = Field(..., gt=0, description="建议焦距，单位毫米")
    gear: list[str] = Field(..., description="本次应携带的器材")
    parameters: list[PlanParameter] = Field(..., description="建议曝光等拍摄参数")
    risks: list[PlanRisk] = Field(..., description="天气、可达性或数据缺口风险")
    evidence_refs: list[PlanEvidenceRef] = Field(..., description="关键判断的数据依据引用")

    @model_validator(mode="after")
    def _ready_requires_actionable_fields(self) -> SpotPlan:
        """禁止把缺少执行要素的卡片伪装为完整计划。"""
        if self.selected_date is not None:
            if (
                self.request.target_date is not None
                and self.selected_date != self.request.target_date
            ):
                raise ValueError("计划日期与请求日期不一致")
            if self.request.date_range is not None and not (
                self.request.date_range.start <= self.selected_date <= self.request.date_range.end
            ):
                raise ValueError("计划日期不在请求范围内")
        if self.status != "ready":
            return self
        required = (
            self.selected_date,
            self.site,
            self.direction,
            self.arrival_window,
            self.setup_window,
            self.shoot_window,
            self.departure_window,
            self.focal_length_mm,
        )
        if any(value is None for value in required):
            raise ValueError("ready 计划缺少地点、日期、方向、焦距或行动窗口")
        if not self.gear or not self.parameters or not self.evidence_refs:
            raise ValueError("ready 计划必须包含器材、参数和证据引用")
        if self.request.spot is not None:
            if self.request.spot.site_id is not None:
                if self.site.site_id != self.request.spot.site_id:
                    raise ValueError("计划机位标识与明确选择不一致")
            elif self.site.name != self.request.spot.name:
                raise ValueError("计划机位名称与明确选择不一致")
        windows = (self.arrival_window, self.setup_window, self.shoot_window, self.departure_window)
        sign = 1 if self.request.tz_offset.startswith("+") else -1
        hours, minutes = (int(part) for part in self.request.tz_offset[1:].split(":"))
        requested_offset = sign * timedelta(hours=hours, minutes=minutes)
        if any(
            value.utcoffset() != requested_offset
            for window in windows
            for value in (window.start, window.end)
        ):
            raise ValueError("行动窗口时区偏移与请求拍摄地不一致")
        if any(earlier.end > later.start for earlier, later in pairwise(windows)):
            raise ValueError("行动窗口必须按到达、准备、拍摄、离场顺序排列且不得重叠")
        return self


__all__ = [
    "DateRange",
    "GeoPoint",
    "PlanDirection",
    "PlanEvidenceRef",
    "PlanParameter",
    "PlanRisk",
    "PlanSite",
    "PlanWindow",
    "PlanningRequest",
    "SearchArea",
    "SpotPlan",
    "SpotSelector",
]
