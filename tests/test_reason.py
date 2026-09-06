"""Agent.reason 纯推理透传通道的 pytest 用例。"""

from __future__ import annotations

from lighttrail.agent import Agent, registry
from lighttrail.tools import basic, exposure  # noqa: F401  确保工具已注册


class FakeChatClient:
    """记录调用参数的伪客户端。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def chat(self, messages, *, model=None, tools=None, temperature=0.2) -> dict:
        self.calls.append({"messages": messages, "model": model, "tools": tools})
        return {"role": "assistant", "content": "  推理结论  "}


def test_reason_default_model_is_max() -> None:
    """reason() 缺省 model 时由路由解析为深推理模型 ecnu-max。"""
    fake = FakeChatClient()
    agent = Agent(fake, registry)
    reply = agent.reason("分析一下这个场景")
    assert reply == "推理结论"
    assert fake.calls[0]["model"] == "ecnu-max"
    assert fake.calls[0]["tools"] is None