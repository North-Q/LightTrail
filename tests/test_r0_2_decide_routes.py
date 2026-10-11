"""R0-2 生产端点验收：五种决策路由与配置护栏均经过统一装配。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from evals.fake_data import FakeDispatcher
from lighttrail.api.app import ApiDeps, create_app
from lighttrail.api.session import SessionManager
from lighttrail.composition import ApplicationServices, build_application
from lighttrail.config import Settings
from lighttrail.contracts.context import RequestContext
from lighttrail.infra.quota import Pricing, QuotaLedger

_CARD = json.dumps(
    {"conclusion": "去测试机位观察云层", "evidence": [], "confidence": "medium", "verdict": "wait"},
    ensure_ascii=False,
)
_PLAN = json.dumps(
    {"steps": [{"tool": "get_current_time", "args": {}, "purpose": "确认拍摄时刻"}]},
    ensure_ascii=False,
)


class _ScriptClient:
    """按顺序响应的离线模型，记录生产桥实际选择的模型。"""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def acall(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        """响应一次模型调用并记录其能力路由结果。"""
        self.calls.append({"messages": messages, **kwargs})
        return {"role": "assistant", "content": self.responses.pop(0)}

    def queue_position(self) -> int:
        """离线客户端没有排队。"""
        return 0


def _setup(
    tmp_path: Path,
    responses: list[str],
    *,
    quota: QuotaLedger | None = None,
    pipeline_max_steps: int = 12,
) -> tuple[Any, ApplicationServices, _ScriptClient]:
    """建立真实 Web 装配与独立请求目录。"""
    settings = Settings(
        _env_file=None,
        api_key="offline",
        data_dir=str(tmp_path),
        model="tools-fake",
        model_reason="reason-fake",
        reason_thinking=False,
        pipeline_max_steps=pipeline_max_steps,
    )
    fake = _ScriptClient(responses)
    services = build_application(settings, client=fake, quota=quota, dispatch=FakeDispatcher())
    services.memory.update_profile(
        RequestContext(),
        {
            "favorite_spots": [
                {
                    "name": "测试机位",
                    "latitude": 31.2,
                    "longitude": 121.5,
                    "coordinate_system": "WGS84",
                }
            ]
        },
    )
    return create_app(ApiDeps(services, SessionManager(tmp_path))), services, fake


def _events(body: str) -> list[dict[str, Any]]:
    """解析已完成的 SSE 响应。"""
    return [json.loads(line[5:].strip()) for line in body.splitlines() if line.startswith("data:")]


async def _decide(app: Any, request: str) -> list[dict[str, Any]]:
    """通过真实 ASGI 端点发起一次决策。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        response = await client.post("/api/decide", json={"request": request})
    assert response.status_code == 200
    events = _events(response.text)
    assert events[-1]["type"] == "done"
    assert not any(event["type"] == "error" for event in events)
    return events


def _steps(events: list[dict[str, Any]]) -> dict[str, str]:
    """按名称索引可审计路由与降级原因。"""
    return {event["name"]: event["output_summary"] for event in events if event["type"] == "step"}


async def test_rule_route_uses_reason_model(tmp_path: Path) -> None:
    """固定题材命中规则，采集完成后只调用深推理模型。"""
    app, services, fake = _setup(tmp_path, [_CARD])
    try:
        events = await _decide(app, "今晚火烧云值得冲吗")
        assert next(event for event in events if event["type"] == "card")["card"]["conclusion"]
        assert "live/火烧云" in _steps(events)["意图路由_规则"]
        assert [call["model"] for call in fake.calls] == ["reason-fake"]
    finally:
        services.close()


async def test_compound_route_executes_controlled_planner(tmp_path: Path) -> None:
    """复合请求使用受控计划，并分别调用工具模型和深推理模型。"""
    app, services, fake = _setup(tmp_path, [_PLAN, _CARD])
    try:
        events = await _decide(app, "做一个跨题材复合任务拍摄计划")
        steps = _steps(events)
        assert "复合任务关键词" in steps["意图路由_Planner"]
        assert "1 步" in steps["规划_产出"]
        assert "规划_执行" in steps
        assert any(event["type"] == "card" for event in events)
        assert [call["model"] for call in fake.calls] == ["tools-fake", "reason-fake"]
    finally:
        services.close()


async def test_failed_pipeline_falls_back_to_react(tmp_path: Path) -> None:
    """无效决策卡触发 ReAct，trace 说明失败原因且不伪造卡片。"""
    app, services, fake = _setup(tmp_path, ["非 JSON"] * 3 + ["先看云图再决定。"])
    try:
        events = await _decide(app, "今晚火烧云值得冲吗")
        assert (
            next(event["card"] for event in events if event["type"] == "card")["verdict"] == "wait"
        )
        assert not any(event["type"] == "token" for event in events)
        assert len(fake.calls) == 3
        assert fake.calls[-1]["model"] == "reason-fake"
    finally:
        services.close()


