"""FastAPI + SSE 路由（E7-3）的 pytest 用例。

验证（roadmap E7-3 验收）：
- /api/chat：SSE 流含 queued → tool_call/tool_result → token → done；
- /api/decide：管线步骤事件 + 末端 card + done；
- /api/photos/review：multipart 上传（限 jpg/png、≤10MB）→ card + done；
- /api/profile：GET/PUT 档案编辑闭环；
- /api/sessions/{id}：会话历史回放 + 404；
- /docs 与 OpenAPI 含全部端点。

全部用 FakeChatClient / Fake 数据源 / Fake 照片分析，不触网、不依赖 API Key。
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from lighttrail.api.app import ApiDeps, create_app
from lighttrail.api.session import SessionManager
from lighttrail.llm.client import LLMError
from lighttrail.memory import MemoryManager

# 工具触发注册（conftest 已导入，此处双保险）
from lighttrail.tools import (  # noqa: F401
    astronomy,
    basic,
    exposure,
    memory_tool,
    photo_analysis,
    site_match,
    weather,
)


class FakeChatClient:
    """按脚本预置响应序列的伪客户端（含 chat 与 queue_position）。"""

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def chat(self, messages, **kwargs) -> dict:
        self.calls.append({"messages": messages, **kwargs})
        return self._responses.pop(0)

    def queue_position(self) -> int:
        """伪客户端不真正排队，恒返回 0（供 queued 事件位置计算）。"""
        return 0


def _intent_json(subject: str, mode: str) -> str:
    return json.dumps({"subject_type": subject, "location": "", "time_hint": "今晚", "mode": mode}, ensure_ascii=False)


def _card_json(conclusion: str = "建议 18:10 到机位蹲守，风险中等可接受。") -> str:
    return json.dumps(
        {
            "conclusion": conclusion,
            "evidence": [{"tool": "sunset_glow_score", "field": "评分", "confidence": "medium", "note": "62 分"}],
            "confidence": "medium",
            "time_window": "18:00-18:40",
            "locations": [{"name": "崇明东滩", "reason": "朝西开阔"}],
            "params": [],
            "alternatives": ["南汇嘴"],
            "degraded": "",
        },
        ensure_ascii=False,
    )


def _tool_call_msg(name: str = "get_current_time", arguments: str = "{}") -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": name, "arguments": arguments}}
        ],
    }


def _fake_dispatch(name: str, args: str) -> str:
    """Fake 数据源（不触网）。"""
    data = {
        "moon_phase": {"月相名称": "新月", "照亮比例（%）": 2, "月光影响建议": "低"},
        "galaxy_visibility": {"可见窗口": [{"开始": "20:10", "结束": "23:50"}], "最高高度角": 55, "提示": "好"},
        "weather_forecast": {"每日预报": [{"日期": "2026-09-08", "平均云量（%）": 30}], "数据来源": "Open-Meteo（免费）"},
        "sun_times": {"日出": "05:42", "日落": "18:06"},
        "sunset_glow_score": {"评分（0-100）": 62, "等级": "中等（可看趋势再定）", "数据来源": "Open-Meteo（免费）"},
    }
    return json.dumps(data.get(name, {"error": f"未知工具 {name}"}), ensure_ascii=False)


def _fake_photo_analyze(image_path: str, focus: str) -> dict:
    """Fake 照片分析：返回结构含已识别 EXIF（复盘管线读取键）。"""
    return {
        "scene": "海边日落",
        "subject": "火烧云",
        "composition": "三分法",
        "assessment": "曝光准确，构图稳妥。",
        "prescription": "下次用 14mm f/8 ISO100 包围曝光。",
        "已识别EXIF": {"光圈": "f/8", "快门": "1/125", "ISO": 100},
    }


@pytest.fixture()
def data_dir() -> Path:
    """自建临时数据目录（档案/会话落盘，退出清理）。"""
    directory = Path(tempfile.mkdtemp(prefix="lt_api_"))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture()
def make_app(data_dir: Path):
    """按 Fake 依赖构造应用（closure 允许每个用例注入不同响应脚本）。"""

    def _build(responses: list[dict]) -> tuple[object, FakeChatClient]:
        fake = FakeChatClient(responses)
        deps = ApiDeps(
            client=fake,
            sessions=SessionManager(data_dir),
            memory=MemoryManager(data_dir),
            model="ecnu-plus",
            reason_thinking=False,
            dispatch=_fake_dispatch,
            photo_analyze=_fake_photo_analyze,
        )
        return create_app(deps), fake

    return _build


async def _collect_sse(client: AsyncClient, method: str, url: str, **kwargs) -> list[dict]:
    """读取 SSE 流并把帧解析为事件字典列表。"""
    events: list[dict] = []
    async with client.stream(method, url, **kwargs) as resp:
        assert resp.status_code == 200, resp.text
        current_type = ""
        data_lines: list[str] = []
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                current_type = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].strip())
            elif line == "" and current_type:
                events.append({"type": current_type, **json.loads("\n".join(data_lines))})
                current_type = ""
                data_lines = []
    return events


async def test_chat_sse_full_protocol(make_app) -> None:
    """/api/chat：queued → tool_call/tool_result → token → done。"""
    app, _ = make_app([_tool_call_msg(), {"role": "assistant", "content": "现在是北京时间 22:30。"}])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        events = await _collect_sse(client, "POST", "/api/chat", json={"message": "现在几点？"})

    types = [e["type"] for e in events]
    assert types[0] == "queued"
    assert "tool_call" in types and "tool_result" in types
    assert types[-1] == "done"
    token = next(e for e in events if e["type"] == "token")
    assert "22:30" in token["content"]
    tool_call = next(e for e in events if e["type"] == "tool_call")
    assert tool_call["name"] == "get_current_time"
    # 会话持久化：done 带 session_id，随后可按 id 取回历史
    session_id = events[-1]["session_id"]
    assert session_id
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/sessions/{session_id}")
    assert resp.status_code == 200
    body = resp.json()
    contents = [m.get("content") for m in body["session"]["history"]]
    assert "现在几点？" in contents


async def test_chat_error_event(make_app) -> None:
    """/api/chat：LLM 失败 → error 事件后 done（流不中断协议）。"""

    class _BrokenClient:
        def chat(self, messages, **kwargs) -> dict:
            raise LLMError("simulated failure")

        def queue_position(self) -> int:
            return 0

    app, _ = make_app([])
    app.state.deps.client = _BrokenClient()  # 覆盖为必败客户端
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        events = await _collect_sse(client, "POST", "/api/chat", json={"message": "hi"})
    types = [e["type"] for e in events]
    assert "error" in types
    assert types[-1] == "done"
    error = next(e for e in events if e["type"] == "error")
    assert "simulated failure" in error["message"]


async def test_decide_sse_card(make_app) -> None:
    """/api/decide：管线步骤事件 + 末端 card + done。"""
    app, _ = make_app(
        [
            {"role": "assistant", "content": _intent_json("火烧云", mode="live")},
            {"role": "assistant", "content": _card_json()},
        ]
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        events = await _collect_sse(client, "POST", "/api/decide", json={"request": "今晚火烧云值得冲吗"})

    types = [e["type"] for e in events]
    assert types[0] == "queued"
    assert "step" in types  # 管线步骤事件实时推送
    assert types[-1] == "done"
    card_event = next(e for e in events if e["type"] == "card")
    assert card_event["card"]["conclusion"] == "建议 18:10 到机位蹲守，风险中等可接受。"
    assert "step" in types


async def test_photos_review_sse(make_app) -> None:
    """/api/photos/review：multipart 上传 → card + done。"""
    app, _ = make_app([{"role": "assistant", "content": _card_json("复盘结论：构图可再精简前景。")}])
    transport = ASGITransport(app=app)
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        events = await _collect_sse(
            client,
            "POST",
            "/api/photos/review",
            files={"file": ("photo.png", png_bytes, "image/png")},
            data={"focus": "构图", "plan_reference": ""},
        )
    types = [e["type"] for e in events]
    assert types[-1] == "done"
    card_event = next(e for e in events if e["type"] == "card")
    assert "复盘结论" in card_event["card"]["conclusion"]


async def test_photos_review_rejects_bad_type(make_app) -> None:
    """照片类型校验：非 jpg/png → 400。"""
    app, _ = make_app([])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/photos/review",
            files={"file": ("x.gif", b"GIF89a", "image/gif")},
        )
    assert resp.status_code == 400


async def test_profile_get_put(make_app) -> None:
    """档案：GET 读默认空档；PUT 更新后 GET 读回。"""
    app, _ = make_app([])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        got = await client.get("/api/profile")
        assert got.status_code == 200
        assert got.json()["camera_body"] == ""

        put = await client.put(
            "/api/profile",
            json={"camera_body": "松下 S5M2", "lenses": ["24-105mm F4", "契卡 14mm"], "skill_level": "进阶"},
        )
        assert put.status_code == 200
        assert put.json()["camera_body"] == "松下 S5M2"

        got2 = await client.get("/api/profile")
        assert got2.json()["lenses"] == ["24-105mm F4", "契卡 14mm"]


async def test_sessions_unknown_404(make_app) -> None:
    """未知会话 → 404。"""
    app, _ = make_app([])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/sessions/no-such-session")
    assert resp.status_code == 404


async def test_openapi_contains_all_endpoints(make_app) -> None:
    """OpenAPI/文档：五个端点全部注册。"""
    app, _ = make_app([])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        spec = (await client.get("/openapi.json")).json()
        docs = await client.get("/docs")
    paths = spec["paths"]
    for path in ("/api/chat", "/api/decide", "/api/photos/review", "/api/profile", "/api/sessions/{session_id}"):
        assert path in paths
    assert docs.status_code == 200
