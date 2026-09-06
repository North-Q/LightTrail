"""TraceRecorder 可观测性基座的 pytest 用例（E2-1 / E2-2）。

验证：
- record_tool / record_llm / record_step 三类事件记录与订阅派发；
- to_prompt_section 精简轨迹文本（工具名 + 结果摘要、①②序号、limit）；
- to_report 结构化报告（工具调用链 + 来源标注 tool/field/confidence）；
- 轨迹注入 prompt 第⑤层（第二轮 LLM 请求的 system 含轨迹文本）；
- run_with_trace 返回 (文本, 本轮 TraceReport)，报告按 cursor 切片；
- 参数/结果截断、data_source 提取、关闭态零开销、注册表/Agent 接线。
"""

from __future__ import annotations

import json

from lighttrail.agent import Agent
from lighttrail.agent.tools import ToolRegistry, registry
from lighttrail.infra.trace import (
    KIND_LLM,
    KIND_TOOL,
    TraceRecorder,
    null_trace,
)
from lighttrail.tools import basic, exposure  # noqa: F401  确保全局工具注册


def _j(**kwargs: object) -> str:
    """构造工具结果 JSON 字符串。"""
    return json.dumps(kwargs, ensure_ascii=False)


# ------ 订阅与三类事件 ------
def test_subscribe_receives_tool_event() -> None:
    """订阅者收到 tool 事件的 kind/name/ts，且可取消订阅。"""
    recorder = TraceRecorder()
    received: list = []
    unsubscribe = recorder.subscribe(received.append)
    recorder.record_tool("sun_times", "{}", _j(日出="05:12", 日落="18:47"))
    assert len(received) == 1
    event = received[0]
    assert event.kind == KIND_TOOL
    assert event.name == "sun_times"
    assert event.ts
    unsubscribe()
    recorder.record_tool("moon_phase", "{}", _j(月相="新月"))
    assert len(received) == 1


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
    """来源标注按规则表派生：天文 high、预报无条目降级 medium、未知 low。"""
    recorder = TraceRecorder()
    recorder.record_tool("sun_times", "{}", _j(日出="05:12"))
    recorder.record_tool("weather_forecast", "{}", _j(数据来源="Open-Meteo（免费）"))
    recorder.record_tool("some_unknown", "{}", _j(值=1))
    sources = recorder.to_report().sources
    assert [s.confidence for s in sources] == ["high", "medium", "low"]
    assert sources[1].data_source == "Open-Meteo（免费）"


def test_source_field_mapped_and_fallback() -> None:
    """来源字段：映射表命中的工具取主字段，未知工具取结果首个业务键。"""
    recorder = TraceRecorder()
    recorder.record_tool("sunset_glow_score", "{}", _j(评分=62, 等级="中等"))
    recorder.record_tool("custom_tool", "{}", _j(业务结果="x", 数据来源="外部"))
    sources = recorder.to_report().sources
    assert sources[0].field == "评分"
    assert sources[1].field == "业务结果"


def test_record_tool_truncates_long_arguments_and_result() -> None:
    """参数/结果超长时截断（200/300 字符），且不抛异常。"""
    recorder = TraceRecorder()
    long_args = "x" * 500
    recorder.record_tool("tool", long_args, _j(数据=("a" * 800)))
    ref = recorder.to_report().tool_calls[0]
    assert len(ref.arguments_summary) <= 200
    assert len(ref.result_summary) <= 300
    assert "…" in ref.arguments_summary


def test_to_report_since_slices_events() -> None:
    """to_report(since=cursor) 只汇总游标之后的事件（本轮切片）。"""
    recorder = TraceRecorder()
    recorder.record_tool("a", "{}", _j(值=1))
    cursor = recorder.cursor()
    recorder.record_tool("b", "{}", _j(值=2))
    report = recorder.to_report(since=cursor)
    assert [ref.name for ref in report.tool_calls] == ["b"]


def test_clear_resets_events() -> None:
    """clear 清空已记录事件（新会话复用记录器）。"""
    recorder = TraceRecorder()
    recorder.record_tool("x", "{}", "{}")
    recorder.clear()
    assert recorder.to_report().tool_calls == ()


