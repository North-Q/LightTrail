"""Agent 主循环：多轮对话 + 工具调用最小链路。

流程：用户输入 → 模型响应 → 若含 tool_calls 则逐一执行并把结果回传模型 →
重复直至模型输出纯文本 → 返回最终回复。全程串行调用 LLM。
"""

from __future__ import annotations

import logging
from typing import Any

from lighttrail.agent.tools import ToolRegistry
from lighttrail.llm.client import ChatClient

logger = logging.getLogger("lighttrail.agent")

# 单轮对话中允许的最大工具调用轮次（防止模型陷入无限循环）
MAX_TOOL_ROUNDS = 8

DEFAULT_SYSTEM_PROMPT = """你是 LightTrail（光迹）摄影助手，一位专业摄影智能体。

职责：帮助摄影师完成拍摄前的规划与拍摄中的参数决策。
当前阶段提供以下能力（通过工具实现）：
- 获取当前时间，用于判断拍摄时机；
- 曝光参数推荐：等效曝光换算、星空 500/NPF 法则、ND 长曝光换算；
- 天文查询：日出日落/蓝调黄金/晨昏蒙影、太阳方位、月相月升月落、银心可见窗口；
- 天气查询：未来 1-7 天云量/能见度/降水/风力，火烧云概率评分；
- 机位匹配：多机位 × 天象条件对比排序。

行为要求：
- 回答简洁、专业，使用中文；
- 需要真实数据或计算时，优先调用工具获取，不要凭空编造；
- 用户未指定时，默认采用 135 全画幅相机与常见档位给出建议；
- 涉及具体拍摄决策时，可以给出推荐值，但要说明依据与取舍。
"""


class Agent:
    """带消息历史与工具调用能力的 Agent。"""

    def __init__(
        self,
        client: ChatClient,
        registry: ToolRegistry,
        *,
        model: str | None = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        max_tool_rounds: int = MAX_TOOL_ROUNDS,
    ) -> None:
        self._client = client
        self._registry = registry
        self._model = model
        self._system_prompt = system_prompt
        self._max_tool_rounds = max_tool_rounds
        self._messages: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def run(self, user_input: str) -> str:
        """处理一条用户输入，返回最终文本回复（多轮历史自动累积）。"""
        self._messages.append({"role": "user", "content": user_input})
        try:
            return self._run_loop()
        except Exception:
            # 循环失败时回滚本轮 user 消息，避免污染历史
            self._messages.pop()
            raise

    def reset(self) -> None:
        """清空对话历史（保留系统提示）。"""
        self._messages = []

    @property
    def history(self) -> list[dict[str, Any]]:
        """当前消息历史（只读视图）。"""
        return list(self._messages)

    # ------------------------------------------------------------------
    # 内部循环
    # ------------------------------------------------------------------
    def _run_loop(self) -> str:
        tools = self._registry.to_openai_schema()
        for _ in range(self._max_tool_rounds):
            resp = self._client.chat(
                self._build_messages(),
                model=self._model,
                tools=tools,
            )
            self._messages.append(resp)

            tool_calls = resp.get("tool_calls")
            if not tool_calls:
                return resp.get("content", "").strip()

            self._execute_tool_calls(tool_calls)

        logger.warning("工具调用超过 %d 轮，终止本轮对话", self._max_tool_rounds)
        return "（工具调用次数过多，本轮对话已终止。请简化问题或换一种问法。）"

    def _build_messages(self) -> list[dict[str, Any]]:
        return [{"role": "system", "content": self._system_prompt}, *self._messages]

    def _execute_tool_calls(self, tool_calls: list[dict[str, Any]]) -> None:
        """逐条执行工具调用，并把结果以 tool 消息回传模型。"""
        for tc in tool_calls:
            func = tc["function"]
            result = self._registry.dispatch(func["name"], func.get("arguments", ""))
            logger.debug("工具 %s -> %s", func["name"], result[:200])
            self._messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                }
            )
