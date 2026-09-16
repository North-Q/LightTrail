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

import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from lighttrail.application.router import RuleIntentRouter
from lighttrail.contracts.models import DecisionCard, Intent, RoutingDecision
from lighttrail.infra.trace import Recorder, null_trace
from lighttrail.infra.validation import parse_with_retry
from lighttrail.llm.router import ModelRouter
from lighttrail.memory import MemoryManager
from lighttrail.orchestrator.context import PipelineContext
from lighttrail.orchestrator.pipelines import PIPELINES, PipelineEnv, default_mode, finalize_card
from lighttrail.runtime.agent import AgentRuntime
from lighttrail.runtime.planner import Planner
from lighttrail.runtime.registry import ToolRegistry

logger = logging.getLogger("lighttrail.orchestrator")

# 意图解析器系统指令（迁移期从 parse_intent 内联抽出，两条路径共用）
_INTENT_SYSTEM = "你是 LightTrail 摄影决策引擎的意图解析器。"


def _default_photo_analyze(registry: ToolRegistry, recorder: Recorder) -> Callable[[str, str], dict]:
    """默认照片分析绑定：经注册表调用已注入依赖的领域工具。"""
    def analyze(image_path: str, focus: str) -> dict:
        raw = registry.dispatch(
            "analyze_photo", json.dumps({"image_path": image_path, "focus": focus}), recorder=recorder
        )
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise TypeError("照片分析工具返回格式非法")
        return result

    return analyze


def _default_photo_reverse(
    registry: ToolRegistry, recorder: Recorder
) -> Callable[[str, str, str], dict]:
    """默认照片反推绑定：经注册表调用已注入依赖的领域工具。"""
    def reverse(image_path: str, note: str, equipment: str) -> dict:
        raw = registry.dispatch(
            "reverse_engineer_photo",
            json.dumps({"image_path": image_path, "note": note, "equipment": equipment}),
            recorder=recorder,
        )
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise TypeError("照片反推工具返回格式非法")
        return result

    return reverse


def _extract_plan_params(plan_reference: str) -> list[dict[str, Any]]:
    """从历史计划文本中提取参数列表（DecisionCard JSON 的 params；非法返回空）。"""
    if not plan_reference:
        return []
    try:
        parsed = json.loads(plan_reference)
    except ValueError:
        return []
    params = parsed.get("params") if isinstance(parsed, dict) else None
    return params if isinstance(params, list) else []


def _candidate_sites(memory: MemoryManager | None) -> list[dict[str, Any]]:
    """从档案真实 favorite_spots 读取候选机位；无记录时返回空。"""
    if memory is None:
        return []
    return [
        {"名称": item["name"], "纬度": item["latitude"], "经度": item["longitude"], "题材": item["subject"]}
        for item in memory.profile.valid_favorite_spots()
    ]


def _build_reverse_plan_prompt(reverse: dict[str, Any], date_iso: str, data: dict[str, Any]) -> str:
    """组装复刻计划综合 prompt：反推结论 + 候选日数据 + 机位，要求输出 DecisionCard JSON。"""
    lines = [
        "用户在照片反推中想要复刻一张参考图，反推结论如下：",
        json.dumps(reverse, ensure_ascii=False, default=str)[:2000],
        f"候选参考日期（未来 3 天云量最低）：{date_iso}",
        "候选日数据（JSON）：" + json.dumps(data, ensure_ascii=False, default=str)[:2000],
        "请给复刻计划决策卡片：conclusion 须回答『去哪 / 什么时候去 / 怎么拍』三要素。",
    ]
    return "\n\n".join(lines) + "\n\n" + _REVERSE_CARD_INSTRUCTION

_REVERSE_CARD_INSTRUCTION = """请只输出如下 JSON 对象（不要任何其他文字）：

{
  "conclusion": "复刻计划一句话（去哪 + 什么时候去 + 怎么拍）",
  "evidence": [{"tool": "reverse_engineer_photo", "field": "复刻计划", "confidence": "medium", "note": "参考图反推"}],
  "confidence": "high|medium|low",
  "verdict": "go|wait|risk（go=值得去；wait=先观察；risk=不建议专程）",
  "time_window": "建议到场时间窗口",
  "locations": [{"name": "机位", "reason": "为何符合参考图特征"}],
  "params": [{"name": "参数", "value": "值", "reason": "理由"}],
  "alternatives": ["备选方案"],
  "degraded": ""
}"""

