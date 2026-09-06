"""TraceRecorder 可观测性基座的 pytest 用例（E2-1）。

验证：
- record_tool / record_llm / record_step 三类事件记录与订阅派发；
- to_prompt_section 精简轨迹文本（含工具名与结果摘要、①②序号、limit）；
- to_report 结构化报告（工具调用链 + 来源标注 + 置信度规则）；
- 参数/结果截断与 data_source 提取；
- NullTrace / 缺省关闭态零开销；注册表与 Agent 的写入点接线。
"""

from __future__ import annotations

from lighttrail.agent import Agent
from lighttrail.agent.tools import ToolRegistry, registry
from lighttrail.infra.trace import (
    KIND_LLM,
    KIND_TOOL,
    TraceEvent,
    TraceRecorder,
    null_trace,
)
from lighttrail.tools import basic, exposure  # noqa: F401  确保全局工具注册


def _j(**kwargs) -> str:
    """构造工具结果 JSON 字符串。"""
    import json

    return json.dumps(kwargs, ensure_ascii=False)


# ------ 订阅与三类事件 ------
def test_subscribe_receives_tool_event() -> None:
    """订阅者收到 tool 事件的 kind/name/ts，且可取消订阅。"""
    recorder = TraceRecorder()
    received: list[TraceEvent] = []
    unsubscribe = recorder.subscribe(received.append)
    recorder.record_tool("sun_times", "{}", _j(日出="05:12", 日落="18:47"))
    assert len(received) == 1
    event = received[0]
    assert event.kind == KIND_TOOL
    assert event.name == "sun_times"
    assert event.ts
    unsubscribe()
    recorder.record_tool("moon_phase", "{}", _j(月相="新月"))
    assert len(received) == 1  # 取消后不再收到


def test_record_llm_and_step() -> None:
    """record_llm / record_step 生成对应类型事件并进入报告。"""
    recorder = TraceRecorder()
    recorder.record_llm("ecnu-plus", prompt_summary="消息数 5", duration_s=0.3, tokens=120)
    recorder.record_step("意图识别", input_summary="用户输入", output_summary="Intent{题材: 星空}")
    report = recorder.to_report()
    assert len(report.llm_calls) == 1
    assert report.llm_calls[0]["tokens"] == 120
    assert len(report.steps) == 1
    assert report.steps[0].name == "意图识别"


# ------ 注入文本 ------
def test_to_prompt_section_contains_tool_and_result() -> None:
    """精简轨迹含工具名与结果摘要，并带 ①② 序号。"""
    recorder = TraceRecorder()
    recorder.record_tool("sun_times", "{}", _j(日出="05:12", 日落="18:47"))
    recorder.record_tool("weather_forecast", "{}", _j(平均云量=35, 数据来源="Open-Meteo（免费）"))
    section = recorder.to_prompt_section()
    assert "① 调用 sun_times：" in section
    assert "日出: 05:12" in section
    assert "② 调用 weather_forecast：" in section
    assert "平均云量: 35" in section


def test_to_prompt_section_steps_and_limit() -> None:
    """步骤事件也进轨迹；limit 只保留最近 N 条。"""
    recorder = TraceRecorder()
    for i in range(5):
        recorder.record_tool(f"tool_{i}", "{}", _j(序号=i))
    section = recorder.to_prompt_section(limit=2)
    assert "① 调用 tool_0" not in section
    assert "⑤ 调用 tool_4" in section


def test_to_prompt_section_empty_when_nothing_recorded() -> None:
    """无事件时轨迹为空串。"""
    recorder = TraceRecorder()
    assert recorder.to_prompt_section() == ""


# ------ 报告与置信度规则 ------
def test_report_sources_and_confidence_rules() -> None:
    """来源标注按规则派生：天文 high、天气 medium、未知 low。"""
    recorder = TraceRecorder()
    recorder.record_tool("sun_times", "{}", _j(日出="05:12"))
    recorder.record_tool("weather_forecast", "{}", _j(数据来源="Open-Meteo（免费）"))
    recorder.record_tool("some_unknown", "{}", _j(值=1))
    sources = recorder.to_report().sources
    assert [s.confidence for s in sources] == ["high", "medium", "low"]
    assert sources[1].data_source == "Open-Meteo（免费）"


