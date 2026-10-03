"""旧收藏机位只读迁移预览：策略显式、身份隔离且不篡改原始数据。"""

from __future__ import annotations

import copy

import pytest

from lighttrail.memory.spot_migration import (
    SpotMigrationError,
    preview_favorite_spot_migration,
)


def _legacy_spots() -> list[object]:
    """覆盖零度坐标、已标注坐标系及非法坐标的旧档案夹具。"""
    return [
        {"名称": "本初子午线", "纬度": 0, "经度": 0, "题材": "星空"},
        {"name": "地图机位", "latitude": 31.2, "longitude": 121.5, "coordinate_system": "GCJ-02"},
        {"name": "非法机位", "latitude": 95, "longitude": 121.5},
    ]


def test_unknown_policy_preserves_unlabeled_coordinates_without_computation() -> None:
    """默认未拍板时的预览选项保留原始零值，但明确显示坐标系未知。"""
    raw = _legacy_spots()
    before = copy.deepcopy(raw)
    preview = preview_favorite_spot_migration(raw, "alice", policy="unknown")
    assert raw == before
    assert len(preview.records) == 2
    assert preview.records[0].latitude == 0
    assert preview.records[0].longitude == 0
    assert preview.records[0].coordinate_system is None
    assert preview.records[0].coordinate_state == "unknown_system"
    assert preview.records[0].subject_types == ["星空"]
    assert preview.records[1].coordinate_system == "GCJ-02"
    assert preview.records[1].coordinate_state == "unsupported_system"
    assert [(item.index, item.reason) for item in preview.skipped] == [(2, "invalid")]


def test_assume_or_skip_policy_is_explicit_and_deterministic() -> None:
    """预览两种替代方案，但不替用户选择、更不写入档案。"""
    raw = _legacy_spots()
    assumed = preview_favorite_spot_migration(raw, "alice", policy="assume_wgs84")
    skipped = preview_favorite_spot_migration(raw, "alice", policy="skip_unlabeled")
    assert assumed.records[0].coordinate_system == "WGS84"
    assert assumed.records[0].coordinate_state == "ready"
    assert [spot.name for spot in skipped.records] == ["地图机位"]
    assert [(item.index, item.reason) for item in skipped.skipped] == [
        (0, "policy"),
        (2, "invalid"),
    ]
    assert (
        assumed.records[0].id
        == preview_favorite_spot_migration(raw, "alice", policy="assume_wgs84").records[0].id
    )
    assert (
        assumed.records[0].id
        != preview_favorite_spot_migration(raw, "bob", policy="assume_wgs84").records[0].id
    )


def test_preview_rejects_implicit_policy_and_bad_identity() -> None:
    """调用方不能靠缺省值或非法用户标识绕开迁移策略。"""
    with pytest.raises(TypeError):
        preview_favorite_spot_migration([], "alice")  # type: ignore[call-arg]
    with pytest.raises(SpotMigrationError):
        preview_favorite_spot_migration([], "../bob", policy="unknown")
    with pytest.raises(SpotMigrationError):
        preview_favorite_spot_migration([], "alice", policy="invalid")  # type: ignore[arg-type]


def test_unrelated_legacy_deletion_does_not_change_spot_id() -> None:
    """旧机位临时 ID 不依赖其他记录在列表中的位置。"""
    original = _legacy_spots()
    before = preview_favorite_spot_migration(original, "alice", policy="unknown")
    after = preview_favorite_spot_migration(original[1:], "alice", policy="unknown")
    assert before.records[1].id == after.records[0].id
