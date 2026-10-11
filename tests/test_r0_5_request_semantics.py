"""R0-5 日期、地点与题材语义的固定时钟及生产端点回归。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from evals.fake_data import FakeDispatcher
from lighttrail.api.app import ApiDeps, create_app
from lighttrail.api.routes import get_request_context
from lighttrail.api.session import SessionManager
from lighttrail.application.router import RuleIntentRouter
from lighttrail.composition import build_application
from lighttrail.config import Settings
from lighttrail.contracts.context import RequestContext
from lighttrail.contracts.models import DecisionCard, Intent, LocationSuggestion
from lighttrail.orchestrator.pipelines import DateResolutionError, _align_card, _target_date

_BASE_TIME = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
_CARD = {
    "conclusion": "按照目标机位和日期观察，再决定是否出发。",
    "evidence": [],
    "confidence": "medium",
    "verdict": "go",
    "locations": [{"name": "西岸", "reason": "视线开阔"}],
}


class _FakeClient:
    """只返回离线决策卡，保留生产装配的模型调用路径。"""

    def __init__(self, location: str = "西岸") -> None:
        self.card = {**_CARD, "locations": [{"name": location, "reason": "测试机位"}]}
        self.calls: list[dict[str, Any]] = []

    async def acall(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        """返回可校验的决策卡。"""
        self.calls.append({"messages": messages, **kwargs})
        return {"role": "assistant", "content": json.dumps(self.card, ensure_ascii=False)}

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        """同步桥兼容接口，测试请求不走照片工具。"""
        return {"role": "assistant", "content": json.dumps(self.card, ensure_ascii=False)}

    def queue_position(self) -> int:
        """离线客户端没有排队。"""
        return 0


def _events(body: str) -> list[dict[str, Any]]:
    """解析已完成的 SSE 响应。"""
    return [json.loads(line[5:].strip()) for line in body.splitlines() if line.startswith("data:")]


def _request(tz_offset: str = "+08:00") -> RequestContext:
    """构造带固定 UTC 基准时间的请求上下文。"""
    return RequestContext(user_id="north", tz_offset=tz_offset, requested_at=_BASE_TIME)


@pytest.mark.parametrize(
    ("hint", "expected"),
    [
        ("今晚", "2026-09-26"),
        ("明天傍晚", "2026-09-27"),
        ("后天", "2026-09-28"),
        ("这周末", "2026-09-26"),
        ("周日", "2026-09-27"),
        ("下周六", "2026-10-03"),
        ("下周末", "2026-10-03"),
        ("2026-10-01", "2026-10-01"),
    ],
)
def test_target_date_uses_request_clock(hint: str, expected: str) -> None:
    """相对日、跨周周末和 ISO 日期使用同一固定请求时钟。"""
    assert _target_date(hint, _request()) == expected


def test_target_date_respects_timezone_and_rejects_invalid_date() -> None:
    """同一 UTC 时刻在不同请求时区可落在不同日期；非法日期须追问。"""
    assert _target_date("今晚", _request("-05:00")) == "2026-09-25"
    assert _target_date("周六", _request("-05:00")) == "2026-09-26"
    with pytest.raises(DateResolutionError):
        _target_date("2026-02-30", _request())
    with pytest.raises(DateResolutionError):
        _target_date(
            "本周六", RequestContext(requested_at=datetime(2026, 9, 27, tzinfo=timezone.utc))
        )


def test_rule_intent_keeps_explicit_location_and_iso_date() -> None:
    """规则命中时不得丢掉用户明确给出的机位和 ISO 日期。"""
    intent = RuleIntentRouter().route("2026-10-03去西岸拍火烧云")
    assert intent is not None
    assert (intent.subject_type, intent.location, intent.time_hint) == (
        "火烧云",
        "西岸",
        "2026-10-03",
    )
    named_for_other_subject = RuleIntentRouter().route("2026-10-03去星空台拍日落")
    assert named_for_other_subject is not None
    assert named_for_other_subject.subject_type == "日落"
    assert named_for_other_subject.location == "星空台"
    grounded = RuleIntentRouter().ground(
        Intent(subject_type="银河", location="错误地点", time_hint="明天"),
        "2026-10-03去星空台拍日落",
    )
    assert (grounded.subject_type, grounded.location, grounded.time_hint) == (
        "日落",
        "星空台",
        "2026-10-03",
    )


def test_card_rejects_conflicting_date_and_place_in_conclusion() -> None:
    """即使结构化地点正确，自由文本中的错误日期或机位也不能直接出卡。"""
    site = {"名称": "西岸"}
    card = DecisionCard(
        conclusion="2026-10-04 去东岸拍火烧云，建议直接出发。",
        evidence=[],
        confidence="medium",
        verdict="go",
        locations=[LocationSuggestion(name="西岸", reason="测试")],
    )
    aligned = _align_card(
        card,
        Intent(subject_type="火烧云"),
        site,
        "2026-10-03",
        _request(),
        [site, {"名称": "东岸"}],
    )
    assert aligned.degraded == "semantic_mismatch"
    assert aligned.verdict == "wait"
    assert "东岸" not in aligned.conclusion
    assert aligned.target_date == "2026-10-03"


async def test_production_decide_respects_place_date_and_current_subject(tmp_path: Path) -> None:
    """生产端点用显式西岸坐标，忽略其银河档案题材而采集火烧云数据。"""
    settings = Settings(
        _env_file=None,
        api_key="offline",
        data_dir=str(tmp_path),
        model="tools-fake",
        model_reason="reason-fake",
        reason_thinking=False,
    )
    fake = _FakeClient()
    dispatch = FakeDispatcher()
    services = build_application(settings, client=fake, dispatch=dispatch)
    context = _request()
    services.memory.update_profile(
        context,
        {
            "favorite_spots": [
                {
                    "name": "东岸",
                    "latitude": 31.1,
                    "longitude": 121.4,
                    "coordinate_system": "WGS84",
                    "subject": "日落",
                },
                {
                    "name": "西岸",
                    "latitude": 30.9,
                    "longitude": 121.9,
                    "coordinate_system": "WGS84",
                    "subject": "银河",
                },
            ]
        },
    )
    app = create_app(ApiDeps(services, SessionManager(tmp_path)))
    app.dependency_overrides[get_request_context] = lambda: context
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://localhost"
        ) as client:
            response = await client.post(
                "/api/decide", json={"request": "别去东岸，2026-10-03去西岸拍火烧云值得冲吗"}
            )
            assert response.status_code == 200
            events = _events(response.text)
            assert not [event for event in events if event["type"] == "error"]
            card = next(event["card"] for event in events if event["type"] == "card")
            assert (card["target_date"], card["subject_type"]) == ("2026-10-03", "火烧云")
            assert [site["name"] for site in card["locations"]] == ["西岸"]
            assert card["degraded"] == "needs_data"  # 固定请求时钟超出默认合成预报覆盖
            assert [name for name, _ in dispatch.calls if name == "moon_phase"] == []
            sun_args = json.loads(
                next(args for name, args in dispatch.calls if name == "sun_times")
            )
            assert sun_args["date"] == "2026-10-03"
            assert sun_args["tz_offset"] == "+08:00"
            assert (sun_args["latitude"], sun_args["longitude"]) == (30.9, 121.9)
            match_args = json.loads(
                next(args for name, args in dispatch.calls if name == "match_sites")
            )
            assert {site["题材"] for site in match_args["sites"]} == {"日落"}
            assert match_args["date"] == "2026-10-03"
            assert all(site["题材"] != "银河" for site in match_args["sites"])

            before = len(dispatch.calls)
            response = await client.post("/api/decide", json={"request": "周日想去拍银河"})
            card = next(
                event["card"] for event in _events(response.text) if event["type"] == "card"
            )
            assert (card["target_date"], card["subject_type"]) == ("2026-09-27", "银河")
            assert [site["name"] for site in card["locations"]] == ["西岸"]
            moon_args = json.loads(
                next(args for name, args in dispatch.calls[before:] if name == "moon_phase")
            )
            assert moon_args["date"] == "2026-09-27"

            before = len(dispatch.calls)
            for message, reason in (
                ("2026-10-03去杭州拍银河", "missing_requested_location"),
                ("今晚想去拍夜景", "ambiguous_location"),
            ):
                response = await client.post("/api/decide", json={"request": message})
                card = next(
                    event["card"] for event in _events(response.text) if event["type"] == "card"
                )
                assert card["degraded"] == reason
                assert card["verdict"] == "wait"
                assert len(dispatch.calls) == before

            services.memory.update_profile(
                context, {"favorite_spots": [{"name": "无坐标机位", "subject": "银河"}]}
            )
            response = await client.post("/api/decide", json={"request": "周日想去拍银河"})
            card = next(
                event["card"] for event in _events(response.text) if event["type"] == "card"
            )
            assert card["degraded"] == "missing_favorite_spot"
            assert len(dispatch.calls) == before
    finally:
        services.close()


async def test_conflicting_model_location_is_marked_and_corrected(tmp_path: Path) -> None:
    """模型把西岸写成东岸时，卡片固定请求目标并保守降级。"""
    settings = Settings(_env_file=None, api_key="offline", data_dir=str(tmp_path))
    fake = _FakeClient(location="东岸")
    fake.card["target_date"] = "2026-10-04"
    fake.card["subject_type"] = "银河"
    fake.card["window_start"] = "2026-10-04T17:00:00+08:00"
    fake.card["window_end"] = "2026-10-04T18:00:00+08:00"
    services = build_application(settings, client=fake, dispatch=FakeDispatcher())
    context = _request()
    services.memory.update_profile(
        context,
        {
            "favorite_spots": [
                {"name": "西岸", "latitude": 30.9, "longitude": 121.9, "coordinate_system": "WGS84"}
            ]
        },
    )
    app = create_app(ApiDeps(services, SessionManager(tmp_path)))
    app.dependency_overrides[get_request_context] = lambda: context
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://localhost"
        ) as client:
            response = await client.post(
                "/api/decide", json={"request": "2026-10-03去西岸拍火烧云值得冲吗"}
            )
        card = next(event["card"] for event in _events(response.text) if event["type"] == "card")
        assert card["degraded"] == "semantic_mismatch"
        assert card["verdict"] == "wait"
        assert card["confidence"] == "low"
        assert card["target_date"] == "2026-10-03"
        assert card["subject_type"] == "火烧云"
        assert [site["name"] for site in card["locations"]] == ["西岸"]
        assert card["window_start"] == card["window_end"] == ""
        assert "东岸" not in card["conclusion"]
    finally:
        services.close()
