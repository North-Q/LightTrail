"""AgentRuntime（B2-6：PydanticAI 组装门面）的 pytest 用例。

验证：
- ReAct 全链路：AgentRuntime → 框架循环 → 注册表 dispatch（声明式工具）→ 终答；
- trace 口径不变：工具事件带 ToolSpec 的 main_field / confidence，LLM 事件带 tokens；
- 热插拔：注册表新增工具后 system prompt 的能力叙述自动包含（无需手改提示词）；
- 深推理通道：走深推理模型、不带工具、temperature=0.3；
- 同步门面：无事件循环时 run() 可用；轮数护栏映射为框架用量上限。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic_ai.exceptions import UsageLimitExceeded

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


def _doubler(x: int = 0) -> dict[str, Any]:
    """把整数翻倍（探测领域函数）。"""
    return {"结果": x * 2}


def _great_circle(name: str = "") -> dict[str, Any]:
    """热插拔测试用的第二个探测工具。"""
    return {"结果": name.upper()}


def _spec(name: str, description: str, parameter: str = "x") -> ToolSpec:
    """构造探测工具声明（参数为一个整数）。"""
    return ToolSpec(
        name=name,
        description=description,
        parameters={
            "type": "object",
            "properties": {parameter: {"type": "integer"}},
            "required": [parameter],
        },
        capabilities=frozenset({"tools"}),
        main_field="结果",
        confidence=Confidence.HIGH,
    )


def _registry(*tools: PureTool) -> ToolRegistry:
    """用声明式工具构造注册表。"""
    return ToolRegistry(list(tools))


def _model(provider: _FakeProvider, name: str, recorder: Any = None) -> Any:
    """用自定义 Model 桥包住假 provider（运行时只接收 pydantic-ai Model）。"""
    return LightTrailModel(provider, model_name=name, recorder=recorder)


class _FakeProvider:
    """LLMProvider 替身：按脚本回应并记录调用参数（单条脚本会重复返回）。"""

    def __init__(self, replies: list[dict[str, Any]]) -> None:
        self._replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        thinking: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
        usage_callback: Any = None,
    ) -> dict[str, Any]:
        """记录参数、回传用量、返回下一条（或重复最后一条）脚本响应。"""
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": tools,
                "temperature": temperature,
                "thinking": thinking,
                "reasoning_effort": reasoning_effort,
            }
        )
        if usage_callback is not None:
            usage_callback(100, 20)
        reply = self._replies[0] if len(self._replies) == 1 else self._replies.pop(0)
        return reply

    def queue_position(self) -> int:
        """恒空闲。"""
        return 0


def _tool_call(name: str, args: dict[str, Any], call_id: str = "c1") -> dict[str, Any]:
    """构造带工具调用的助手响应。"""
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}],
    }


def test_arun_runs_react_loop_and_traces_spec_metadata() -> None:
    """端到端：注册表工具被框架调用，trace 工具/LLM 事件口径与旧实现一致。"""
    recorder = TraceRecorder()
    registry = _registry(PureTool(spec=_spec("doubler", "把整数翻倍"), func=_doubler))
    provider = _FakeProvider([_tool_call("doubler", {"x": 21}), {"role": "assistant", "content": "结果是 42"}])
    runtime = AgentRuntime(
        _model(provider, "ecnu-plus", recorder),
        registry,
        reason_model=_model(provider, "ecnu-max", recorder),
        recorder=recorder,
    )

    text, report = runtime.run_with_trace("把 21 翻倍")

    assert text == "结果是 42"
    assert [ref.name for ref in report.tool_calls] == ["doubler"]
    assert report.tool_calls[0].field == "结果"  # 来自 ToolSpec.main_field
    assert report.tool_calls[0].confidence == "high"  # 来自 ToolSpec.confidence
    assert json.loads(provider.calls[1]["messages"][-1]["content"]) == {"结果": 42}
    assert len(report.llm_calls) == 2
    assert report.llm_calls[0]["tokens"] == 120  # 桥把 usage 回调写进 trace
    assert provider.calls[0]["tools"][0]["function"]["name"] == "doubler"


def test_system_prompt_includes_new_tool_automatically() -> None:
    """热插拔：注册表新增工具后，能力叙述自动包含（提示词无需手改）。"""
    registry = _registry(
        PureTool(spec=_spec("doubler", "把整数翻倍"), func=_doubler),
        PureTool(spec=_spec("great_circle", "把名称转大写（新工具）", parameter="name"), func=_great_circle),
    )
    runtime = AgentRuntime(_model(_FakeProvider([{"role": "assistant", "content": "ok"}]), "m"), registry)
    prompt = runtime.system_prompt()
    assert "doubler：把整数翻倍" in prompt
    assert "great_circle：把名称转大写（新工具）" in prompt


def test_areason_uses_reason_model_without_tools() -> None:
    """深推理：走深推理模型、tools=None、temperature=0.3。"""
    provider = _FakeProvider([{"role": "assistant", "content": "  推理结论  "}])
    runtime = AgentRuntime(
        _model(provider, "ecnu-plus"), _registry(), reason_model=_model(provider, "ecnu-max")
    )

    text = runtime.reason("综合判断这个场景")

    assert text == "推理结论"
    call = provider.calls[0]
    assert call["model"] == "ecnu-max"
    assert call["tools"] is None
    assert call["temperature"] == 0.3
    assert call["thinking"] is None  # reason_thinking 默认关闭（平台中立）


def test_reason_thinking_passes_extra_params() -> None:
    """reason_thinking 开启时 thinking / reasoning_effort 透传到 provider（ADR-003）。"""
    provider = _FakeProvider([{"role": "assistant", "content": "ok"}])
    runtime = AgentRuntime(
        _model(provider, "m"), _registry(), reason_model=_model(provider, "ecnu-max"), reason_thinking=True
    )

    runtime.reason("判断", reasoning_effort="high")

    call = provider.calls[0]
    assert call["thinking"] == {"type": "enabled"}
    assert call["reasoning_effort"] == "high"


def test_acomplete_uses_chat_model_without_tools() -> None:
    """原始补全：用工具链路模型、不带工具（意图解析等结构化输出的前置面）。"""
    provider = _FakeProvider([{"role": "assistant", "content": '  {"subject_type": "星空"}  '}])
    runtime = AgentRuntime(_model(provider, "ecnu-plus"), _registry())

    text = runtime.complete("解析意图", system="只输出 JSON")

    assert text == '{"subject_type": "星空"}'
    call = provider.calls[0]
    assert call["model"] == "ecnu-plus"
    assert call["tools"] is None
    assert call["temperature"] == 0.2
    assert call["messages"][0]["content"] == "只输出 JSON"


def test_tool_round_limit_enforced_by_usage_limits() -> None:
    """轮数护栏：模型持续调用工具时由框架用量上限拦截（REACT_MAX_ROUNDS → request_limit）。"""
    provider = _FakeProvider([_tool_call("doubler", {"x": 1})])
    registry = _registry(PureTool(spec=_spec("doubler", "把整数翻倍"), func=_doubler))
    runtime = AgentRuntime(_model(provider, "m"), registry, max_tool_rounds=3)

    with pytest.raises(UsageLimitExceeded):
        runtime.run("无限调用工具")