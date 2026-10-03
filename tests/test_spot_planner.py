"""S1-4 结构化计划：当前用户机位、日期/时区一致性与证据降级。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from lighttrail.api.app import ApiDeps, create_app
from lighttrail.api.routes import get_request_context
from lighttrail.api.session import SessionManager
from lighttrail.application.spot_planner import SpotPlanInputError, build_spot_plan
from lighttrail.composition import build_application
from lighttrail.config import Settings
from lighttrail.contracts.context import RequestContext
from lighttrail.contracts.favorite_spot import FavoriteSpotInput, FavoriteSpotRecord
from lighttrail.contracts.spot_plan import PlanningRequest
from lighttrail.memory.manager import MemoryManager
from lighttrail.tools.astronomy import sun_times


def _record(
    *,
    spot_id: str = "spot-east",
    direction: float | None = None,
    state: str = "ready",
    system: str | None = "WGS84",
) -> FavoriteSpotRecord:
    """生成不依赖真实用户档案的合成机位。"""
    return FavoriteSpotRecord.model_validate(
        {
            "id": spot_id,
            "name": "东滩",
            "latitude": 31.52,
            "longitude": 121.95,
            "coordinate_system": system,
            "coordinate_state": state,
            "tz_offset": "+08:00",
            "direction_deg": direction,
            "source": "合成测试机位",
        }
    )


def _request(subject: str = "日落", *, spot_id: str = "spot-east") -> PlanningRequest:
    """生成含完整选择的合成规划请求。"""
    return PlanningRequest.model_validate(
        {
            "subject_type": subject,
            "target_date": "2026-10-03",
            "tz_offset": "+08:00",
            "spot": {"name": "东滩", "site_id": spot_id},
        }
    )


def _astronomy_dispatch(name: str, raw_args: str) -> str:
    """只允许确定性天文工具参与本组计划测试。"""
    assert name == "sun_times"
    return json.dumps(sun_times(**json.loads(raw_args)), ensure_ascii=False)


@pytest.mark.parametrize("subject", ["日出", "日落", "蓝调"])
def test_solar_plan_has_ordered_local_windows_and_evidence(subject: str) -> None:
    """三个太阳题材使用本轮日期和机位坐标生成带时区行动窗口。"""
    plan = build_spot_plan(_request(subject), [_record()], _astronomy_dispatch)
    assert plan.status == "ready"
    assert plan.selected_date == date(2026, 10, 3)
    assert plan.site is not None and plan.site.site_id == "spot-east"
    assert plan.site.point.coordinate_system == "WGS84"
    assert plan.arrival_window is not None and plan.setup_window is not None
    assert plan.shoot_window is not None and plan.departure_window is not None
    assert plan.arrival_window.end <= plan.setup_window.start
    assert plan.setup_window.end <= plan.shoot_window.start
    assert plan.shoot_window.end <= plan.departure_window.start
    assert plan.shoot_window.start.utcoffset().total_seconds() == 8 * 3600
    assert {item.source for item in plan.evidence_refs} == {"favorite_spots", "sun_times"}
    assert {item.code for item in plan.risks} == {"weather_unverified", "travel_time_unverified"}


def test_weather_dependent_subject_never_claims_ready_without_forecast() -> None:
    """晚霞和火烧云有天文窗口也不能假称已判断天气条件。"""
    for subject in ("晚霞", "火烧云"):
        plan = build_spot_plan(_request(subject), [_record()], _astronomy_dispatch)
        assert plan.status == "needs_data"
        assert plan.shoot_window is not None
        assert plan.risks[0].code == "weather_unverified"


def test_direction_conflict_requires_sightline_confirmation() -> None:
    """机位主朝向与日落方位相反时不能给出 ready 计划。"""
    plan = build_spot_plan(_request(), [_record(direction=90)], _astronomy_dispatch)
    assert plan.status == "needs_data"
    assert plan.risks[0].code == "direction_conflict"


def test_unverified_coordinate_system_is_never_dispatched() -> None:
    """未知坐标系不能被当作 WGS84 输入天文计算。"""
    plan = build_spot_plan(
        _request(),
        [_record(state="unknown_system", system=None)],
        lambda *_: pytest.fail("未知坐标系不应调用天文工具"),
    )
    assert plan.status == "needs_data"
    assert plan.site is None
    assert plan.risks[0].code == "spot_coordinates_unusable"


def test_wrong_site_or_timezone_is_rejected_not_guessed() -> None:
    """稳定 ID 不存在或请求时区与档案不一致时拒绝生成。"""
    with pytest.raises(SpotPlanInputError, match="没有该收藏机位"):
        build_spot_plan(_request(spot_id="other"), [_record()], _astronomy_dispatch)
    wrong_timezone = _request().model_copy(update={"tz_offset": "+09:00"})
    with pytest.raises(SpotPlanInputError, match="时区"):
        build_spot_plan(wrong_timezone, [_record()], _astronomy_dispatch)


def test_stale_tool_date_downgrades_instead_of_showing_wrong_timeline() -> None:
    """即使工具返回完整窗口，日期不匹配也不能混入本轮结果。"""

    def stale(name: str, raw_args: str) -> str:
        """模拟一份过期的天文结果。"""
        data = json.loads(_astronomy_dispatch(name, raw_args))
        data["日期"] = "2026-10-02"
        return json.dumps(data, ensure_ascii=False)

    plan = build_spot_plan(_request(), [_record()], stale)
    assert plan.status == "needs_data"
    assert plan.risks[0].code == "astronomy_mismatch"
    assert plan.shoot_window is None


class _UnusedClient:
    """结构化计划不得触发模型调用。"""

    def chat(self, *args: object, **kwargs: object) -> None:
        """任何 LLM 调用都是测试失败。"""
        pytest.fail("结构化计划不应调用 LLM")

    def queue_position(self) -> int:
        """满足应用装配的排队端口。"""
        return 0


@pytest.mark.asyncio
async def test_spot_plan_api_uses_current_user_id_and_no_store(tmp_path: Path) -> None:
    """生产 API 只解析当前用户的机位 ID，不让相同名称或其他用户 ID 串读。"""
    memory = MemoryManager(tmp_path)
    first = RequestContext(user_id="user-a")
    other = RequestContext(user_id="user-b")
    spot_input = FavoriteSpotInput.model_validate(
        {
            "name": "东滩",
            "latitude": 31.52,
            "longitude": 121.95,
            "coordinate_system": "WGS84",
            "tz_offset": "+08:00",
            "source": "合成测试机位",
        }
    )
    saved = memory.create_favorite_spot(first, spot_input)
    memory.create_favorite_spot(other, spot_input)
    calls: list[str] = []

    def dispatch(name: str, raw_args: str) -> str:
        """记录生产端点选择的数据源，不触网。"""
        calls.append(name)
        return _astronomy_dispatch(name, raw_args)

    settings = Settings(_env_file=None, data_dir=str(tmp_path), reason_thinking=False)
    services = build_application(settings, client=_UnusedClient(), memory=memory, dispatch=dispatch)
    app = create_app(ApiDeps(services=services, sessions=SessionManager(tmp_path)))
    payload = _request(spot_id=saved.id).model_dump(mode="json")
    try:
        app.dependency_overrides[get_request_context] = lambda: first
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/spot-plans", json=payload)
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "private, no-store"
            assert response.json()["site"]["site_id"] == saved.id
            assert response.json()["status"] == "ready"
            assert calls == ["sun_times"]
            app.dependency_overrides[get_request_context] = lambda: other
            denied = await client.post("/api/spot-plans", json=payload)
            assert denied.status_code == 422
            assert calls == ["sun_times"]
    finally:
        services.close()


def test_spot_plan_is_exported_to_endpoint_openapi() -> None:
    """前端必须从实际端点取得 PlanningRequest/SpotPlan 类型。"""
    schema = create_app().openapi()
    operation = schema["paths"]["/api/spot-plans"]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/PlanningRequest"
    )
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/SpotPlan"
    )