_ROUTING_INSTRUCTION = """规则未命中，请把用户的一句话请求解析为规范意图并评估判定可信度，只输出如下 JSON（不要任何其他文字）：

{
  "intent": {
    "subject_type": "题材：星空/银河/日出/日落/朝霞/晚霞/火烧云/蓝调/黄金时刻/夜景/城市风光/其他",
    "location": "目标地点（用户明确给出时填写，否则空串）",
    "time_hint": "时间提示原文（如：这周末 / 明天傍晚 / 今晚；未知空串）",
    "mode": "inspiration(灵感出方案) | planning(计划编排) | live(临场决策) | review(照片复盘)"
  },
  "confidence": 0.0,
  "reason": "不超过 30 字的判定依据"
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
        registry: ToolRegistry,
        agent: AgentRuntime,
        *,
        memory: MemoryManager | None = None,
        recorder: Recorder | None = None,
        router: ModelRouter | None = None,
        dispatch: Callable[[str, str], str] | None = None,
        photo_analyze: Callable[[str, str], dict] | None = None,
        photo_reverse: Callable[[str, str, str], dict] | None = None,
        intent_router: RuleIntentRouter | None = None,
        routing_confidence_threshold: float = 0.6,
        planner: Planner | None = None,
    ) -> None:
        """初始化编排器。

        Args:
            registry: 工具注册表（数据采集直调）。
            agent: LLM 交互面——B2-7 起传 `AgentRuntime`（PydanticAI）；
                迁移期亦接受旧 `Agent` 门面（TODO(B2-7): 测试全部换装后删旧分支）。
            memory: 记忆管理器（档案/事件/语义注入）。
            recorder: 可观测性记录器。
            router: 模型路由（缺省取 agent 内部路由）。
            dispatch: 数据采集函数（工具名, 参数 JSON）→ 结果 JSON；缺省用注册表
                直调并注入 recorder；测试可传 Fake 数据源（不触网、不依赖 API Key）。
            photo_analyze: 照片分析绑定（缺省经注册表调用已装配工具）。
            photo_reverse: 照片反推绑定（同上；测试可注入 Fake）。
            intent_router: 规则优先意图路由器；缺省使用项目规则表。
            routing_confidence_threshold: LLM 路由结果的最低接纳置信度（0-1）。
        """
        self._registry = registry
        self._runtime = agent
        self._recorder: Recorder = recorder or null_trace
        self._router = router or getattr(agent, "router", None)
        self._env = PipelineEnv(
            dispatch=dispatch
            or (lambda name, args: self._registry.dispatch(name, args, recorder=self._recorder)),
            reason=self._reason_text,
            memory=memory,
            recorder=self._recorder,
            router=self._router,
            photo_analyze=photo_analyze or _default_photo_analyze(self._registry, self._recorder),
        )
        self._photo_reverse = photo_reverse or _default_photo_reverse(self._registry, self._recorder)
        self._intent_router = intent_router or RuleIntentRouter()
        self._routing_confidence_threshold = routing_confidence_threshold
        self._planner = planner or Planner(
            self._registry,
            lambda prompt, system: self._runtime.complete(prompt, system=system),
            self._reason_text,
            recorder=self._recorder,
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
        unsupported_reply = self._intent_router.unsupported_reply(user_request)
        if unsupported_reply is not None:
            self._recorder.record_step(
                "意图路由_超工具集",
                input_summary=user_request[:80],
                output_summary="显式诚实告知",
            )
            return unsupported_reply
        try:
            if self._intent_router.needs_controlled_plan(user_request):
                self._recorder.record_step("意图路由_Planner", input_summary=user_request[:80], output_summary="受控规划")
                card = self._planner.execute(self._planner.make_plan(user_request))
            else:
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

    def review(self, image_path: str, *, focus: str = "", plan_reference: str = "") -> str:
        """D4 复盘：照片 → EXIF+画面分析 → 与历史计划对账 → 复盘卡（渲染文本）。

        Args:
            image_path: 照片路径。
            focus: 分析重点。
            plan_reference: 历史计划（DecisionCard JSON 或文本；可空）。

        Returns:
            复盘卡片文本；失败自动降级自由对话。
        """
        try:
            card = self.run_review(image_path, focus=focus, plan_reference=plan_reference)
            self.last_card = card
            return _render_card(card)
        except Exception as exc:  # noqa: BLE001 - 复盘链路异常统一降级
            logger.warning("复盘失败，降级 ReAct：%s", exc)
            return self._fallback(f"帮我复盘这张照片（{image_path}）{focus}")

    def run_review(self, image_path: str, *, focus: str = "", plan_reference: str = "") -> DecisionCard:
        """执行复盘管线：照片分析 + 对账 + reason 复盘卡。

        Args:
            image_path: 照片路径。
            focus: 分析重点。
            plan_reference: 历史计划（DecisionCard JSON 或文本）。

        Returns:
            复盘 DecisionCard。

        Raises:
            照片分析 / rSchema 校验失败向上抛（由 review 捕获降级）。
        """
        plan_params = _extract_plan_params(plan_reference)
        ctx = PipelineContext(
            user_request=f"复盘照片：{image_path}",
            data={"image_path": image_path, "focus": focus, "plan_params": plan_params},
        )
        return PIPELINES["review"].run(ctx, self._env)

    def reverse_plan(
        self,
        image_path: str,
        *,
        note: str = "",
        equipment: str = "",
    ) -> str:
        """D1.2 照片反推：参考图 → 复刻计划（渲染文本）；失败自动降级自由对话。

        Args:
            image_path: 参考图路径。
            note: 用户补充约束（时间/地点/器材等）。
            equipment: 器材覆盖（缺省读档案）。

        Returns:
            复刻计划卡片文本；失败时返回自由对话回复（降级标注）。
        """
        try:
            card = self.run_reverse(image_path, note=note, equipment=equipment)
            self.last_card = card
            return _render_card(card)
        except Exception as exc:  # noqa: BLE001 - 反推链路任何异常统一降级
            logger.warning("照片反推失败，降级 ReAct：%s", exc)
            return self._fallback(f"这张参考图我想复刻（{image_path}）{note}")

    def run_reverse(
        self,
        image_path: str,
        *,
        note: str = "",
        equipment: str = "",
    ) -> DecisionCard:
        """执行照片反推：多模态识别 → 候选日数据 → 深推理综合复刻计划卡。

        Args:
            image_path: 参考图路径。
            note: 用户补充约束。
            equipment: 器材覆盖。

        Returns:
            复刻计划 DecisionCard。

        Raises:
            多模态/数据采集/rSchema 校验失败向上抛（由 reverse_plan 捕获降级）。
        """
        self._recorder.record_step("反推_多模态", input_summary=image_path[:80], output_summary="")
        reverse = self._photo_reverse(image_path, note, equipment)
        if not _candidate_sites(self._env.memory):
            return finalize_card(
                DecisionCard(
                    conclusion="请先在档案中录入至少一个带经纬度的常用机位，再生成复刻计划。",
                    evidence=[],
                    confidence="low",
                    verdict="wait",
                    degraded="missing_favorite_spot",
                )
            )
        date_iso, data = self._collect_candidate_day()
        self._recorder.record_step(
            "反推_候选日", input_summary=date_iso, output_summary=json.dumps(data, ensure_ascii=False)[:120]
        )
        prompt = _build_reverse_plan_prompt(reverse, date_iso, data)
        self._recorder.record_step("反推_综合", input_summary=prompt[:80], output_summary="")
        return finalize_card(parse_with_retry(DecisionCard, lambda p: self._reason_text(p, ""), prompt))

    def _collect_candidate_day(self) -> tuple[str, dict[str, Any]]:
        """为复刻选候选日：未来 3 天取平均云量最低（通透优先）作为基准日并采集数据。"""
        sites = _candidate_sites(self._env.memory)
        if not sites:
            raise ValueError("缺少带经纬度的 favorite_spot")
        lat, lon = sites[0]["纬度"], sites[0]["经度"]
        weather_raw = self._env.dispatch(
            "weather_forecast",
            json.dumps({"latitude": lat, "longitude": lon, "days": 3, "tz_offset": "+08:00"}, ensure_ascii=False),
        )
        try:
            weather: dict[str, Any] = json.loads(weather_raw or "{}")
        except ValueError:
            weather = {}
        daily = weather.get("每日预报") or []
        valid = [d for d in daily if isinstance(d, dict) and d.get("平均云量（%）") is not None]
        best = min(valid, key=lambda d: int(d["平均云量（%）"])) if valid else None
        date_iso = str(best["日期"]) if best else datetime.now(timezone(timedelta(hours=8))).date().isoformat()
        data: dict[str, Any] = {"weather_forecast": weather}
        if best:
            data["sun_times"] = json.loads(
                self._env.dispatch(
                    "sun_times",
                    json.dumps({"latitude": lat, "longitude": lon, "date": date_iso}, ensure_ascii=False),
                )
                or "{}"
            )
        moon_raw = self._env.dispatch("moon_phase", json.dumps({"date": date_iso}, ensure_ascii=False))
        try:
            data["moon_phase"] = json.loads(moon_raw or "{}")
        except ValueError:
            data["moon_phase"] = {}
        data["候选机位"] = _candidate_sites(self._env.memory)
        return date_iso, data

    def parse_intent(self, user_request: str) -> Intent:
        """意图理解：规则优先，未命中时由默认模型输出可审计判定。

        Args:
            user_request: 用户请求。

        Returns:
            规范意图。

        Raises:
            SchemaError: 重试后仍无法解析（由 plan 降级）。
        """
        rule_intent = self._intent_router.route(user_request)
        if rule_intent is not None:
            self._recorder.record_step(
                "意图路由_规则",
                input_summary=user_request[:80],
                output_summary=f"{rule_intent.mode}/{rule_intent.subject_type}",
            )
            return rule_intent

        prompt = _ROUTING_INSTRUCTION.replace("__REQUEST__", user_request)
        self._recorder.record_step("意图路由_LLM", input_summary=user_request[:80], output_summary="")

        def _chat(prompt_text: str) -> str:
            return self._runtime.complete(prompt_text, system=_INTENT_SYSTEM)

        decision = parse_with_retry(RoutingDecision, _chat, prompt)
        self._recorder.record_step(
            "意图路由_LLM",
            input_summary=user_request[:80],
            output_summary=(
                f"{decision.intent.mode}/{decision.intent.subject_type}/"
                f"{decision.confidence:.2f}"
            ),
        )
        if decision.confidence < self._routing_confidence_threshold:
            raise ValueError(
                f"LLM 路由置信度 {decision.confidence:.2f} "
                f"低于阈值 {self._routing_confidence_threshold:.2f}"
            )
        return decision.intent

    # ------ 内部实现 ------
    def _reason_text(self, prompt: str, system: str) -> str:
        """深推理综合：走 runtime.reason（PydanticAI 深推理通道）。"""
        return self._runtime.reason(prompt, system=system)

    def _run_freeform(self, user_request: str) -> str:
        """降级自由对话（ReAct）：走 runtime.run。"""
        return self._runtime.run(user_request)

    def _fallback(self, user_request: str) -> str:
        """管线降级：携带卡片上下文转入自由对话（ReAct）。"""
        context_note = ""
        if self.last_card is not None:
            context_note = f"（背景：管线已产出部分方案「{self.last_card.conclusion}」，可在此基础上追问调整）"
        self._recorder.record_step("管线降级", input_summary=user_request[:80], output_summary=context_note[:80])
        return self._run_freeform(user_request + context_note)


__all__ = ["Orchestrator", "_render_card"]
