"""编排层（E5-1 / E5-3）：意图路由 + 四管线调度 + 失败降级 ReAct。

设计要点（架构 v2.0 §2.1 / §2.2，E5 落地）：
- 意图理解用默认模型一次调用（强约束 JSON + parse_with_retry 自愈），
  失败或管线异常统一降级到 Agent.run() 自由对话（原因标注「管线降级」）；
- 管线环境（PipelineEnv）注入 dispatch / reason / memory / recorder，
  测试可整体替换为 Fake 数据源 + FakeChatClient，不依赖真实 API Key；
- 平台中立性（ADR-002）：意图理解走 RouteIntent.DEFAULT（默认模型）与
  reason 走 RouteIntent.DEEP_REASONING（深推理模型），不写死任何模型品牌。
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from lighttrail.agent import Agent
from lighttrail.agent.tools import ToolRegistry
from lighttrail.infra.trace import Recorder, null_trace
from lighttrail.infra.validation import parse_with_retry
from lighttrail.llm.client import ChatClient
from lighttrail.llm.router import ModelRouter, RouteIntent
from lighttrail.memory import MemoryManager
from lighttrail.orchestrator.context import PipelineContext
from lighttrail.orchestrator.pipelines import PIPELINES, PipelineEnv, default_mode
from lighttrail.orchestrator.schemas import DecisionCard, Intent

logger = logging.getLogger("lighttrail.orchestrator")

_INTENT_INSTRUCTION = """请把用户的一句话请求解析为规范意图，只输出如下 JSON（不要任何其他文字）：

{
  "subject_type": "题材：星空/银河/日出/日落/朝霞/晚霞/火烧云/蓝调/黄金时刻/夜景/城市风光/其他",
  "location": "目标地点（用户明确给出时填写，否则空串）",
  "time_hint": "时间提示原文（如：这周末 / 明天傍晚 / 今晚；未知空串）",
  "mode": "inspiration(灵感出方案) | planning(计划编排) | live(临场决策) | review(照片复盘)"
}

用户请求：__REQUEST__"""


def _render_card(card: DecisionCard) -> str:
    """卡片 → 中文展示文本（CLI 输出用）。"""
    lines = ["## 拍摄方案（决策卡片）", f"- 结论：{card.conclusion}"]
    if card.time_window:
        lines.append(f"- 时间窗口：{card.time_window}")
    lines.append(f"- 置信度：{card.confidence}")
    if card.locations:
        lines.append("- 推荐机位：" + "；".join(f"{loc.name}（{loc.reason}）" for loc in card.locations))
    if card.params:
        lines.append("- 参数建议：" + "；".join(f"{p.name}={p.value}（{p.reason}）" for p in card.params))
    if card.evidence:
        lines.append("- 依据：" + "；".join(f"{e.tool}/{e.field} [置信度 {e.confidence}]" for e in card.evidence))
    if card.alternatives:
        lines.append("- 备选：" + "；".join(card.alternatives))
    if card.degraded:
        lines.append(f"- ⚠ 降级标注：{card.degraded}")
    return "\n".join(lines)


class Orchestrator:
    """管线调度器：一句话 → 意图 → 管线 → 卡片（失败降级自由对话）。"""

    def __init__(
        self,
        client: ChatClient,
        registry: ToolRegistry,
        agent: Agent,
        *,
        memory: MemoryManager | None = None,
        recorder: Recorder | None = None,
        router: ModelRouter | None = None,
        dispatch: Callable[[str, str], str] | None = None,
    ) -> None:
        """初始化编排器。

        Args:
            client: LLM 客户端（意图理解走默认模型）。
            registry: 工具注册表（数据采集直调）。
            agent: Agent 门面（降级 ReAct 通道 + 历史上下文）。
            memory: 记忆管理器（档案/事件/语义注入）。
            recorder: 可观测性记录器。
            router: 模型路由（缺省取 agent 内部路由）。
            dispatch: 数据采集函数（工具名, 参数 JSON）→ 结果 JSON；缺省用注册表
                直调并注入 recorder；测试可传 Fake 数据源（不触网、不依赖 API Key）。
        """
        self._client = client
        self._registry = registry
        self._agent = agent
        self._recorder: Recorder = recorder or null_trace
        self._router = router or agent.router  # 复用 Agent 的路由配置
        self._env = PipelineEnv(
            dispatch=dispatch
            or (lambda name, args: self._registry.dispatch(name, args, recorder=self._recorder)),
            reason=lambda prompt, system: self._agent.reason(prompt, system=system),
            memory=memory,
            recorder=self._recorder,
            router=self._router,
        )
        self.last_card: DecisionCard | None = None

    # ------ 对外接口 ------
    def plan(self, user_request: str) -> str:
        """E5-3 主入口：一句话出方案（文本渲染）；失败自动降级自由对话。

        Args:
            user_request: 用户的一句话请求。

        Returns:
            方案卡片文本；管线失败时返回自由对话回复（原因标注管线降级）。
        """
        try:
            card = self.run_pipeline(user_request)
            self.last_card = card
            return _render_card(card)
        except Exception as exc:  # noqa: BLE001 - 管线任何异常统一降级
            logger.warning("管线执行失败，降级 ReAct：%s", exc)
            return self._fallback(user_request)

    def run_pipeline(self, user_request: str, *, mode: str = "") -> DecisionCard:
        """执行一条决策管线。

        Args:
            user_request: 用户请求。
            mode: 强制管线模式；缺省由意图解析决定。

        Returns:
            决策卡片。

        Raises:
            意图解析/管线步骤/rSchema 校验失败向上抛（由 plan 捕获降级）。
        """
        intent = self.parse_intent(user_request)
        if mode:
            intent.mode = mode
        pipeline_name = intent.mode or default_mode(intent.subject_type)
        ctx = PipelineContext(user_request=user_request, intent=intent)
        pipeline = PIPELINES.get(pipeline_name) or PIPELINES[default_mode(intent.subject_type)]
        self._recorder.record_step(f"管线_{pipeline.name}", input_summary=user_request[:80], output_summary="启动")
        return pipeline.run(ctx, self._env)

    def parse_intent(self, user_request: str) -> Intent:
        """意图理解：默认模型强约束 JSON 解析（parse_with_retry 自愈）。

        Args:
            user_request: 用户请求。

        Returns:
            规范意图。

        Raises:
            SchemaError: 重试后仍无法解析（由 plan 降级）。
        """
        prompt = _INTENT_INSTRUCTION.replace("__REQUEST__", user_request)
        self._recorder.record_step("意图理解", input_summary=user_request[:80], output_summary="")

        def _chat(prompt_text: str) -> str:
            model = RouteIntent.DEFAULT.resolve(self._router)
            resp = self._client.chat(
                [{"role": "system", "content": "你是 LightTrail 摄影决策引擎的意图解析器。"}, {"role": "user", "content": prompt_text}],
                model=model,
                tools=None,
            )
            return resp.get("content", "")

        return parse_with_retry(Intent, _chat, prompt)

    # ------ 内部实现 ------
    def _fallback(self, user_request: str) -> str:
        """管线降级：携带卡片上下文转入自由对话（ReAct）。"""
        context_note = ""
        if self.last_card is not None:
            context_note = f"（背景：管线已产出部分方案「{self.last_card.conclusion}」，可在此基础上追问调整）"
        self._recorder.record_step("管线降级", input_summary=user_request[:80], output_summary=context_note[:80])
        return self._agent.run(user_request + context_note)


__all__ = ["Orchestrator", "_render_card"]
