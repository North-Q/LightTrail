"""Agent 对外门面：组合 ReAct 循环、上下文构建与模型路由。

设计要点（架构 v2.0）：
- 循环 / 上下文 / 路由分别收敛到 loop.py、context.py、llm/router.py，
  本模块只做组装，`Agent.run()` 公开签名与行为保持不变；
- reason() 提供纯推理透传通道（E4-3 落地前为一次不带工具的单轮调用）。
"""

from __future__ import annotations

from typing import Any

from lighttrail.agent.context import ContextBuilder
from lighttrail.agent.loop import MAX_TOOL_ROUNDS, ReActLoop
from lighttrail.agent.tools import ToolRegistry
from lighttrail.llm.client import ChatClient
from lighttrail.llm.router import ModelRouter

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
    """带消息历史与工具调用能力的 Agent 门面。"""

    def __init__(
        self,
        client: ChatClient,
        registry: ToolRegistry,
        *,
        model: str | None = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        max_tool_rounds: int = MAX_TOOL_ROUNDS,
        router: ModelRouter | None = None,
    ) -> None:
        self._client = client
        self._router = router or ModelRouter()
        self._loop = ReActLoop(
            client,
            registry,
            model=model,
            max_tool_rounds=max_tool_rounds,
            context=ContextBuilder(),
        )
        self._system_prompt = system_prompt
        self._messages: list[dict[str, Any]] = []

    # ------ 对外接口 ------
    def run(self, user_input: str) -> str:
        """处理一条用户输入，返回最终文本回复（多轮历史自动累积）。"""
        self._messages.append({"role": "user", "content": user_input})
        try:
            return self._loop.run(self._messages, system_prompt=self._system_prompt)
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