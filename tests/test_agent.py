"""AgentRuntime 循环的 pytest 用例（伪客户端 + 自定义 Model 桥，离线验证）。

B2-7 换装：由旧 Agent 门面改为 AgentRuntime（PydanticAI）——工具调用仍经注册表 dispatch，
「工具调用 → 结果回传 → 最终回复」的完整循环由框架驱动。
"""

from __future__ import annotations

from typing import Any

from lighttrail.adapters.llm.provider import ChatClientProvider
from lighttrail.adapters.llm.pydantic_bridge import LightTrailModel, to_openai_history
from lighttrail.agent.tools import registry
from lighttrail.runtime.agent import AgentRuntime
from lighttrail.tools import basic, exposure  # noqa: F401  确保工具已注册


class FakeChatClient:
    """按脚本预置响应序列的伪客户端，用于离线验证 runtime 循环。"""

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def chat(self, messages, *, model=None, tools=None, temperature=0.2, usage_callback=None) -> dict:
        self.calls.append({"messages": messages, "model": model, "tools": tools})
        return self._responses.pop(0)

    async def acall(self, messages, **kwargs) -> dict:
        """async 通道：B2-7 起 runtime 经 Model 桥走 acall。"""
        allowed = {k: v for k, v in kwargs.items() if k in {"model", "tools", "temperature"}}
        return self.chat(messages, **allowed)


def _runtime(fake: FakeChatClient, *, model: str = "ecnu-plus") -> AgentRuntime:
    """把伪客户端经自定义 Model 桥接成 AgentRuntime。"""
    provider = ChatClientProvider(fake)
    return AgentRuntime(LightTrailModel(provider, model_name=model), registry)


def _tool_call_msg() -> dict[str, Any]:
    """一条带工具调用的助手响应。"""
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


def test_agent_loop() -> None:
    """runtime 应完成「工具调用 → 结果回传 → 最终回复」的完整循环。"""
    final_msg = {"role": "assistant", "content": "现在是北京时间 2026-08-12 23:40。"}
    fake = FakeChatClient([_tool_call_msg(), final_msg])
    runtime = _runtime(fake)
    reply = runtime.run("现在几点？")

    # 最终回复来自模型
    assert reply.startswith("现在是")
    # 伪客户端被调用 2 次
    assert len(fake.calls) == 2
    # 第一轮请求应携带 tools schema
    assert fake.calls[0]["tools"] is not None
    # 工具结果已回传：第二轮消息含 role=tool
    roles = [m["role"] for m in fake.calls[1]["messages"]]
    assert "tool" in roles
    # 历史保留用户消息
    history = to_openai_history(runtime.messages)
    assert any(m.get("role") == "user" and m.get("content") == "现在几点？" for m in history)

    runtime.reset()
    assert runtime.messages == []


def test_agent_loop_uses_bound_chat_model() -> None:
    """对话循环用装配根绑定的 chat 模型（B2-7：模型在装配根决定，运行时不做品牌判断）。"""
    final_msg = {"role": "assistant", "content": "现在是北京时间 2026-08-12 23:40。"}
    fake = FakeChatClient([_tool_call_msg(), final_msg])
    runtime = _runtime(fake, model="ecnu-plus")
    runtime.run("现在几点？")
    assert fake.calls[0]["model"] == "ecnu-plus"
    assert fake.calls[1]["model"] == "ecnu-plus"