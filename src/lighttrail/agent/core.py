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
    ) -> None:
        self._client = client
        self._router = router or ModelRouter()
        # system_prompt 参数语义（E1-2 起）：覆盖第①层「角色与使命」，行为准则固定用默认
        self._context = ContextBuilder(
            role_prompt=system_prompt,
            conduct_prompt=DEFAULT_CONDUCT_PROMPT,
            registry=registry,
        )
        self._loop = ReActLoop(
            client,
            registry,
            model=model,
            max_tool_rounds=max_tool_rounds,
            context=self._context,
        )
        self._system_prompt = system_prompt
        self._messages: list[dict[str, Any]] = []

    # ------ 对外接口 ------
    def run(self, user_input: str) -> str:
        """处理一条用户输入，返回最终文本回复（多轮历史自动累积）。"""
        self._messages.append({"role": "user", "content": user_input})
        try:
            return self._loop.run(self._messages)
        except Exception:
            # 循环失败时回滚本轮 user 消息，避免污染历史
            self._messages.pop()
            raise

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