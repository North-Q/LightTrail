"""工具注册表（声明式 ToolSpec 路径 + 迁移期兼容）的 pytest 用例（B2-1）。

验证：
- 声明式工具注册：dispatch 走 Tool 端口，返回 ToolResult.content 的 JSON 字符串；
- 元数据单一真源：tools schema 与 trace 的「来源字段 / 置信度」均来自 ToolSpec
  （不再查 trace._MAIN_FIELD 手抄表与 confidence 三集合）；
- 旧装饰器注册路径仍可用（迁移期兼容，无 spec 时走规则化兜底）；
- 工具名重复 / 非法报错，未知工具返回结构化错误。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from lighttrail.agent.tools import ToolRegistry
from lighttrail.contracts.tool import Confidence, ToolSpec
from lighttrail.infra.trace import TraceRecorder
from lighttrail.tools._base import PureTool


def _spec(name: str = "probe", **overrides: Any) -> ToolSpec:
    """构造探测工具的 ToolSpec（可覆盖任意字段）。"""
    fields: dict[str, Any] = {
        "name": name,
        "description": "探测工具",
        "parameters": {"type": "object", "properties": {"value": {"type": "integer"}}},
        "capabilities": frozenset({"tools"}),
        "main_field": "结果",
        "confidence": Confidence.HIGH,
    }
    fields.update(overrides)
    return ToolSpec(**fields)


def test_dispatch_declarative_tool_records_spec_metadata() -> None:
    """声明式工具：dispatch 返回 content；trace 的主字段/置信度来自 ToolSpec。"""
    recorder = TraceRecorder()
    registry = ToolRegistry(recorder=recorder)
    registry.register_tool(PureTool(spec=_spec(), func=lambda value=0: {"结果": value * 2}))

    text = registry.dispatch("probe", json.dumps({"value": 21}))
    assert json.loads(text) == {"结果": 42}

    ref = recorder.to_report().tool_calls[0]
    assert ref.name == "probe"
    assert ref.field == "结果"
    assert ref.confidence == "high"


def test_schema_and_specs_come_from_tool_spec() -> None:
    """schema 单一真源：声明式工具的 tools schema 直接来自 ToolSpec。"""
    registry = ToolRegistry()
    registry.register_tool(PureTool(spec=_spec(), func=lambda value=0: {"结果": value}))
    assert registry.to_openai_schema() == [_spec().to_openai_schema()]
    assert [spec.name for spec in registry.specs()] == ["probe"]


def test_legacy_registration_path_still_works() -> None:
    """迁移期兼容：旧装饰器注册仍可 dispatch，无 spec 时走规则化置信度兜底。"""
    recorder = TraceRecorder()
    registry = ToolRegistry(recorder=recorder)
    registry.register(lambda: {"ok": True}, name="legacy_probe", description="旧路径")

    assert json.loads(registry.dispatch("legacy_probe", "{}")) == {"ok": True}
    assert recorder.to_report().tool_calls[0].confidence == "low"
    assert registry.specs() == []


def test_duplicate_and_invalid_tool_names_rejected() -> None:
    """重复注册与非法工具名直接报错（装配期暴露，不静默覆盖）。"""
    registry = ToolRegistry()
    registry.register_tool(PureTool(spec=_spec(), func=lambda value=0: {}))
    with pytest.raises(ValueError):
        registry.register_tool(PureTool(spec=_spec(), func=lambda value=0: {}))
    with pytest.raises(ValueError):
        registry.register_tool(PureTool(spec=_spec(name="非法 名"), func=lambda value=0: {}))


def test_unknown_tool_returns_structured_error() -> None:
    """未知工具返回结构化错误（模型可据此修正），不抛异常。"""
    payload = json.loads(ToolRegistry().dispatch("nope", "{}"))
    assert "未知工具" in payload["error"]


def test_bad_arguments_return_structured_error() -> None:
    """参数不合法（TypeError）返回结构化错误（模型可修正后重试）。"""
    registry = ToolRegistry()
    registry.register_tool(PureTool(spec=_spec(), func=lambda value=0: {"结果": value}))
    payload = json.loads(registry.dispatch("probe", json.dumps({"unexpected": 1})))
    assert "error" in payload