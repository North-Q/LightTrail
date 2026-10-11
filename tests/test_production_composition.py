"""R0-1 生产装配集成测试：以同一 Fake 驱动 Web、CLI 和评估入口。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from httpx import ASGITransport, AsyncClient

from evals.fake_data import FakeDispatcher
from evals.runner import _run_pipeline_case
from lighttrail.api.app import ApiDeps, create_app
from lighttrail.api.routes import get_request_context
from lighttrail.api.session import SessionManager
from lighttrail.cli import main as cli_main
from lighttrail.composition import build_application
from lighttrail.config import Settings
from lighttrail.contracts.context import RequestContext
from lighttrail.infra.quota import QuotaLedger

_REQUEST = "今晚火烧云值得冲吗"
_CARD = {
    "conclusion": "今晚建议先观察云层，再决定是否出发。",
    "evidence": [
        {"tool": "sunset_glow_score", "field": "评分", "confidence": "medium", "note": "Fake 数据"}
    ],
    "confidence": "medium",
    "verdict": "wait",
    "locations": [{"name": "测试机位", "reason": "档案中已有坐标"}],
}


class _FakeClient:
    """复用同一脚本与账本的离线客户端，记录每次模型选择和提示词。"""

    def __init__(self) -> None:
        self.quota = QuotaLedger()
        self.calls: list[dict[str, Any]] = []

    async def acall(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        """记录异步调用并返回固定有效卡片。"""
        self.calls.append({"messages": messages, **kwargs})
        return {"role": "assistant", "content": json.dumps(_CARD, ensure_ascii=False)}

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        """同步照片工具若被调用，同样使用固定响应。"""
        self.calls.append({"messages": messages, **kwargs})
        return {"role": "assistant", "content": json.dumps(_CARD, ensure_ascii=False)}

    def queue_position(self) -> int:
        """离线客户端无排队。"""
        return 0


def _events(body: str) -> list[dict[str, Any]]:
    """从完整 SSE 响应取结构化事件负载。"""
    return [json.loads(line[5:].strip()) for line in body.splitlines() if line.startswith("data:")]


async def test_web_cli_eval_share_production_scope(tmp_path: Path, capsys: Any) -> None:
    """三入口同请求共用路由、配额、知识与用户目录，得到等价卡片。"""
    settings = Settings(
        _env_file=None,
        api_key="offline",
        data_dir=str(tmp_path),
        model="tools-fake",
        model_reason="reason-fake",
        reason_thinking=False,
        plan_max_steps=5,
        react_max_rounds=3,
    )
    fake = _FakeClient()
    services = build_application(settings, client=fake, dispatch=FakeDispatcher())
    context = RequestContext(user_id="north")
    services.memory.update_profile(
        context,
        {
            "camera_body": "松下 S5M2",
            "favorite_spots": [
                {
                    "name": "测试机位",
                    "latitude": 31.2,
                    "longitude": 121.5,
                    "coordinate_system": "WGS84",
                }
            ],
        },
    )
    app = create_app(ApiDeps(services, SessionManager(tmp_path)))
    app.dependency_overrides[get_request_context] = lambda: context
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://localhost"
        ) as client:
            response = await client.post("/api/decide", json={"request": _REQUEST})
        assert response.status_code == 200
        card = next(event["card"] for event in _events(response.text) if event["type"] == "card")

        cli_status = await asyncio.to_thread(
            cli_main, ["--pipeline", _REQUEST], services=services, request_context=context
        )
        assert cli_status == 0
        assert card["conclusion"] in capsys.readouterr().out

        case = {
            "id": "same-input",
            "group": "same",
            "request": _REQUEST,
            "expect": {"allow_degraded": True},
            "request_context": {
                "user_id": context.user_id,
                "requested_at": context.requested_at.isoformat(),
                "tz_offset": context.tz_offset,
            },
        }
        cassette = {"same": {"request_context": {"user_id": context.user_id}}}
        eval_card, tools, problems = await asyncio.to_thread(
            _run_pipeline_case, case, cassette, services=services
        )
        assert problems == []
        assert tools
        assert card == eval_card.model_dump(mode="json", exclude_none=True)

        scope = services.create_scope(context)
        assert scope.tool_context.request is context
        assert scope.tool_context.knowledge is services.knowledge
        assert scope.tool_context.memory is services.memory
        assert scope.orchestrator._env.router is services.router
        assert scope.orchestrator._env.quota is services.quota is fake.quota
        assert scope.orchestrator._planner._max_steps == settings.plan_max_steps
        assert scope.orchestrator._env.max_steps == settings.pipeline_max_steps
        assert scope.runtime._max_tool_rounds == settings.react_max_rounds
        assert {call["model"] for call in fake.calls} == {settings.model_reason}
        assert (tmp_path / "users" / "north" / "memory" / "profile.json").exists()
        assert not (tmp_path / "users" / "_local" / "memory" / "profile.json").exists()
    finally:
        services.close()


async def test_web_request_identity_isolated_and_knowledge_available(tmp_path: Path) -> None:
    """API worker 查询共享知识，并按请求身份分别注入档案和保护会话。"""
    settings = Settings(_env_file=None, api_key="offline", data_dir=str(tmp_path))
    fake = _FakeClient()
    services = build_application(settings, client=fake)
    north = RequestContext(user_id="north")
    south = RequestContext(user_id="south")
    services.memory.update_profile(north, {"camera_body": "北方相机"})
    services.memory.update_profile(south, {"camera_body": "南方相机"})
    app = create_app(ApiDeps(services, SessionManager(tmp_path)))
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://localhost"
        ) as client:
            app.dependency_overrides[get_request_context] = lambda: north
            first = await client.post("/api/chat", json={"message": "卷云冰晶"})
            assert first.status_code == 200
            first_id = _events(first.text)[-1]["session_id"]

            app.dependency_overrides[get_request_context] = lambda: south
            second = await client.post("/api/chat", json={"message": "卷云冰晶"})
            assert second.status_code == 200
            assert (await client.get(f"/api/sessions/{first_id}")).status_code == 404

        first_prompt = fake.calls[0]["messages"][0]["content"]
        second_prompt = fake.calls[1]["messages"][0]["content"]
        assert "北方相机" in first_prompt and "南方相机" not in first_prompt
        assert "南方相机" in second_prompt and "北方相机" not in second_prompt
        assert "卷云冰晶对长波散射敏感" in first_prompt
        assert "来源：" in first_prompt
        assert (tmp_path / "users" / "north" / "memory" / "profile.json").exists()
        assert (tmp_path / "users" / "south" / "memory" / "profile.json").exists()
    finally:
        services.close()
