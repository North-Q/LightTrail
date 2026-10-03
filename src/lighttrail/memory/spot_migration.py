"""旧收藏机位的只读迁移预览；坐标系策略必须由调用方显式传入。"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError

from lighttrail.contracts.favorite_spot import FavoriteSpotRecord

LegacyCoordinatePolicy = Literal["unknown", "assume_wgs84", "skip_unlabeled"]


class SpotMigrationError(ValueError):
    """迁移预览参数不合法。"""


@dataclass(frozen=True)
class SkippedSpot:
    """未能转换的旧机位，只记录序号与原因，不复制敏感坐标。"""

    index: int
    reason: Literal["policy", "invalid"]


@dataclass(frozen=True)
class SpotMigrationPreview:
    """只读预览结果；调用本函数不会写回 profile.json。"""

    records: tuple[FavoriteSpotRecord, ...]
    skipped: tuple[SkippedSpot, ...]
    record_indices: tuple[int, ...] = ()


def _field(row: dict[str, Any], english: str, chinese: str) -> Any:
    """兼容旧 profile 的中英文键，同时保留合法的数值零。"""
    return row[english] if english in row else row.get(chinese)


def _legacy_record(
    row: dict[str, Any], user_id: str, occurrence: int, policy: LegacyCoordinatePolicy
) -> FavoriteSpotRecord | None:
    """按显式策略投影一条旧机位；策略跳过时返回空。"""
    latitude = _field(row, "latitude", "纬度")
    longitude = _field(row, "longitude", "经度")
    coordinate_system = _field(row, "coordinate_system", "坐标系")
    if coordinate_system is None:
        if policy == "skip_unlabeled":
            return None
        if policy == "assume_wgs84" and latitude is not None and longitude is not None:
            coordinate_system = "WGS84"
    if latitude is None:
        state = "missing_coordinates"
    elif coordinate_system is None:
        state = "unknown_system"
    elif coordinate_system == "WGS84":
        state = "ready"
    else:
        state = "unsupported_system"

    subject_types = row.get("subject_types")
    if subject_types is None:
        subject = _field(row, "subject", "题材")
        subject_types = [subject] if isinstance(subject, str) and subject.strip() else []
    source = row.get("source") or "旧档案，来源未记录"
    fingerprint = json.dumps(row, ensure_ascii=False, sort_keys=True)
    site_id = str(
        row.get("id") or uuid5(NAMESPACE_URL, f"lighttrail:{user_id}:{fingerprint}:{occurrence}")
    )
    return FavoriteSpotRecord.model_validate(
        {
            "id": site_id,
            "name": _field(row, "name", "名称"),
            "latitude": latitude,
            "longitude": longitude,
            "coordinate_system": coordinate_system,
            "tz_offset": row.get("tz_offset"),
            "direction_deg": row.get("direction_deg"),
            "subject_types": subject_types,
            "notes": row.get("notes") or "",
            "source": source,
            "coordinate_state": state,
        }
    )


def preview_favorite_spot_migration(
    raw_spots: list[Any], user_id: str, *, policy: LegacyCoordinatePolicy
) -> SpotMigrationPreview:
    """预览旧档案机位转换，绝不修改输入或磁盘。

    Args:
        raw_spots: 旧 profile 中的收藏机位列表。
        user_id: 用户命名空间，用于生成稳定且互相隔离的机位 ID。
        policy: 未标注坐标系机位的明确处理方式，无默认值。

    Returns:
        可转换记录与无法转换的索引/原因。

    Raises:
        SpotMigrationError: 策略或用户标识非法。
    """
    if policy not in ("unknown", "assume_wgs84", "skip_unlabeled"):
        raise SpotMigrationError("必须明确选择旧机位坐标系策略")
    if not user_id or any(
        char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        for char in user_id
    ):
        raise SpotMigrationError("用户标识只能包含字母、数字、下划线和连字符")
    records: list[FavoriteSpotRecord] = []
    skipped: list[SkippedSpot] = []
    record_indices: list[int] = []
    occurrences: dict[str, int] = {}
    for index, row in enumerate(raw_spots):
        if not isinstance(row, dict):
            skipped.append(SkippedSpot(index, "invalid"))
            continue
        latitude = _field(row, "latitude", "纬度")
        longitude = _field(row, "longitude", "经度")
        if (latitude is None) != (longitude is None):
            skipped.append(SkippedSpot(index, "invalid"))
            continue
        if isinstance(latitude, bool) or isinstance(longitude, bool):
            skipped.append(SkippedSpot(index, "invalid"))
            continue
        if latitude is not None:
            try:
                lat = float(latitude)
                lon = float(longitude)
            except (TypeError, ValueError):
                skipped.append(SkippedSpot(index, "invalid"))
                continue
            if not (
                math.isfinite(lat)
                and math.isfinite(lon)
                and -90 <= lat <= 90
                and -180 <= lon <= 180
            ):
                skipped.append(SkippedSpot(index, "invalid"))
                continue
        try:
            fingerprint = json.dumps(row, ensure_ascii=False, sort_keys=True)
            occurrence = occurrences.get(fingerprint, 0)
            occurrences[fingerprint] = occurrence + 1
            record = _legacy_record(row, user_id, occurrence, policy)
        except (TypeError, ValueError, ValidationError):
            skipped.append(SkippedSpot(index, "invalid"))
            continue
        if record is None:
            skipped.append(SkippedSpot(index, "policy"))
        else:
            records.append(record)
            record_indices.append(index)
    return SpotMigrationPreview(tuple(records), tuple(skipped), tuple(record_indices))


__all__ = ["SpotMigrationError", "SpotMigrationPreview", "preview_favorite_spot_migration"]
