"""契约层（contracts/）的 pytest 用例（B1-1 起步，B1-2 起补充）。

验证：
- ToolSpec → OpenAI function schema 转换（三段结构、参数 JSON Schema 原样透传）；
- ToolSpec / RequestContext 为 frozen（不可变）；默认值符合设计（confidence=low、
  main_field=""、user_id="_local"、llm_overrides=None）；
- Confidence 三档取值与既有字符串口径一致（str 混用，存量代码零改动）；
- Tool 是结构化端口（runtime_checkable：实现 spec + __call__ 即满足）；
- ToolContext 关闭态（无 sink）emit 零开销，有 sink 时投递 TraceEvent（B1-2）；
- B1-2 契约下沉：Plan 步数护栏、TraceEvent/SSEEvent 单一真源、旧路径 shim 同一对象。
"""

from __future__ import annotations

import dataclasses

import pytest

from lighttrail.contracts import (
    Confidence,
    RequestContext,
    Tool,
    ToolContext,
    ToolResult,
    ToolSpec,
)
from lighttrail.contracts.events import KIND_TOOL, SSEEvent, TraceEvent
from lighttrail.contracts.plan import Plan, PlanStep


def test_tool_spec_to_openai_schema() -> None:
    """ToolSpec 转 OpenAI tool 定义：三段结构 + 参数 JSON Schema 原样透传。"""
    spec = ToolSpec(
        name="star_shutter_rule",
        description="按 500/NPF 法则算星空最大快门",
        parameters={"type": "object", "properties": {"focal_length": {"type": "number"}}},
        capabilities=frozenset({"tools"}),
        main_field="最大快门",
        confidence=Confidence.HIGH,
    )
    schema = spec.to_openai_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "star_shutter_rule"
    assert schema["function"]["description"].startswith("按 500/NPF")
    assert schema["function"]["parameters"] == spec.parameters


def test_tool_spec_defaults() -> None:
    """默认值：无能力声明、main_field 空、置信度 low（未知工具如实降级）。"""
    spec = ToolSpec(name="x", description="d")
    assert spec.capabilities == frozenset()
    assert spec.main_field == ""
    assert spec.confidence is Confidence.LOW
    assert spec.to_openai_schema()["function"]["parameters"] == {}


def test_tool_spec_and_request_context_are_frozen() -> None:
    """frozen 契约：ToolSpec 与 RequestContext 不可就地修改。"""
    spec = ToolSpec(name="x", description="d")
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.name = "y"  # type: ignore[misc]
    ctx = RequestContext()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.user_id = "u1"  # type: ignore[misc]


def test_request_context_local_defaults() -> None:
    """RequestContext 默认本地单用户：user_id=_local、session_id 空、overrides 恒 None。"""
    ctx = RequestContext()
    assert ctx.user_id == "_local"
    assert ctx.session_id == ""
    assert ctx.llm_overrides is None
    assert RequestContext(user_id="u1", session_id="s1").session_id == "s1"


def test_confidence_str_compatible() -> None:
    """Confidence 与既有字符串置信度口径一致（`== "high"` 直接成立）。"""
    assert Confidence.HIGH == "high"
    assert Confidence.MEDIUM == "medium"
    assert Confidence.LOW == "low"
    assert {item.value for item in Confidence} == {"high", "medium", "low"}


def test_tool_protocol_structural_check() -> None:
    """Tool 是结构化端口：实现 spec + __call__ 的类即满足（runtime_checkable）。"""

    class _Echo:
        spec = ToolSpec(name="echo", description="回显工具")

        def __call__(self, ctx: ToolContext, **kwargs: object) -> ToolResult:
            return ToolResult(content="ok")

    assert isinstance(_Echo(), Tool)


def test_tool_context_emit_without_sink_is_noop() -> None:
    """关闭态：无 sink 时 emit 零开销、不抛错（不 import 事件类型）。"""
    ctx = ToolContext(request=RequestContext(), llm=object(), datasource=object())
    ctx.emit("tool", "sun_times", 结果="日出 05:12")  # 不应抛错

# ------ B1-2：契约下沉（事件 / 计划 / shim）------
def test_tool_context_emit_forwards_trace_event() -> None:
    """有 sink 时 emit 投递契约层 TraceEvent（kind/name/payload 口径与 trace 一致）。"""
    captured: list[TraceEvent] = []

    class _Sink:
        def emit(self, event: TraceEvent) -> None:
            captured.append(event)

    ctx = ToolContext(request=RequestContext(), llm=object(), datasource=object(), sink=_Sink())
    ctx.emit(KIND_TOOL, "sun_times", 结果="日出 05:12")
    assert len(captured) == 1
    assert captured[0].kind == KIND_TOOL
    assert captured[0].name == "sun_times"
    assert captured[0].payload == {"结果": "日出 05:12"}
    assert captured[0].ts  # 自动打时间戳


def test_plan_step_guard() -> None:
    """Plan 步数护栏：超限与非法上限都直接拒绝（不做静默截断）。"""
    steps = tuple(PlanStep(tool=f"tool_{index}") for index in range(3))
    plan = Plan(goal="今晚拍火烧云", steps=steps, max_steps=3)
    assert len(plan.steps) == 3
    assert plan.max_steps == 3
    with pytest.raises(ValueError):
        Plan(goal="超限", steps=steps, max_steps=2)
    with pytest.raises(ValueError):
        Plan(goal="非法上限", max_steps=0)


def test_trace_event_frozen_and_default_payload() -> None:
    """TraceEvent 为 frozen 契约，payload 默认空 dict 且每次实例独立。"""
    first = TraceEvent(kind=KIND_TOOL, name="a")
    second = TraceEvent(kind=KIND_TOOL, name="b")
    assert first.payload == {}
    assert first.payload is not second.payload  # 默认工厂不共享可变对象
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.name = "c"  # type: ignore[misc]


def test_schemas_shim_reexports_same_objects() -> None:
    """旧路径 shim 与新真源指向同一对象（迁移期零行为变化）。"""
    from lighttrail.contracts import models
    from lighttrail.orchestrator import schemas

    for name in ("DecisionCard", "Intent", "Source", "PhotoAnalysisReport", "PhotoReverseReport"):
        assert getattr(schemas, name) is getattr(models, name)


def test_trace_event_reexported_from_infra_trace() -> None:
    """infra.trace 迁移期仍可导出 TraceEvent / KIND_*（旧 import 路径可用）。"""
    from lighttrail.infra import trace

    assert trace.TraceEvent is TraceEvent
    assert trace.KIND_TOOL == KIND_TOOL


def test_sse_event_types_single_source() -> None:
    """SSE 事件类型单一真源：api.events 的常量由 contracts.events.SSEEvent 派生。"""
    from lighttrail.api import events

    assert events.EVENT_TOOL_CALL == SSEEvent.TOOL_CALL.value
    assert events.EVENT_DONE == SSEEvent.DONE.value
    assert {item.value for item in SSEEvent} == {
        "queued",
        "step",
        "tool_call",
        "tool_result",
        "token",
        "card",
        "error",
        "done",
    }