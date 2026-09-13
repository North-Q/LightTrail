"""PydanticAI 自定义 Model 桥（B2-5）的 pytest 用例。

验证：
- 端到端链路：Agent（框架循环 + 工具执行）→ 桥 → LLMProvider 端口 → 既有 ChatClient.acall；
- 消息映射：instructions / SystemPromptPart → 前置 system 消息；工具结果 → role=tool；
  assistant 文本 + tool_calls 合并为一条 assistant 消息；重试提示按有无工具名分流；
- 工具映射：ToolDefinition → OpenAI function schema（参数 JSON Schema 原样透传）；
- 平台扩展参数（ADR-003）：ModelSettings.extra_body 的 thinking / reasoning_effort 透传到 provider；
- 用量：provider 的 usage 回调 → ModelResponse.usage（input/output tokens）；
- 错误包装：provider 异常统一为 ModelAPIError。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters, ModelSettings, ToolDefinition

from lighttrail.adapters.llm.provider import ChatClientProvider
from lighttrail.adapters.llm.pydantic_bridge import LightTrailModel, _map_messages, _map_tools
from lighttrail.contracts.llm import LLMProvider


@pytest.fixture(autouse=True)
def _no_banner(monkeypatch: pytest.MonkeyPatch) -> None:
    """关闭 pydantic-ai 的启动横幅，保持测试输出干净。"""
    monkeypatch.setenv("PYDANTIC_AI_NO_BANNER", "1")


def _doubler(x: int) -> int:
    """把整数翻倍（桥测试用的探测工具）。"""
    return x * 2


class _FakeChatClient:
    """按脚本返回 OpenAI 风格响应的伪客户端（记录 acall 参数）。"""

    def __init__(self, replies: list[dict[str, Any]]) -> None:
        self._replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def acall(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        thinking: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
        usage_callback: Any = None,
    ) -> Any:
        """返回协程：记录参数 + 回传固定 usage + 弹出下一条脚本响应。"""

        async def _run() -> dict[str, Any]:
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
                usage_callback(120, 30)
            return self._replies.pop(0)

        return _run()

    def queue_position(self) -> int:
        """伪客户端恒空闲。"""
        return 0


def _tool_call_reply(name: str, args: dict[str, Any], call_id: str = "c1") -> dict[str, Any]:
    """构造一条带工具调用的助手回复。"""
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}],
    }


async def test_agent_loop_runs_through_bridge() -> None:
    """端到端：框架循环 → 桥 → provider → ChatClient（工具调用 + 结果回传 + 终答）。"""
    fake = _FakeChatClient([_tool_call_reply("_doubler", {"x": 21}), {"role": "assistant", "content": "结果是 42"}])
    model = LightTrailModel(ChatClientProvider(fake), model_name="ecnu-plus")
    agent = Agent(model, instructions="你是摄影师助手", tools=[_doubler])

    result = await agent.run("把 21 翻倍")

    assert result.output == "结果是 42"
    assert len(fake.calls) == 2
    first = fake.calls[0]
    assert first["messages"][0] == {"role": "system", "content": "你是摄影师助手"}
    assert first["messages"][1]["role"] == "user"
    assert first["model"] == "ecnu-plus"
    assert first["tools"][0]["function"]["name"] == "_doubler"

    second = fake.calls[1]
    assert second["messages"][-1]["role"] == "tool"
    assert second["messages"][-1]["tool_call_id"] == "c1"
    assert second["messages"][-1]["content"] == "42"
    assistant = [m for m in second["messages"] if m["role"] == "assistant"][-1]
    assert assistant["tool_calls"][0]["function"]["name"] == "_doubler"


async def test_reason_params_passed_to_provider() -> None:
    """平台扩展参数（thinking / reasoning_effort）经 ModelSettings.extra_body 透传（ADR-003）。"""
    fake = _FakeChatClient([{"role": "assistant", "content": "ok"}])
    model = LightTrailModel(
        ChatClientProvider(fake),
        model_name="ecnu-max",
        settings=ModelSettings(extra_body={"thinking": {"type": "enabled"}, "reasoning_effort": "high"}),
    )
    await Agent(model).run("综合判断")

    call = fake.calls[0]
    assert call["thinking"] == {"type": "enabled"}
    assert call["reasoning_effort"] == "high"


async def test_usage_forwarded_to_request_usage() -> None:
    """provider 的 usage 回调 → ModelResponse.usage（输入/输出 token）。"""
    fake = _FakeChatClient([{"role": "assistant", "content": "hi"}])
    model = LightTrailModel(ChatClientProvider(fake), model_name="ecnu-plus")

    response = await model.request(
        [ModelRequest(parts=[UserPromptPart(content="hi")])], None, ModelRequestParameters()
    )

    assert response.usage.input_tokens == 120
    assert response.usage.output_tokens == 30
    assert response.finish_reason == "stop"
    assert response.model_name == "ecnu-plus"


def test_map_messages_shapes() -> None:
    """消息映射：system 前置、assistant 合并 tool_calls、工具结果与重试提示分流。"""
    messages = [
        ModelRequest(parts=[SystemPromptPart(content="平台规则"), UserPromptPart(content="第一问")], instructions="系统指令"),
        ModelResponse(parts=[TextPart(content="回答"), ToolCallPart("_doubler", {"x": 2}, tool_call_id="c9")]),
        ModelRequest(parts=[ToolReturnPart("_doubler", 4, tool_call_id="c9")]),
        ModelRequest(parts=[RetryPromptPart("参数不合法", tool_name="_doubler", tool_call_id="c9")]),
        ModelRequest(parts=[UserPromptPart(content="继续")]),
    ]

    mapped = _map_messages(messages)

    assert mapped[0] == {"role": "system", "content": "系统指令"}
    assert mapped[1] == {"role": "system", "content": "平台规则"}
    assert mapped[2] == {"role": "user", "content": "第一问"}
    assert mapped[3]["role"] == "assistant"
    assert mapped[3]["content"] == "回答"
    assert json.loads(mapped[3]["tool_calls"][0]["function"]["arguments"]) == {"x": 2}
    assert mapped[4] == {"role": "tool", "tool_call_id": "c9", "content": "4"}
    assert mapped[5] == {"role": "tool", "tool_call_id": "c9", "content": "参数不合法"}
    assert mapped[6] == {"role": "user", "content": "继续"}
    assert "instructions" not in mapped[0]  # 只保留 OpenAI 协议字段


def test_map_tools_passthrough_schema() -> None:
    """工具映射：ToolDefinition → OpenAI function schema，参数 JSON Schema 原样透传。"""
    tool = ToolDefinition(
        name="sun_times",
        description="日出日落查询",
        parameters_json_schema={"type": "object", "properties": {"latitude": {"type": "number"}}},
    )
    assert _map_tools([tool]) == [
        {
            "type": "function",
            "function": {
                "name": "sun_times",
                "description": "日出日落查询",
                "parameters": tool.parameters_json_schema,
            },
        }
    ]


def test_provider_satisfies_llm_provider_contract() -> None:
    """ChatClientProvider 结构化满足 LLMProvider 端口。"""
    assert isinstance(ChatClientProvider(_FakeChatClient([])), LLMProvider)


async def test_provider_error_wrapped_as_model_api_error() -> None:
    """provider 异常统一包装为 ModelAPIError（框架可见的模型错误类型）。"""

    class _BoomProvider:
        async def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("供应商炸了")

        def queue_position(self) -> int:
            return 0

    model = LightTrailModel(_BoomProvider(), model_name="ecnu-plus")
    with pytest.raises(ModelAPIError):
        await model.request(
            [ModelRequest(parts=[UserPromptPart(content="hi")])], None, ModelRequestParameters()
        )