async def test_high_water_returns_conservative_card_without_model(tmp_path: Path) -> None:
    """真实账本达告警水位后，统一入口停止深推理并留下原因。"""
    quota = QuotaLedger(
        pricing={"tools-fake": Pricing(1_000_000, 0)},
        windows=(("hours", 3600.0, 10.0), ("daily", 86400.0, 100.0)),
        warn_threshold=0.8,
    )
    quota.record("tools-fake", 9, 0)
    app, services, fake = _setup(tmp_path, [], quota=quota)
    try:
        events = await _decide(app, "今晚火烧云值得冲吗")
        assert (
            next(event for event in events if event["type"] == "card")["card"]["degraded"]
            == "quota_high_water"
        )
        assert "90%" in _steps(events)["配额_降级"]
        assert fake.calls == []
    finally:
        services.close()


@pytest.mark.parametrize(
    ("route", "user_request"),
    [
        ("fixed_pipeline", "今晚火烧云值得冲吗"),
        ("controlled_planner", "做一个跨题材复合任务拍摄计划"),
        ("react_fallback_candidate", "今晚火烧云值得冲吗"),
    ],
)
@pytest.mark.parametrize(
    ("quota_state", "spent", "expected"),
    [
        ("rejected", 11, "quota_exhausted"),
        ("high_water", 9, "quota_high_water"),
    ],
)
async def test_quota_guard_is_uniform_before_all_decision_paths(
    tmp_path: Path,
    route: str,
    user_request: str,
    quota_state: str,
    spent: int,
    expected: str,
) -> None:
    """固定管线、Planner 与可回落请求共享同一入口的配额判断。"""
    quota = QuotaLedger(
        pricing={"tools-fake": Pricing(1_000_000, 0)},
        windows=(("hours", 3600.0, 10.0), ("daily", 86400.0, 100.0)),
        warn_threshold=0.8,
    )
    quota.record("tools-fake", spent, 0)
    responses = ["非 JSON"] * 3 if route == "react_fallback_candidate" else []
    app, services, fake = _setup(tmp_path, responses, quota=quota)
    try:
        events = await _decide(app, user_request)
        card = next(event["card"] for event in events if event["type"] == "card")
        steps = _steps(events)
        assert card["degraded"] == expected
        assert len([name for name in steps if name.startswith("配额_")]) == 1
        assert not any(name.startswith(("管线_", "意图路由_Planner", "管线降级")) for name in steps)
        assert fake.calls == []
    finally:
        services.close()


async def test_react_fallback_does_not_repeat_plan_quota_check(tmp_path: Path) -> None:
    """计划执行失败回落 ReAct 时不重复执行已完成的入口配额判定。"""
    quota = QuotaLedger(
        pricing={"tools-fake": Pricing(1_000_000, 0), "reason-fake": Pricing(1_000_000, 0)},
        windows=(("hours", 3600.0, 10_000.0),),
    )
    original_check = quota.check
    checks = 0

    def counted_check(estimate_credits: float = 0.0) -> bool:
        nonlocal checks
        checks += 1
        return original_check(estimate_credits)

    quota.check = counted_check  # type: ignore[method-assign]
    app, services, fake = _setup(tmp_path, ["非 JSON"] * 3 + ["请先查看云图。"], quota=quota)
    try:
        events = await _decide(app, "今晚火烧云值得冲吗")
        assert (
            next(event["card"] for event in events if event["type"] == "card")["verdict"] == "wait"
        )
        assert not any(event["type"] == "token" for event in events)
        assert checks == 1
        assert fake.calls[-1]["model"] == "reason-fake"
    finally:
        services.close()


async def test_unsupported_request_states_capability_boundary(tmp_path: Path) -> None:
    """超工具集请求只给文本边界，不调用模型或生成虚假卡片。"""
    app, services, fake = _setup(tmp_path, [])
    try:
        events = await _decide(app, "帮我订机票去拍星空")
        assert "不可代办" in _steps(events)["意图路由_超工具集"]
        assert any(event["type"] == "token" and "暂不支持" in event["content"] for event in events)
        assert not any(event["type"] == "card" for event in events)
        assert fake.calls == []
    finally:
        services.close()


async def test_pipeline_step_limit_is_injected_and_traced(tmp_path: Path) -> None:
    """配置步数不足时在采集前转 ReAct，trace 写明上限。"""
    app, services, fake = _setup(tmp_path, ["请缩小规划范围。"], pipeline_max_steps=1)
    try:
        events = await _decide(app, "今晚火烧云值得冲吗")
        assert "上限 1 步" in _steps(events)["管线_步数超限"]
        assert (
            next(event["card"] for event in events if event["type"] == "card")["degraded"]
            == "decision_failed"
        )
        assert not any(event["type"] == "tool_call" for event in events)
        assert fake.calls == []
    finally:
        services.close()
