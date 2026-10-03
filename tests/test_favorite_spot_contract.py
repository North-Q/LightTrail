"""S1-2 机位输入契约保证坐标、来源和时区语义明确。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from lighttrail.api.app import create_app
from lighttrail.contracts.context import RequestContext
from lighttrail.contracts.favorite_spot import FavoriteSpotInput, FavoriteSpotRecord
from lighttrail.memory.manager import MemoryManager
from lighttrail.memory.profile import UserProfile


def _spot() -> dict[str, object]:
    """生成一个可计算的 WGS84 机位。"""
    return {
        "name": "东滩",
        "latitude": 31.52,
        "longitude": 121.95,
        "coordinate_system": "WGS84",
        "tz_offset": "+08:00",
        "direction_deg": 125,
        "subject_types": ["银河"],
        "notes": "潮汐涨落需复核",
        "source": "用户实测",
    }


def test_new_spot_requires_explicit_coordinate_system() -> None:
    """新坐标必须声明坐标系，经纬度不得只填一项。"""
    assert FavoriteSpotInput.model_validate(_spot()).coordinate_system == "WGS84"
    for mutation in (
        {"latitude": 91},
        {"longitude": 181},
        {"longitude": None},
        {"coordinate_system": None},
        {"tz_offset": None},
        {"coordinate_system": "EPSG:9999"},
        {"tz_offset": "+08:60"},
        {"source": "   "},
    ):
        with pytest.raises(ValidationError):
            FavoriteSpotInput.model_validate({**_spot(), **mutation})


def test_spot_without_coordinates_is_explicitly_incomplete() -> None:
    """允许先收藏名称，但不能伪装为可计算机位。"""
    spot = FavoriteSpotInput.model_validate(
        {
            "name": "待勘察机位",
            "source": "用户收藏",
        }
    )
    assert spot.latitude is None and spot.coordinate_system is None
    record = FavoriteSpotRecord.model_validate(
        {
            **spot.model_dump(),
            "id": "spot-1",
            "coordinate_state": "missing_coordinates",
        }
    )
    assert record.coordinate_state == "missing_coordinates"


def test_legacy_unlabeled_coordinates_are_not_ready() -> None:
    """旧档案缺坐标系时只能标记未知，不能当作 WGS84 参与计算。"""
    legacy = {**_spot(), "coordinate_system": None, "id": "legacy-1"}
    record = FavoriteSpotRecord.model_validate(
        {
            **legacy,
            "coordinate_state": "unknown_system",
        }
    )
    assert record.coordinate_state == "unknown_system"
    with pytest.raises(ValidationError):
        FavoriteSpotRecord.model_validate({**legacy, "coordinate_state": "ready"})


def test_non_wgs84_coordinates_remain_labeled_but_not_calculable() -> None:
    """GCJ-02 数据在转换能力落地前不得静默交给 WGS84 工具。"""
    spot = FavoriteSpotRecord.model_validate(
        {
            **_spot(),
            "coordinate_system": "GCJ-02",
            "id": "spot-2",
            "coordinate_state": "unsupported_system",
        }
    )
    assert spot.coordinate_system == "GCJ-02"


def test_favorite_spot_contract_is_exported_to_openapi() -> None:
    """机位编辑器未来可直接消费 OpenAPI 生成的输入和记录类型。"""
    schemas = create_app().openapi()["components"]["schemas"]
    fields = schemas["FavoriteSpotRecord"]["properties"]
    assert {
        "id",
        "name",
        "latitude",
        "longitude",
        "coordinate_system",
        "tz_offset",
        "direction_deg",
        "subject_types",
        "notes",
        "source",
        "coordinate_state",
    } <= fields.keys()
    assert "FavoriteSpotInput" in schemas


def test_failed_spot_write_restores_in_memory_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """档案写盘失败时不能只在当前进程里出现新机位。"""
    manager = MemoryManager(tmp_path)
    context = RequestContext()

    def fail_save(self: UserProfile, data_dir: str | Path) -> Path:
        """模拟磁盘写入失败。"""
        raise OSError("disk unavailable")

    monkeypatch.setattr(UserProfile, "save", fail_save)
    with pytest.raises(OSError, match="disk unavailable"):
        manager.create_favorite_spot(context, FavoriteSpotInput.model_validate(_spot()))
    assert manager.list_favorite_spots(context).records == ()