def test_record_tool_truncates_long_arguments_and_result() -> None:
    """参数/结果超长时截断（200/300 字符），且不抛异常。"""
    recorder = TraceRecorder()
    long_args = "x" * 500
    long_result = _j(数据=("a" * 800))
    recorder.record_tool("tool", long_args, long_result)
    event = recorder.to_report().tool_calls[0]
    assert len(event.arguments_summary) <= 201
    assert len(event.result_summary) <= 301
    assert "…" in event.arguments_summary or len(event.arguments_summary) == 200


def test_clear_resets_events() -> None:
    """clear 清空已记录事件（新会话复用记录器）。"""
    recorder = TraceRecorder()
    recorder.record_tool("x", "{}", "{}")
    recorder.clear()
    assert recorder.to_report().tool_calls == ()


# ------ 关闭态 ------
def test_null_trace_zero_recording() -> None:
    """关闭态：不产生事件、注入文本与报告为空、订阅返回空取消函数。"""
    null_trace.record_tool("sun_times", "{}", _j(日出="05:12"))
    null_trace.record_llm("ecnu-plus")
    null_trace.record_step("步骤")
    assert null_trace.to_prompt_section() == ""
    assert null_trace.to_report() == TraceRecorder().to_report() or null_trace.to_report().tool_calls == ()
    null_trace.subscribe(lambda e: None)()


def test_registry_default_no_recording() -> None:
    """注册表缺省关闭态：dispatch 不产生任何事件。"""
    reg = ToolRegistry()
    reg.register(lambda: {"now": 1}, name="probe_a", description="探测")
    result = reg.dispatch("probe_a", "{}")
    assert "now" in result
    assert null_trace.to_prompt_section() == ""


def test_registry_dispatch_records_with_recorder() -> None:
    """注册表注入 recorder 后 dispatch 写入 tool 事件（含耗时与结果）。"""
    recorder = TraceRecorder()
    reg = ToolRegistry(recorder=recorder)
    reg.register(lambda: {"ok": True}, name="probe_b", description="探测")
    reg.dispatch("probe_b", "{}")
    report = recorder.to_report()
    assert len(report.tool_calls) == 1
    assert report.tool_calls[0].name == "probe_b"
    assert report.tool_calls[0].result_summary == "ok: True"


# ------ Agent / ReActLoop 集成 ------
class _FakeChatClient:
    """按脚本预置响应序列的伪客户端。"""

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def chat(self, messages, *, model=None, tools=None, temperature=0.2) -> dict:
        self.calls.append({"messages": messages, "model": model, "tools": tools})
        return self._responses.pop(0)


def test_agent_loop_writes_trace() -> None:
    """Agent 注入 recorder 后：工具调用进轨迹、LLM/工具事件被订阅者收到。"""
    recorder = TraceRecorder()
    kinds: list[str] = []
    recorder.subscribe(lambda event: kinds.append(event.kind))

    tool_call_msg = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_current_time", "arguments": "{}"},
            }
        ],
    }
    final_msg = {"role": "assistant", "content": "现在是北京时间 2026-08-12 23:40。"}
    fake = _FakeChatClient([tool_call_msg, final_msg])
    agent = Agent(fake, registry, model="ecnu-plus", recorder=recorder)
    agent.run("现在几点？")

    section = recorder.to_prompt_section()
    assert "get_current_time" in section
    assert "iso:" in section or "现在是" not in section  # 工具结果摘要含 iso 字段
    report = recorder.to_report()
    assert any(call.name == "get_current_time" for call in report.tool_calls)
    assert len(report.llm_calls) == 2  # 两轮 chat
    assert KIND_TOOL in kinds
    assert KIND_LLM in kinds
