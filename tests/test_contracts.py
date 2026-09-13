"""契约层（contracts/）的 pytest 用例（B1-1 起步，B1-2 起补充）。

验证：
- ToolSpec → OpenAI function schema 转换（三段结构、参数 JSON Schema 原样透传）；
- ToolSpec / RequestContext 为 frozen（不可变）；默认值符合设计（confidence=low、
  main_field=""、user_id="_local"、llm_overrides=None）；
- Confidence 三档取值与既有字符串口径一致（str 混用，存量代码零改动）；
- Tool 是结构化端口（runtime_checkable：实现 spec + __call__ 即满足）；
- ToolContext 关闭态（无 sink）emit 零开销。
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