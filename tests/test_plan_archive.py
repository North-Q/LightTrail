"""S1-5 计划脱敏导出、冻结回放与导入边界回归。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from lighttrail.api.app import ApiDeps, create_app
from lighttrail.api.session import SessionManager
from lighttrail.application.plan_archive import archive_spot_plan
from lighttrail.application.spot_planner import build_spot_plan
from lighttrail.composition import build_application
from lighttrail.config import Settings
from lighttrail.contracts.favorite_spot import FavoriteSpotRecord
from lighttrail.contracts.plan_archive import PlanArchive
from lighttrail.contracts.spot_plan import PlanningRequest, SpotPlan
from lighttrail.tools.astronomy import sun_times


def _plan() -> SpotPlan:
    """使用确定性天文工具生成真实结构的合成计划。"""
    request = PlanningRequest.model_validate(
        {
            "subject_type": "日落",
            "target_date": "2026-10-03",
            "tz_offset": "+08:00",
            "spot": {"name": "东滩", "site_id": "spot-east"},
        }
    )
    spot = FavoriteSpotRecord.model_validate(
        {
            "id": "spot-east",
            "name": "东滩",
            "latitude": 31.52,
            "longitude": 121.95,
            "coordinate_system": "WGS84",
            "coordinate_state": "ready",
            "tz_offset": "+08:00",
            "source": "合成测试机位",
        }
    )

    def dispatch(name: str, raw_args: str) -> str:
        """只允许本地天文计算，不连接第三方服务。"""
        assert name == "sun_times"
        return json.dumps(sun_times(**json.loads(raw_args)), ensure_ascii=False)

    return build_spot_plan(request, [spot], dispatch)


def test_archive_removes_coordinates_and_replays_identical_semantics() -> None:
    """导出保留时间线与证据，但不能含结构化经纬度。"""
    plan = _plan()
    archive = archive_spot_plan(plan)
    encoded = archive.model_dump_json()
    assert archive.schema_version == 1
    assert archive.privacy == "coordinates_removed"
    assert "latitude" not in encoded and "longitude" not in encoded
    assert "121.95" not in encoded and "31.52" not in encoded
    restored = PlanArchive.model_validate_json(encoded)
    assert restored == archive
    assert restored.plan.selected_date == plan.selected_date
    assert restored.plan.shoot_window == plan.shoot_window
    assert restored.plan.parameters == plan.parameters
    assert restored.plan.evidence_refs == plan.evidence_refs
    assert restored.plan.site is not None and restored.plan.site.site_id == "spot-east"


def test_area_request_center_is_redacted_too() -> None:
    """即使未来从区域规划，也不能漏出区域中心坐标。"""
    request = PlanningRequest.model_validate(
        {
            "subject_type": "星空",
            "target_date": "2026-10-03",
            "tz_offset": "+08:00",
            "area": {
                "name": "东部",
                "center": {
                    "latitude": 31.52,
                    "longitude": 121.95,
                    "coordinate_system": "WGS84",
                },
                "radius_km": 5,
            },
        }
    )
    plan = build_spot_plan(request, [], lambda *_: pytest.fail("区域待选时不应触发工具"))
    archive = archive_spot_plan(plan)
    assert archive.request.area is not None
    assert archive.request.area.coordinate_system == "WGS84"
    assert "latitude" not in archive.model_dump_json()
    assert "longitude" not in archive.model_dump_json()


@pytest.mark.asyncio
async def test_archive_api_validates_import_without_live_dependencies(tmp_path: Path) -> None:
    """API 导出/导入保留语义，拒绝未知版本、伪造 ready 和坐标注入。"""
    settings = Settings(_env_file=None, data_dir=str(tmp_path), api_key="offline-test-key")
    services = build_application(settings)
    app = create_app(ApiDeps(services=services, sessions=SessionManager(tmp_path)))
    plan = _plan()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            exported = await client.post(
                "/api/spot-plans/archive", json=plan.model_dump(mode="json")
            )
            assert exported.status_code == 200
            assert exported.headers["cache-control"] == "private, no-store"
            payload = exported.json()
            assert "latitude" not in exported.text and "longitude" not in exported.text
            replayed = await client.post("/api/spot-plans/replay", json=payload)
            assert replayed.status_code == 200
            assert replayed.json() == payload
            assert replayed.headers["cache-control"] == "private, no-store"
            invalid_version = await client.post(
                "/api/spot-plans/replay", json={**payload, "schema_version": 99}
            )
            assert invalid_version.status_code == 422
            injected = json.loads(json.dumps(payload))
            injected["plan"]["site"]["latitude"] = 31.52
            assert (await client.post("/api/spot-plans/replay", json=injected)).status_code == 422
            false_ready = json.loads(json.dumps(payload))
            false_ready["plan"]["shoot_window"] = None
            assert (
                await client.post("/api/spot-plans/replay", json=false_ready)
            ).status_code == 422
    finally:
        services.close()
