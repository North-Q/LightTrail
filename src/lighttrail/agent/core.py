"""Agent 对外门面：组合 ReAct 循环、上下文构建与模型路由。

设计要点（架构 v2.0）：
- 循环 / 上下文 / 路由分别收敛到 loop.py、context.py、llm/router.py，
  本模块只做组装，`Agent.run()` 公开签名与行为保持不变；
- reason() 提供纯推理透传通道（E4-3 落地前为一次不带工具的单轮调用）。
"""

from __future__ import annotations

from typing import Any

from lighttrail.agent.context import DEFAULT_CONDUCT_PROMPT, DEFAULT_ROLE_PROMPT, ContextBuilder
from lighttrail.agent.loop import MAX_TOOL_ROUNDS, ReActLoop
from lighttrail.agent.tools import ToolRegistry
from lighttrail.infra.trace import Recorder, TraceReport, null_trace
from lighttrail.llm.client import ChatClient
from lighttrail.llm.router import ModelRouter

DEFAULT_SYSTEM_PROMPT = DEFAULT_ROLE_PROMPT + "\n\n" + DEFAULT_CONDUCT_PROMPT


class Agent:
    """带消息历史与工具调用能力的 Agent 门面。"""

    def __init__(
        self,
        client: ChatClient,
        registry: ToolRegistry,
        *,
        model: str | None = None,
        system_prompt: str = DEFAULT_ROLE_PROMPT,
        max_tool_rounds: int = MAX_TOOL_ROUNDS,
        router: ModelRouter | None = None,
        recorder: Recorder | None = None,
    ) -> None:
        self._client = client
        self._router = router or ModelRouter()
        self._recorder: Recorder = recorder or null_trace
        # system_prompt 参数语义（E1-2 起）：覆盖第①层「角色与使命」，行为准则固定用默认；
        # 第⑤层「会话轨迹摘要」接 recorder（E2-2）：每轮组装的 system 自动包含已发生轨迹
        self._context = ContextBuilder(
            role_prompt=system_prompt,
            conduct_prompt=DEFAULT_CONDUCT_PROMPT,
            registry=registry,
            trace_provider=lambda: self._recorder.to_prompt_section(),
        )
        self._loop = ReActLoop(
            client,
            registry,
            model=model,
            max_tool_rounds=max_tool_rounds,
            context=self._context,
            recorder=recorder,
        )
        self._system_prompt = system_prompt
        self._messages: list[dict[str, Any]] = []

    # ------ 对外接口 ------
    def run(self, user_input: str) -> str:
        """处理一条用户输入，返回最终文本回复（多轮历史自动累积）。

        兼容入口：等价于 run_with_trace()[0]，历史与轨迹行为一致。
        """
        return self.run_with_trace(user_input)[0]

    def run_with_trace(self, user_input: str) -> tuple[str, TraceReport]:
        """处理一条用户输入，返回 (文本回复, 本轮 TraceReport)。

        Args:
            user_input: 用户输入文本。

        Returns:
            (最终回复, 结构化轨迹报告)：报告的 tool_calls / steps / llm_calls
            仅包含本次调用期间产生的事件（经 cursor 切片），sources 携带
            来源字段与规则化置信度（M2 依据）。

        Raises:
            底层 LLMError / 工具异常原样上抛；失败时回滚本轮 user 消息。
        """
        since = self._recorder.cursor()
        self._messages.append({"role": "user", "content": user_input})
        try:
            text = self._loop.run(self._messages)
        except Exception:
            # 循环失败时回滚本轮 user 消息，避免污染历史
            self._messages.pop()
            raise
        return text, self._recorder.to_report(since=since)

    def reason(self, prompt: str, *, model: str | None = None) -> str:
        """纯推理通道（E4-3 落地前为透传：一次不带工具的单轮调用）。

        Args:
            prompt: 推理问题文本。
            model: 模型名，缺省由路由按深推理需求解析（默认 ecnu-max）。
        """
        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": prompt},
        ]
        resp = self._client.chat(
            messages,
            model=model or self._router.resolve(needs_deep_reasoning=True),
        )
        return resp.get("content", "").strip()

    def reset(self) -> None:
        """清空对话历史（保留系统提示）。"""
        self._messages = []

    @property
    def history(self) -> list[dict[str, Any]]:
        """当前消息历史（只读视图）。"""
        return list(self._messages)