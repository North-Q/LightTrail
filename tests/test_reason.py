"""Agent.reason 纯推理通道（E4-3）的 pytest 用例。

验证：
- 深推理路由（默认深推理模型）、tools=None、temperature=0.3、thinking 开启；
- reasoning_effort / system / model 覆盖透传；
- thinking 摘要写入 TraceReport（M2-04 推理可见）。
"""

from __future__ import annotations

from lighttrail.agent import Agent, registry
from lighttrail.infra.trace import TraceRecorder
from lighttrail.tools import basic, exposure  # noqa: F401  确保工具已注册


class FakeChatClient:
    """记录调用参数的伪客户端（可预置 thinking 内容）。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.thinking = ""

    def chat(
        self,
        messages,
        *,
        model=None,
        tools=None,
        temperature=0.2,
        thinking=None,
        reasoning_effort=None,
    ) -> dict:
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
        resp = {"role": "assistant", "content": "  推理结论  "}
        if self.thinking:
            resp["thinking"] = self.thinking
        return resp


def test_reason_default_uses_deep_model_without_tools() -> None:
    """reason 缺省：路由深推理模型、tools=None、temperature=0.3、不携带扩展参数。"""
    fake = FakeChatClient()
    agent = Agent(fake, registry)
    reply = agent.reason("分析一下这个场景")
    assert reply == "推理结论"
    call = fake.calls[0]
    assert call["model"] == "ecnu-max"
    assert call["tools"] is None
    assert call["temperature"] == 0.3
    # 平台中立：默认不发送 thinking/reasoning_effort（通用 OpenAI 兼容接口直接可用）
    assert call["thinking"] is None
    assert call["reasoning_effort"] is None


def test_reason_thinking_enabled_when_configured() -> None:
    """reason_thinking=True 时携带 thinking 扩展参数与 reasoning_effort。"""
    fake = FakeChatClient()
    agent = Agent(fake, registry, reason_thinking=True)
    agent.reason("综合判断", reasoning_effort="high")
    call = fake.calls[0]
    assert call["thinking"] == {"type": "enabled"}
    assert call["reasoning_effort"] == "high"


def test_reason_thinking_disabled_ignores_effort() -> None:
    """reason_thinking 关闭时即便传入 reasoning_effort 也不发送（平台中立）。"""
    fake = FakeChatClient()
    agent = Agent(fake, registry)
    agent.reason("综合判断", reasoning_effort="high")
    assert fake.calls[0]["reasoning_effort"] is None


def test_reason_custom_model_and_system() -> None:
    """model 覆盖 + system 覆盖（管线结构化指令）。"""
    fake = FakeChatClient()
    agent = Agent(fake, registry)
    agent.reason("判断", model="custom-reason", system="按 JSON 输出")
    call = fake.calls[0]
    assert call["model"] == "custom-reason"
    assert call["messages"][0]["content"] == "按 JSON 输出"


def test_reason_thinking_recorded_in_trace() -> None:
    """thinking 摘要写入 TraceReport（步骤 reason_thinking + LLM 调用记录）。"""
    fake = FakeChatClient()
    fake.thinking = "先看云量，再判断火烧云概率…（思考过程）"
    recorder = TraceRecorder()
    agent = Agent(fake, registry, recorder=recorder)
    agent.reason("今晚火烧云值得冲吗")
    report = recorder.to_report()
    assert len(report.llm_calls) == 1
    assert report.llm_calls[0]["模型"] == "ecnu-max"
    assert any(step.name == "reason_thinking" for step in report.steps)
    assert "云量" in report.steps[0].output_summary


def test_reason_without_thinking_no_step() -> None:
    """平台未返回 thinking 时不计 reason_thinking 步骤。"""
    fake = FakeChatClient()
    recorder = TraceRecorder()
    agent = Agent(fake, registry, recorder=recorder)
    agent.reason("简单问题")
    assert [s.name for s in recorder.to_report().steps] == []
