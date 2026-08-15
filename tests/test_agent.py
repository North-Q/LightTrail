"""Agent 循环的 pytest 用例（使用伪客户端，离线验证）。

迁移自 src/lighttrail/smoke.py 的 test_agent_loop，
使用 FakeChatClient 按脚本预置响应序列驱动 Agent 多轮工具调用。
"""

from __future__ import annotations

from lighttrail.agent import Agent, registry
from lighttrail.tools import basic, exposure  # noqa: F401  确保工具已注册


class FakeChatClient:
    """按脚本预置响应序列的伪客户端，用于离线验证 Agent 循环。"""

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def chat(self, messages, *, model=None, tools=None, temperature=0.2) -> dict:
        self.calls.append({"messages": messages, "tools": tools})
        return self._responses.pop(0)


def test_agent_loop() -> None:
    """Agent 应完成「工具调用 → 结果回传 → 最终回复」的完整循环。"""
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
    fake = FakeChatClient([tool_call_msg, final_msg])
    agent = Agent(fake, registry, model="ecnu-plus")
    reply = agent.run("现在几点？")

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
    assert any(m.get("content") == "现在几点？" for m in agent.history)

    agent.reset()
    assert agent.history == []
