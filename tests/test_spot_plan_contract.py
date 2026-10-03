"""S1-1 单机位计划契约的输入、执行语义与 OpenAPI 回归测试。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from lighttrail.api.app import create_app
from lighttrail.contracts.spot_plan import PlanningRequest, SpotPlan

ROOT = Path(__file__).resolve().parents[1]


def _request() -> dict[str, object]:
    """构造具备明确日期和机位的最小请求。"""
    return {
        "subject_type": "银河",
        "target_date": "2026-10-03",
        "tz_offset": "+08:00",
        "spot": {"name": "东滩"},
    }


def _ready_plan() -> dict[str, object]:
    """构造包含四段行动窗口与证据的完整计划。"""
    return {
        "status": "ready",
        "request": _request(),
        "selected_date": "2026-10-03",
        "site": {
            "name": "东滩",
            "point": {"latitude": 31.52, "longitude": 121.95, "coordinate_system": "WGS84"},
            "source": "用户收藏机位",
        },
        "direction": {"bearing_deg": 135, "reason": "银河核心方位"},
        "arrival_window": {
            "start": "2026-10-03T19:00:00+08:00",
            "end": "2026-10-03T19:20:00+08:00",
        },
        "setup_window": {"start": "2026-10-03T19:20:00+08:00", "end": "2026-10-03T19:40:00+08:00"},
        "shoot_window": {"start": "2026-10-03T19:40:00+08:00", "end": "2026-10-03T20:30:00+08:00"},
        "departure_window": {
            "start": "2026-10-03T20:30:00+08:00",
            "end": "2026-10-03T21:00:00+08:00",
        },
        "focal_length_mm": 14,
        "gear": ["松下 S5M2", "14mm 定焦"],
        "parameters": [{"name": "光圈", "value": "f/2.8", "reason": "提高进光量"}],
        "risks": [{"code": "cloud", "severity": "medium", "detail": "云层可能遮挡"}],
        "evidence_refs": [{"source": "天文工具", "field": "银河方位"}],
    }


def test_planning_request_requires_unambiguous_date_and_location() -> None:
    """日期和地点各允许一个选项，不能猜测缺失语义。"""
    request = PlanningRequest.model_validate(_request())
    assert request.target_date == date(2026, 10, 3)
    assert request.spot is not None and request.spot.name == "东滩"

    for mutation in (
        {"target_date": None},
        {"date_range": {"start": "2026-10-03", "end": "2026-10-05"}},
        {"spot": None},
        {"spot": {"name": "   "}},
        {"subject_type": "   "},
        {
            "area": {
                "name": "崇明",
                "center": {"latitude": 31.5, "longitude": 121.9, "coordinate_system": "WGS84"},
                "radius_km": 10,
            }
        },
    ):
        with pytest.raises(ValidationError):
            PlanningRequest.model_validate({**_request(), **mutation})


def test_planning_request_accepts_ordered_range_and_rejects_reversed_range() -> None:
    """区域和日期范围是显式替代输入，倒置范围不得被接受。"""
    request = {
        **_request(),
        "target_date": None,
        "spot": None,
        "date_range": {"start": "2026-10-03", "end": "2026-10-05"},
        "area": {
            "name": "崇明",
            "center": {"latitude": 31.5, "longitude": 121.9, "coordinate_system": "WGS84"},
            "radius_km": 10,
        },
    }
    assert PlanningRequest.model_validate(request).date_range is not None
    request["date_range"] = {"start": "2026-10-05", "end": "2026-10-03"}
    with pytest.raises(ValidationError):
        PlanningRequest.model_validate(request)


@pytest.mark.parametrize("offset", ["+14:01", "+08:60", "08:00"])
def test_planning_request_rejects_invalid_timezone(offset: str) -> None:
    """非法时区偏移不得进入计划。"""
    with pytest.raises(ValidationError):
        PlanningRequest.model_validate({**_request(), "tz_offset": offset})


def test_ready_plan_requires_actionable_fields_and_chronology() -> None:
    """完整计划须能执行；缺字段和倒置时间线必须拒绝。"""
    plan = SpotPlan.model_validate(_ready_plan())
    assert plan.site is not None and plan.site.point.coordinate_system == "WGS84"
    assert plan.shoot_window is not None and plan.shoot_window.start.tzinfo is not None

    for mutation in (
        {"direction": None},
        {"evidence_refs": []},
        {"selected_date": "2026-10-04"},
        {"site": {**_ready_plan()["site"], "name": "外滩"}},
        {
            "arrival_window": {
                "start": "2026-10-03T11:00:00+00:00",
                "end": "2026-10-03T11:20:00+00:00",
            }
        },
        {
            "setup_window": {
                "start": "2026-10-03T19:15:00+08:00",
                "end": "2026-10-03T19:40:00+08:00",
            }
        },
        {"shoot_window": {"start": "2026-10-03T19:40:00", "end": "2026-10-03T20:30:00"}},
    ):
        with pytest.raises(ValidationError):
            SpotPlan.model_validate({**_ready_plan(), **mutation})


def test_needs_data_plan_can_express_missing_fields() -> None:
    """数据不足时保留结构化缺口，不伪造坐标或时间。"""
    plan = _ready_plan()
    plan.update(status="needs_data", site=None, direction=None, shoot_window=None, evidence_refs=[])
    assert SpotPlan.model_validate(plan).status == "needs_data"


def test_planning_contract_is_in_openapi_and_typescript() -> None:
    """API schema 和前端生成物均包含同一组结构化计划字段。"""
    schemas = create_app().openapi()["components"]["schemas"]
    request = schemas["PlanningRequest"]["properties"]
    plan = schemas["SpotPlan"]["properties"]
    assert {
        "subject_type",
        "target_date",
        "date_range",
        "tz_offset",
        "spot",
        "area",
    } <= request.keys()
    assert {
        "site",
        "direction",
        "arrival_window",
        "setup_window",
        "shoot_window",
        "departure_window",
        "focal_length_mm",
        "gear",
        "parameters",
        "risks",
        "evidence_refs",
    } <= plan.keys()
    generated = (ROOT / "frontend" / "src" / "api" / "generated.ts").read_text(encoding="utf-8")
    assert "PlanningRequest:" in generated and "SpotPlan:" in generated
