"""R0-3 生产 API 验收：ReAct 用户记忆隔离、知识命中与来源 trace。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from httpx import ASGITransport, AsyncClient

from lighttrail.api.app import ApiDeps, create_app
from lighttrail.api.routes import get_request_context
from lighttrail.api.session import SessionManager
from lighttrail.composition import build_application
from lighttrail.config import Settings
from lighttrail.contracts.context import RequestContext


class _ScriptClient:
    """离线脚本客户端，保留生产模型桥收到的提示词。"""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def acall(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        """按顺序返回模型响应并记录请求。"""
        self.calls.append({"messages": messages, **kwargs})
        return self.responses.pop(0)

    def queue_position(self) -> int:
        """离线模型没有排队。"""
        return 0


def _tool_call(query: str) -> dict[str, Any]:
    """构造一次由 ReAct 触发的历史事件检索。"""
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "memory-call",
                "type": "function",
                "function": {
                    "name": "search_memory",
                    "arguments": json.dumps({"query": query}, ensure_ascii=False),
                },
            }
        ],
    }


def _events(body: str) -> list[dict[str, Any]]:
    """解析一次完整 SSE 响应。"""
    return [json.loads(line[5:].strip()) for line in body.splitlines() if line.startswith("data:")]


async def _chat(app: Any, user_id: str, message: str) -> list[dict[str, Any]]:
    """在测试依赖中模拟入口已验证的用户身份。"""
    app.dependency_overrides[get_request_context] = lambda: RequestContext(user_id=user_id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/chat", json={"message": message})
    assert response.status_code == 200
    events = _events(response.text)
    assert events[-1]["type"] == "done"
    assert not any(event["type"] == "error" for event in events)
    return events


async def test_react_memory_reads_only_current_user_and_no_legacy_db(tmp_path: Path) -> None:
    """双用户的注入和 search_memory 互不串读，也不新建旧根目录事件库。"""
    fake = _ScriptClient(
        [
            _tool_call(""),
            {"role": "assistant", "content": "已查看历史记录。"},
            _tool_call(""),
            {"role": "assistant", "content": "已查看历史记录。"},
        ]
    )
    services = build_application(
        Settings(_env_file=None, api_key="offline", data_dir=str(tmp_path), reason_thinking=False),
        client=fake,
    )
    north = RequestContext(user_id="north")
    south = RequestContext(user_id="south")
    services.memory.update_profile(north, {"camera_body": "北方相机"})
    services.memory.update_profile(south, {"camera_body": "南方相机"})
    services.memory.add_event(
        north,
        "2026-09-01T18:00:00+08:00",
        "北方机位",
        subject_type="火烧云",
        summary="北方独有历史",
    )
    services.memory.add_event(
        south,
        "2026-09-02T18:00:00+08:00",
        "南方机位",
        subject_type="火烧云",
        summary="南方独有历史",
    )
    app = create_app(ApiDeps(services, SessionManager(tmp_path)))
    try:
        north_events = await _chat(app, "north", "以前拍火烧云的经历")
        south_events = await _chat(app, "south", "以前拍火烧云的经历")
        north_prompt = fake.calls[0]["messages"][0]["content"]
        south_prompt = fake.calls[2]["messages"][0]["content"]
        assert "北方独有历史" in north_prompt and "南方独有历史" not in north_prompt
        assert "南方独有历史" in south_prompt and "北方独有历史" not in south_prompt

        for user_id, events, own, other in (
            ("north", north_events, "北方独有历史", "南方独有历史"),
            ("south", south_events, "南方独有历史", "北方独有历史"),
        ):
            memory_result = next(
                event
                for event in events
                if event["type"] == "tool_result" and event["name"] == "search_memory"
            )
            assert own in json.dumps(memory_result["data"], ensure_ascii=False)
            assert other not in json.dumps(memory_result["data"], ensure_ascii=False)
            assert memory_result["data_source"] == f"用户事件记忆：{user_id}"
            memory_step = next(
                event
                for event in events
                if event["type"] == "step" and event["name"] == "记忆_注入"
            )
            assert f"users/{user_id}/memory/" in memory_step["output_summary"]
            assert "profile,events" in memory_step["output_summary"]
            assert (tmp_path / "users" / user_id / "memory" / "events.db").exists()
        assert not (tmp_path / "events.db").exists()
        assert not (tmp_path / "profile.json").exists()
    finally:
        services.close()


async def test_global_knowledge_hits_both_users_and_trace_names_source(tmp_path: Path) -> None:
    """同一只读知识索引在两个请求中命中，trace 写出全局来源。"""
    fake = _ScriptClient(
        [
            {"role": "assistant", "content": "卷云信息已获取。"},
            {"role": "assistant", "content": "卷云信息已获取。"},
        ]
    )
    services = build_application(
        Settings(_env_file=None, api_key="offline", data_dir=str(tmp_path), reason_thinking=False),
        client=fake,
    )
    app = create_app(ApiDeps(services, SessionManager(tmp_path)))
    try:
        for index, user_id in enumerate(("north", "south")):
            events = await _chat(app, user_id, "卷云冰晶")
            prompt = fake.calls[index]["messages"][0]["content"]
            assert "卷云冰晶对长波散射敏感" in prompt
            assert "来源：architecture-v4-proposal.md" in prompt
            knowledge_step = next(
                event
                for event in events
                if event["type"] == "step" and event["name"] == "知识_命中"
            )
            assert "全局只读知识" in knowledge_step["output_summary"]
            assert "architecture-v4-proposal.md" in knowledge_step["output_summary"]
        assert (tmp_path / "knowledge" / "knowledge.db").exists()
        assert not (tmp_path / "users" / "north" / "knowledge").exists()
        assert not (tmp_path / "users" / "south" / "knowledge").exists()
    finally:
        services.close()