# ------ 关闭态 ------
def test_null_trace_zero_recording() -> None:
    """关闭态：不产生事件、注入文本与报告为空。"""
    null_trace.record_tool("sun_times", "{}", _j(日出="05:12"))
    null_trace.record_llm("ecnu-plus")
    assert null_trace.to_prompt_section() == ""
    assert null_trace.to_report().tool_calls == ()
    assert null_trace.cursor() == 0
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


# ------ Agent / ReActLoop 集成（E2-2）------
class _FakeChatClient:
    """按脚本预置响应序列的伪客户端。"""

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list = []

    def chat(self, messages, *, model=None, tools=None, temperature=0.2) -> dict:
        self.calls.append({"messages": messages, "model": model, "tools": tools})
        return self._responses.pop(0)


def _tool_call_msg() -> dict:
    return {
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


def test_agent_loop_writes_trace() -> None:
    """Agent 注入 recorder：工具调用进轨迹、LLM/工具事件被订阅者收到。"""
    recorder = TraceRecorder()
    kinds: list[str] = []
    recorder.subscribe(lambda event: kinds.append(event.kind))

    fake = _FakeChatClient([_tool_call_msg(), {"role": "assistant", "content": "现在是 23:40。"}])
    agent = Agent(fake, registry, model="ecnu-plus", recorder=recorder)
    agent.run("现在几点？")

    section = recorder.to_prompt_section()
    assert "get_current_time" in section
    report = recorder.to_report()
    assert any(call.name == "get_current_time" for call in report.tool_calls)
    assert len(report.llm_calls) == 2
    assert KIND_TOOL in kinds
    assert KIND_LLM in kinds


def test_loop_injects_trace_into_next_round_system() -> None:
    """第二轮 LLM 请求的 system 第⑤层包含第一轮工具轨迹；首轮不包含。"""
    recorder = TraceRecorder()
    fake = _FakeChatClient([_tool_call_msg(), {"role": "assistant", "content": "回复"}])
    agent = Agent(fake, registry, model="ecnu-plus", recorder=recorder)
    agent.run("现在几点？")

    first_system = fake.calls[0]["messages"][0]["content"]
    second_system = fake.calls[1]["messages"][0]["content"]
    assert "会话轨迹摘要" not in first_system  # 首轮无事件
    assert "## 会话轨迹摘要" in second_system
    assert "① 调用 get_current_time" in second_system  # LLM 事件不占序号


def test_run_with_trace_returns_report() -> None:
    """run_with_trace 返回 (文本, TraceReport)，含 sources 与置信度。"""
    recorder = TraceRecorder()
    fake = _FakeChatClient([_tool_call_msg(), {"role": "assistant", "content": "现在是 23:40。"}])
    agent = Agent(fake, registry, model="ecnu-plus", recorder=recorder)
    text, report = agent.run_with_trace("现在几点？")

    assert text == "现在是 23:40。"
    assert len(report.tool_calls) == 1
    assert report.tool_calls[0].name == "get_current_time"
    assert report.tool_calls[0].confidence == "high"  # 确定性工具
    assert report.sources[0].tool == "get_current_time"
    assert report.sources[0].field == "时间"
    assert len(report.llm_calls) == 2


def test_run_with_trace_report_scoped_to_this_run() -> None:
    """连续两次 run_with_trace：第二次报告只含本次事件（cursor 切片）。"""
    recorder = TraceRecorder()
    fake = _FakeChatClient(
        [
            _tool_call_msg(),
            {"role": "assistant", "content": "第一次"},
            _tool_call_msg(),
            {"role": "assistant", "content": "第二次"},
        ]
    )
    agent = Agent(fake, registry, model="ecnu-plus", recorder=recorder)
    text1, report1 = agent.run_with_trace("第一问")
    text2, report2 = agent.run_with_trace("第二问")

    assert text1 == "第一次"
    assert text2 == "第二次"
    assert len(report1.tool_calls) == 1
    assert len(report2.tool_calls) == 1
    # 第二次的轨迹注入包含第一次的工具调用（对模型的记忆），但报告只含本轮
    assert len(recorder.to_prompt_section().splitlines()) >= 2
