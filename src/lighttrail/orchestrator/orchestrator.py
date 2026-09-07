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
from lighttrail.tools.astronomy import _parse_date  # noqa: F401  日期校验辅助（供 reverse 采集）

logger = logging.getLogger("lighttrail.orchestrator")

# 反推采集默认坐标（上海）；E6-3 favorite_spots 落地后按档案精确定位
_DEFAULT_LAT = 31.23
_DEFAULT_LON = 121.47


def _default_photo_analyze() -> Callable[[str, str], dict]:
    """默认照片分析绑定（延迟 import tools.photo_analysis；测试注入 Fake）。"""
    from lighttrail.tools import photo_analysis

    def analyze(image_path: str, focus: str) -> dict:
        return photo_analysis.analyze_photo(image_path, focus=focus)

    return analyze


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
    """候选机位：档案常去地点（无坐标时按默认坐标近似偏移兜底）。"""
    sites: list[dict[str, Any]] = []
    if memory is not None:
        for index, name in enumerate(memory.profile.common_locations):
            sites.append(
                {"名称": name, "纬度": _DEFAULT_LAT + index * 0.01, "经度": _DEFAULT_LON + index * 0.01, "题材": ""}
            )
    if not sites:
        sites.append({"名称": "默认机位（上海）", "纬度": _DEFAULT_LAT, "经度": _DEFAULT_LON, "题材": ""})
    return sites


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
  "time_window": "建议到场时间窗口",
  "locations": [{"name": "机位", "reason": "为何符合参考图特征"}],
  "params": [{"name": "参数", "value": "值", "reason": "理由"}],
  "alternatives": ["备选方案"],
  "degraded": ""
}"""

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
        photo_analyze: Callable[[str, str], dict] | None = None,
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
            photo_analyze=photo_analyze or _default_photo_analyze(),
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
        import lighttrail.tools.photo_analysis as photo

        self._recorder.record_step("反推_多模态", input_summary=image_path[:80], output_summary="")
        reverse = photo.reverse_engineer_photo(image_path, note=note, equipment=equipment)
        date_iso, data = self._collect_candidate_day()
        self._recorder.record_step(
            "反推_候选日", input_summary=date_iso, output_summary=json.dumps(data, ensure_ascii=False)[:120]
        )
        prompt = _build_reverse_plan_prompt(reverse, date_iso, data)
        self._recorder.record_step("反推_综合", input_summary=prompt[:80], output_summary="")
        return parse_with_retry(DecisionCard, lambda p: self._agent.reason(p, system=""), prompt)

    def _collect_candidate_day(self) -> tuple[str, dict[str, Any]]:
        """为复刻选候选日：未来 3 天取平均云量最低（通透优先）作为基准日并采集数据。"""
        lat, lon = _DEFAULT_LAT, _DEFAULT_LON  # 反推阶段默认坐标，档案精确定位在 E6-3 接 favorite_spots
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
