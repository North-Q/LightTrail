"""收藏机位的结构化契约：保留坐标来源，禁止无坐标系数据参与计算。"""

from __future__ import annotations

from datetime import timedelta
from math import isfinite
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lighttrail.contracts.spot_plan import CoordinateSystem

CoordinateState = Literal["ready", "missing_coordinates", "unknown_system", "unsupported_system"]


class _SpotFields(BaseModel):
    """机位共用字段；旧档案兼容由持久化记录层负责。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=120, description="机位名称，如崇明东滩")
    latitude: float | None = Field(default=None, ge=-90, le=90, description="纬度，北纬为正")
    longitude: float | None = Field(default=None, ge=-180, le=180, description="经度，东经为正")
    coordinate_system: CoordinateSystem | None = Field(
        default=None, description="坐标系；旧机位未知时为空，不得参与计算"
    )
    tz_offset: str | None = Field(
        default=None, pattern=r"^[+-]\d{2}:\d{2}$", description="拍摄地时区偏移，如 +08:00"
    )
    direction_deg: float | None = Field(default=None, ge=0, lt=360, description="主要朝向方位角")
    subject_types: list[str] = Field(default_factory=list, description="适合拍摄的题材")
    notes: str = Field(default="", max_length=1000, description="机位备注")
    source: str = Field(..., min_length=1, max_length=120, description="坐标或机位信息来源")

    @field_validator("name", "source")
    @classmethod
    def _nonblank(cls, value: str) -> str:
        """拒绝仅由空格构成的机位名称和来源。"""
        clean = value.strip()
        if not clean:
            raise ValueError("机位名称和来源不能为空")
        return clean

    @field_validator("latitude", "longitude")
    @classmethod
    def _coordinate_precision(cls, value: float | None) -> float | None:
        """本地坐标最多保存六位小数，拒绝非有限数。

        Args:
            value: 待保存的纬度或经度。

        Returns:
            保留六位小数后的坐标，或空值。
        """
        if value is None:
            return None
        if not isfinite(value):
            raise ValueError("坐标必须是有限数")
        return round(value, 6)

    @field_validator("tz_offset")
    @classmethod
    def _valid_timezone(cls, value: str | None) -> str | None:
        """拒绝不合法的 UTC 偏移。"""
        if value is None:
            return None
        hours, minutes = (int(part) for part in value[1:].split(":"))
        if minutes > 59 or timedelta(hours=hours, minutes=minutes) > timedelta(hours=14):
            raise ValueError("机位时区偏移超出有效范围")
        return value

    @model_validator(mode="after")
    def _coordinate_pair(self) -> _SpotFields:
        """经纬度必须成对填写；没有坐标时不能标记坐标系。"""
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("经纬度必须同时填写或同时留空")
        if self.latitude is None and self.coordinate_system is not None:
            raise ValueError("没有经纬度时不得填写坐标系")
        return self


class FavoriteSpotInput(_SpotFields):
    """新增或完整更新机位的输入；有坐标时必须声明坐标系与时区。"""

    @model_validator(mode="after")
    def _explicit_system(self) -> FavoriteSpotInput:
        """有坐标的新机位必须声明坐标系和拍摄地时区。"""
        if self.latitude is not None and self.coordinate_system is None:
            raise ValueError("新机位有坐标时必须指定坐标系")
        if self.latitude is not None and self.tz_offset is None:
            raise ValueError("新机位有坐标时必须指定拍摄地时区")
        return self


class FavoriteSpotRecord(_SpotFields):
    """已保存机位；兼容旧档案中尚待用户确认坐标系的记录。"""

    id: str = Field(..., min_length=1, description="稳定机位标识")
    coordinate_state: CoordinateState = Field(..., description="机位能否参与 WGS84 计算")

    @model_validator(mode="after")
    def _consistent_state(self) -> FavoriteSpotRecord:
        """使可计算状态与坐标和坐标系一致。"""
        if self.latitude is None:
            expected = "missing_coordinates"
        elif self.coordinate_system is None:
            expected = "unknown_system"
        elif self.coordinate_system != "WGS84":
            expected = "unsupported_system"
        else:
            expected = "ready"
        if self.coordinate_state != expected:
            raise ValueError("机位坐标状态与字段不一致")
        return self


__all__ = ["FavoriteSpotInput", "FavoriteSpotRecord"]
