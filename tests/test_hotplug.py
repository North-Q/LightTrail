"""工具热插拔验收（B2-7，v4 §2.4）：新增工具只改收集点一行，四处自动生效。

四断言（对应 §2.4 第 5 条）：
1) **dispatch**：新工具（动态构造 ToolSpec 注入注册表）可直接分发，无需改注册表/运行时/提示词；
2) **trace**：工具事件的来源字段与置信度来自 ToolSpec（main_field / confidence）；
3) **confidence**：动态置信度规则（confidence_rule）按 spec 生效，不看工具名手抄表；
4) **能力叙述**：`AgentRuntime.system_prompt()` 的「可用工具」层自动包含新工具（提示词零改动）。

另附：框架侧工具 schema 与 ToolSpec 同源（`Tool.from_schema(json_schema=spec.parameters)`）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from lighttrail.adapters.llm.pydantic_bridge import LightTrailModel
from lighttrail.contracts.tool import Confidence, ToolSpec
from lighttrail.infra.trace import TraceRecorder
from lighttrail.runtime.agent import AgentRuntime
from lighttrail.runtime.registry import ToolRegistry
from lighttrail.tools._base import PureTool


@pytest.fixture(autouse=True)
def _no_banner(monkeypatch: pytest.MonkeyPatch) -> None:
    """关闭 pydantic-ai 启动横幅。"""
    monkeypatch.setenv("PYDANTIC_AI_NO_BANNER", "1")


class _NullProvider:
    """不触网的 provider（本用例只断言 schema/叙述/trace，不发起对话）。"""

    async def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        return {"role": "assistant", "content": "ok"}

    def queue_position(self) -> int:
        return 0


def _tide_tool() -> PureTool:
    """构造一个「新工具」（模拟新增工具只写一行收集点）。"""
    spec = ToolSpec(
        name="tide_table",
        description="查询潮汐表：给出某地的涨落潮时刻",
        parameters={
            "type": "object",
            "properties": {"station": {"type": "string", "description": "观测站名，如『临港』"}},
            "required": ["station"],
        },
        capabilities=frozenset({"tools"}),
        main_field="潮汐",
        confidence=Confidence.MEDIUM,
    )
    return PureTool(spec=spec, func=lambda station="": {"潮汐": f"{station} 涨潮 06:12"})


def test_new_tool_takes_effect_in_dispatch_trace_and_narrative() -> None:
    """新工具注入注册表后：dispatch / trace 元数据 / 能力叙述三处自动生效。"""
    recorder = TraceRecorder()
    registry = ToolRegistry([_tide_tool()], recorder=recorder)  # ←「新增工具 = 一行」
    runtime = AgentRuntime(
        LightTrailModel(_NullProvider(), model_name="ecnu-plus"),
        registry,
        recorder=recorder,
    )

    # 1) dispatch：无需改运行时
    payload = json.loads(
        registry.dispatch("tide_table", json.dumps({"station": "临港"}), recorder=recorder)
    )
    assert payload["潮汐"] == "临港 涨潮 06:12"

    # 2) trace：来源字段与置信度来自 ToolSpec
    ref = recorder.to_report().tool_calls[0]
    assert (ref.name, ref.field, ref.confidence) == ("tide_table", "潮汐", "medium")

    # 4) 能力叙述：提示词自动包含新工具（零手改）
    prompt = runtime.system_prompt()
    assert "tide_table：查询潮汐表：给出某地的涨落潮时刻" in prompt


def test_new_tool_schema_comes_from_tool_spec() -> None:
    """框架侧工具 schema 与 ToolSpec 同源（参数 JSON Schema 原样透传）。"""
    tool = _tide_tool()
    registry = ToolRegistry([tool])
    schema = registry.to_openai_schema()[0]["function"]
    assert schema["name"] == "tide_table"
    assert schema["parameters"] == tool.spec.parameters
    assert schema["parameters"]["required"] == ["station"]


def test_dynamic_confidence_rule_applies_to_new_tool() -> None:
    """动态置信度规则随 spec 生效（预报时效：覆盖当日 → high，不看工具名）。"""
    recorder = TraceRecorder()
    registry = ToolRegistry(
        [
            PureTool(
                spec=ToolSpec(
                    name="tide_forecast",
                    description="潮汐预报（新工具，动态置信度）",
                    parameters={"type": "object", "properties": {}},
                    main_field="潮汐预报",
                    confidence=Confidence.MEDIUM,
                    confidence_rule="forecast",
                ),
                func=lambda **_: {"每日预报": [{"日期": datetime.now(timezone.utc).date().isoformat()}]},
            )
        ],
        recorder=recorder,
    )

    registry.dispatch("tide_forecast", "{}", recorder=recorder)

    assert recorder.to_report().tool_calls[0].confidence == "high"  # 覆盖当日 → high
    assert recorder.to_report().tool_calls[0].field == "潮汐预报"