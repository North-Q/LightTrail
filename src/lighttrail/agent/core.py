"""Agent 对外门面：组合 ReAct 循环、上下文构建与模型路由。

设计要点（架构 v2.0）：
- 循环 / 上下文 / 路由分别收敛到 loop.py、context.py、llm/router.py，
  本模块只做组装，`Agent.run()` 公开签名与行为保持不变；
- reason() 提供纯推理透传通道（E4-3 落地前为一次不带工具的单轮调用）。
"""

from __future__ import annotations

import time
from typing import Any

from lighttrail.agent.context import DEFAULT_CONDUCT_PROMPT, DEFAULT_ROLE_PROMPT, ContextBuilder
from lighttrail.agent.loop import MAX_TOOL_ROUNDS, ReActLoop
from lighttrail.agent.tools import ToolRegistry
from lighttrail.infra.trace import Recorder, TraceReport, null_trace
from lighttrail.llm.client import ChatClient
from lighttrail.llm.router import ModelRouter, RouteIntent
from lighttrail.memory import MemoryManager

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
        memory: MemoryManager | None = None,
        reason_thinking: bool = False,
    ) -> None:
        self._client = client
        self._router = router or ModelRouter()
        self._recorder: Recorder = recorder or null_trace
        self._memory = memory
        # reason 通道是否发送 thinking/reasoning_effort 扩展参数（平台中立：默认关闭，
        # 通用 OpenAI 兼容接口不带这些字段即可用；支持的平台在 .env 设 LLM_REASON_THINKING=true）
        self._reason_thinking = reason_thinking
        self._pending_intent = ""  # 当前用户输入的意图文本（供记忆按需检索）
        # system_prompt 参数语义（E1-2 起）：覆盖第①层「角色与使命」，行为准则固定用默认；
        # 第④层「用户档案+语义记忆」接 MemoryManager（E3-1 先只有档案常驻块）；
        # 第⑤层「会话轨迹摘要」接 recorder（E2-2）：每轮组装的 system 自动包含已发生轨迹
        profile_provider = self._memory_injection if self._memory is not None else None
        self._context = ContextBuilder(
            role_prompt=system_prompt,
            conduct_prompt=DEFAULT_CONDUCT_PROMPT,
            registry=registry,
            profile_provider=profile_provider,
            trace_provider=lambda: self._recorder.to_prompt_section(),
        )
        self._loop = ReActLoop(
            client,
            registry,
            model=model,
            max_tool_rounds=max_tool_rounds,
            context=self._context,
            recorder=recorder,
            router=self._router,
        )
        self._system_prompt = system_prompt
        self._messages: list[dict[str, Any]] = []

    # ------ 内部实现 ------
    def _memory_injection(self) -> str:
        """按当前意图组装第④层记忆注入文本（档案常驻 + 事件按需）。"""
        return "\n".join(block.text for block in self._memory.build_injections(self._pending_intent))

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
        self._pending_intent = user_input
        self._messages.append({"role": "user", "content": user_input})
        try:
            text = self._loop.run(self._messages)
        except Exception:
            # 循环失败时回滚本轮 user 消息，避免污染历史
            self._messages.pop()
            raise
        return text, self._recorder.to_report(since=since)

    def reason(
        self,
        prompt: str,
        *,
        system: str = "",
        model: str | None = None,
        reasoning_effort: str | None = None,
        temperature: float = 0.3,
    ) -> str:
        """纯推理通道：深推理模型单轮调用（不携带工具，thinking 开启）。

        Args:
            prompt: 推理问题文本。
            system: 附加系统提示，缺省用角色层（E5 管线可传入略结构化指令）。
            model: 模型名覆盖；缺省由路由按深推理意图解析（默认矩阵 → 深推理模型）。
            reasoning_effort: 推理强度（low/medium/high）；仅当 reason_thinking 开启时透传。
            temperature: 采样温度（默认 0.3，深推理综合输出用稍高值）。

        Raises:
            LLMError: 调用失败（上游 SDK 不支持扩展参数时，请关闭 reason_thinking）。
        

        Returns:
            纯文本推理结果（thinking 摘要已记入 TraceReport，供 M2-04 推理可见）。
        """
        resolved_model = model or RouteIntent.DEEP_REASONING.resolve(self._router)
        messages = [
            {"role": "system", "content": system or self._system_prompt},
            {"role": "user", "content": prompt},
        ]
        started = time.perf_counter()
        resp = self._client.chat(
            messages,
            model=resolved_model,
            tools=None,
            temperature=temperature,
            thinking={"type": "enabled"} if self._reason_thinking else None,
            reasoning_effort=reasoning_effort if self._reason_thinking else None,
        )
        self._recorder.record_llm(
            resolved_model,
            prompt_summary=f"深推理问题（{len(prompt)} 字符）",
            duration_s=time.perf_counter() - started,
        )
        thinking = resp.get("thinking", "")
        if thinking:
            self._recorder.record_step(
                "reason_thinking",
                input_summary=f"问题：{prompt[:80]}",
                output_summary=thinking[:120],
            )
        return resp.get("content", "").strip()

    def load_history(self, messages: list[dict[str, Any]]) -> None:
        """用已有历史替换当前会话历史（Web 会话恢复用，CLI 不受影响）。

        Args:
            messages: 要载入的消息历史（user/assistant/tool 字典列表，不含 system）。
        """
        self._messages = [dict(message) for message in messages]

    def reset(self) -> None:
        """清空对话历史（保留系统提示）。"""
        self._messages = []

    @property
    def history(self) -> list[dict[str, Any]]:
        """当前消息历史（只读视图）。"""
        return list(self._messages)

    @property
    def router(self) -> ModelRouter:
        """模型路由（供编排层复用同一能力矩阵）。"""
        return self._router