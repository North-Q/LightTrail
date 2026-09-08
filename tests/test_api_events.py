"""trace → SSE 事件桥（E7-4）的 pytest 用例。

验证（roadmap E7-4 验收）：
- 事件映射：step → SSE step；tool → tool_call + tool_result 两条；llm 不映射；
- TraceBridge 线程桥：worker 线程内的事件实时入队（call_soon_threadsafe），
  detach 后不再转发；
- 跑一次 decide 管线（走 SSE 路由）：step → tool_call → tool_result → card →
  done 全序列且顺序正确（「trace 即 UI」验收）。

全部用 Fake 依赖，不触网、不依赖 API Key。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from lighttrail.api.app import ApiDeps, create_app
from lighttrail.api.events import TraceBridge, map_trace_event, sse_text
from lighttrail.api.session import SessionManager
from lighttrail.infra.trace import TraceEvent, TraceRecorder
from lighttrail.memory import MemoryManager
from lighttrail.tools import (  # noqa: F401  触发注册
    astronomy,
    basic,
    exposure,
    memory_tool,
    photo_analysis,
    site_match,
    weather,
)


class FakeChatClient:
    """按脚本预置响应序列的伪客户端。"""

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def chat(self, messages, **kwargs) -> dict:
        self.calls.append({"messages": messages, **kwargs})
        return self._responses.pop(0)

    def queue_position(self) -> int:
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


@pytest.fixture()
def data_dir() -> Path:
    """自建临时数据目录（退出清理）。"""
    directory = Path(tempfile.mkdtemp(prefix="lt_events_"))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture()
def make_app(data_dir: Path):
    """按 Fake 依赖构造应用。"""

    def _build(responses: list[dict]) -> tuple[object, FakeChatClient]:
        fake = FakeChatClient(responses)
        deps = ApiDeps(
            client=fake,
            sessions=SessionManager(data_dir),
            memory=MemoryManager(data_dir),
            model="ecnu-plus",
            reason_thinking=False,
            dispatch=_fake_dispatch,
        )
        return create_app(deps), fake

    return _build


# ------ 事件映射单元测试 ------
def test_map_step_event() -> None:
    """step 事件 → SSE step（携带名称与输入/输出摘要）。"""
    event = TraceEvent(
        kind="step",
        name="采集_weather_forecast",
        payload={"输入摘要": '{"days": 3}', "输出摘要": ""},
        ts="2026-09-08T00:00:00+00:00",
    )
    mapped = map_trace_event(event, session_id="s1")
    assert mapped is not None and len(mapped) == 1
    assert mapped[0]["type"] == "step"
    assert mapped[0]["name"] == "采集_weather_forecast"
    assert mapped[0]["session_id"] == "s1"


def test_map_tool_event_splits_call_and_result() -> None:
    """tool 事件 → tool_call + tool_result 两条（结果带来源与置信度）。"""
    event = TraceEvent(
        kind="tool",
        name="sunset_glow_score",
        payload={
            "参数摘要": '{"date": "2026-09-08"}',
            "结果摘要": "评分: 62",
            "结果原文": '{"评分（0-100）": 62}',
            "数据来源": "Open-Meteo（免费）",
            "置信度": "medium",
            "来源字段": "评分",
            "耗时_ms": 12.5,
        },
        ts="2026-09-08T00:00:00+00:00",
    )
    mapped = map_trace_event(event, session_id="s2")
    assert mapped is not None and len(mapped) == 2
    call, result = mapped
    assert call["type"] == "tool_call"
    assert call["name"] == "sunset_glow_score"
    assert result["type"] == "tool_result"
    assert result["data_source"] == "Open-Meteo（免费）"
    assert result["confidence"] == "medium"
    assert result["field"] == "评分"
    assert result["elapsed_ms"] == 12.5


def test_map_llm_event_skipped() -> None:
    """llm 事件不映射（token 由端点按文本补发）。"""
    event = TraceEvent(kind="llm", name="ecnu-plus", payload={"tokens": 10}, ts="t")
    assert map_trace_event(event) is None


def test_sse_text_frame() -> None:
    """SSE 帧格式：event: type + data: JSON + 空行。"""
    frame = sse_text({"type": "done", "session_id": "s1", "text": "好"})
    assert frame.startswith("event: done\ndata: ")
    payload = json.loads(frame.split("\n")[1][len("data: ") :])
    assert payload["session_id"] == "s1"
    assert frame.endswith("\n\n")


# ------ TraceBridge 线程桥 ------
async def test_bridge_forwards_from_worker_thread_and_detach() -> None:
    """桥：worker 线程事件实时入队；detach 后停止转发。"""
    recorder = TraceRecorder()
    queue: asyncio.Queue[dict | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    bridge = TraceBridge(recorder)
    bridge.attach(queue, loop, session_id="s-bridge")

    def emit() -> None:
        recorder.record_step("意图理解", input_summary="hi", output_summary="")
        recorder.record_tool("sun_times", "{}", '{"日落":"18:06"}', elapsed_ms=3.0)

    await asyncio.to_thread(emit)  # 在 worker 线程记录，经桥转发
    first = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert first["type"] == "step"
    call = await asyncio.wait_for(queue.get(), timeout=1.0)
    result = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert (call["type"], result["type"]) == ("tool_call", "tool_result")
    assert result["session_id"] == "s-bridge"

    bridge.detach()
    await asyncio.to_thread(emit)  # detach 后不再转发
    await asyncio.sleep(0.05)
    assert queue.empty()


# ------ decide 管线全序列（「trace 即 UI」验收）------
async def test_decide_sse_sequence_order(make_app) -> None:
    """/api/decide：step → tool_call → tool_result → card → done 顺序正确。"""
    app, _ = make_app(
        [
            {"role": "assistant", "content": _intent_json("火烧云", mode="live")},
            {"role": "assistant", "content": _card_json()},
        ]
    )
    transport = ASGITransport(app=app)
    events: list[dict] = []
    async with (
        AsyncClient(transport=transport, base_url="http://test") as client,
        client.stream("POST", "/api/decide", json={"request": "今晚火烧云值得冲吗"}) as resp,
    ):
        assert resp.status_code == 200
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

    types = [e["type"] for e in events]
    assert types[0] == "queued"
    step_idx = types.index("step")
    tool_call_idx = types.index("tool_call")
    tool_result_idx = types.index("tool_result")
    card_idx = types.index("card")
    assert step_idx < tool_call_idx < tool_result_idx < card_idx
    assert types[-1] == "done"
    counts = {t: types.count(t) for t in ("step", "tool_call", "tool_result")}
    assert counts["tool_call"] >= 2  # live 管线 ≥ 2 个数据工具
    assert counts["tool_call"] == counts["tool_result"]  # call/result 成对
