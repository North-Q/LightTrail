"""四管线（E5-1）：灵感 / 规划 / 临场 / 复盘。每管线 = 确定性步骤序列。

设计要点（架构 v2.0 §2.1 / §2.2，E5 落地）：
- 数据采集用 `registry.dispatch` 直调（非 ReAct 轮次），每步记 TraceRecorder；
- 评分是**代码化确定性规则**（不让模型自评，与 E2-2 置信度规则同一哲学）；
- LLM 只出现在两头：意图理解（默认模型，强约束 JSON）与末端综合
  （深推理 reason 通道），中间全部确定性；
- 管线失败由上层 Orchestrator 捕获降级 ReAct 自由对话。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from lighttrail.contracts.context import RequestContext
from lighttrail.contracts.models import DecisionCard, Intent
from lighttrail.infra.confidence import confidence_detail
from lighttrail.infra.quota import QuotaLedger
from lighttrail.infra.trace import Recorder, null_trace
from lighttrail.infra.validation import parse_with_retry
from lighttrail.llm.router import ModelRouter, RouteIntent
from lighttrail.memory import MemoryManager
from lighttrail.orchestrator.context import PipelineContext

logger = logging.getLogger("lighttrail.orchestrator.pipelines")

# 默认时区（与工具层 tz_offset 默认值一致）
_LOCAL_TZ = timezone(timedelta(hours=8))


@dataclass
class PipelineEnv:
    """管线运行环境（由 Orchestrator 注入，测试可替换为 Fake）。"""

    dispatch: Callable[[str, str], str]
    reason: Callable[[str, str], str]  # (prompt, system) -> 文本
    memory: MemoryManager | None = None
    recorder: Recorder = field(default_factory=lambda: null_trace)
    router: ModelRouter | None = None
    photo_analyze: Callable[[str, str], dict[str, Any]] | None = None
    quota: QuotaLedger | None = None


# ------ 确定性工具 ------
def _target_date(time_hint: str) -> str:
    """时间提示 → 目标日期（MVP：明天/周末 → 明日，其余今天）；「今晚」→ 今天。"""
    today = datetime.now(_LOCAL_TZ).date()
    hint = time_hint or ""
    if any(keyword in hint for keyword in ("明天", "后天", "周末", "周六", "周日")):
        return (today + timedelta(days=1)).isoformat()
    return today.isoformat()


def _candidate_sites(memory: MemoryManager | None) -> list[dict[str, Any]]:
    """从档案的真实 favorite_spots 返回候选机位；空档案不编造坐标。"""
    if memory is None:
        return []
    return [
        {"名称": item["name"], "纬度": item["latitude"], "经度": item["longitude"], "题材": item["subject"]}
        for item in memory.profile.valid_favorite_spots()
    ]


def _missing_site_card() -> DecisionCard:
    """没有可用真实机位时诚实降级，避免以占位坐标调用外部数据源。"""
    return DecisionCard(
        conclusion="请先在档案中录入至少一个带经纬度的常用机位，再生成拍摄决策。",
        evidence=[],
        confidence="low",
        verdict="wait",
        degraded="missing_favorite_spot",
    )


def _collection_steps(subject_type: str, lat: float, lon: float, date_iso: str) -> list[tuple[str, str]]:
    """题材 → 数据采集步骤（工具名, 参数 JSON）。"""
    subject = subject_type or ""
    if subject in ("星空", "银河", "星轨"):
        return [
            ("moon_phase", json.dumps({"date": date_iso}, ensure_ascii=False)),
            (
                "moon_events",
                json.dumps({"latitude": lat, "longitude": lon, "date": date_iso}, ensure_ascii=False),
            ),
            (
                "galaxy_visibility",
                json.dumps({"latitude": lat, "longitude": lon, "date": date_iso}, ensure_ascii=False),
            ),
            (
                "weather_forecast",
                json.dumps({"latitude": lat, "longitude": lon, "days": 3, "tz_offset": "+08:00"}, ensure_ascii=False),
            ),
        ]
    if subject in ("日出", "朝霞", "日落", "晚霞", "火烧云", "蓝调", "黄金时刻"):
        return [
            (
                "sun_times",
                json.dumps({"latitude": lat, "longitude": lon, "date": date_iso}, ensure_ascii=False),
            ),
            (
                "sunset_glow_score",
                json.dumps({"latitude": lat, "longitude": lon, "date": date_iso}, ensure_ascii=False),
            ),
            (
                "weather_forecast",
                json.dumps({"latitude": lat, "longitude": lon, "days": 3, "tz_offset": "+08:00"}, ensure_ascii=False),
            ),
        ]
    return [
        (
            "weather_forecast",
            json.dumps({"latitude": lat, "longitude": lon, "days": 3, "tz_offset": "+08:00"}, ensure_ascii=False),
        )
    ]


def _planning_steps(subject_type: str, lat: float, lon: float, date_iso: str) -> list[tuple[str, str]]:
    """规划管线按题材采集，并把天气窗口扩至七天。"""
    steps = _collection_steps(subject_type, lat, lon, date_iso)
    return [
        (
            name,
            json.dumps({**json.loads(args), "days": 7}, ensure_ascii=False)
            if name == "weather_forecast"
            else args,
        )
        for name, args in steps
    ]


def _match_candidate_sites(
    env: PipelineEnv, sites: list[dict[str, Any]], date_iso: str
) -> dict[str, Any]:
    """调用确定性机位匹配，并把异常收敛为可解释的降级数据。"""
    args = json.dumps({"sites": sites, "date": date_iso, "tz_offset": "+08:00"}, ensure_ascii=False)
    env.recorder.record_step("机位匹配", input_summary=args[:80], output_summary="")
    try:
        raw = env.dispatch("match_sites", args)
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"error": "match_sites 返回格式非法"}
    except Exception as exc:  # noqa: BLE001 - 匹配失败不阻断其他数据源
        logger.warning("机位匹配失败：%s", exc)
        return {"error": f"{type(exc).__name__}: {exc}"}


async def _collect_async(
    env: PipelineEnv, ctx: PipelineContext, steps: list[tuple[str, str]]
) -> dict[str, Any]:
    """并发采集：每源一个 task（`asyncio.TaskGroup`），单源失败降级为 {"error": ...}。

    设计要点（B3-3，v4 §3 D5 配套①）：
    - 四管线采集源（天气 / 天文 / 机位 / 光污染）彼此无依赖，顺序 for 循环纯在浪费延迟；
      改为并发后采集阶段延迟由「各源之和」降为「最慢一源」；
    - 取数并发与 LLM 并发是两条独立边界：`env.dispatch` 走 `asyncio.to_thread`（工具内部是
      阻塞 HTTP / 计算），**不占用** LLM 信号量；
    - 单源失败不改整体结果形态：该源写 `{"error": ...}`（与旧实现的 `{"error": raw[:120]}`
      语义一致），其余源照常进卡片，管线不被拖垮。

    Args:
        env: 管线运行环境（dispatch 为同步直调）。
        ctx: 管线上下文（未使用，保留签名一致性）。
        steps: (工具名, 参数 JSON) 列表。

    Returns:
        {工具名: 解析后的结果 dict}；单个源失败时其值为 {"error": ...}。
    """
    data: dict[str, Any] = {}

    async def _run_one(tool: str, args: str) -> None:
        env.recorder.record_step(f"采集_{tool}", input_summary=args[:80], output_summary="")
        try:
            raw = await asyncio.to_thread(env.dispatch, tool, args)
        except Exception as exc:  # noqa: BLE001 - 单源失败降级，不拖垮管线
            logger.warning("采集源 %s 失败，降级为 error：%s", tool, exc)
            data[tool] = {"error": f"{type(exc).__name__}: {exc}"}
            return
        try:
            data[tool] = json.loads(raw)
        except ValueError:
            data[tool] = {"error": str(raw)[:120]}

    async with asyncio.TaskGroup() as group:
        for tool, args in steps:
            group.create_task(_run_one(tool, args))
    return data


def _collect(env: PipelineEnv, ctx: PipelineContext, steps: list[tuple[str, str]]) -> dict[str, Any]:
    """同步门面：在独立事件循环里跑并发采集（管线在 worker 线程中同步调用）。

    Args:
        env: 管线运行环境。
        ctx: 管线上下文。
        steps: (工具名, 参数 JSON) 列表。

    Returns:
        与 `_collect_async` 相同的采集结果。
    """
    return asyncio.run(_collect_async(env, ctx, steps))


def _score(data: dict[str, Any]) -> dict[str, Any]:
    """代码化评分（确定性规则，替代模型自评）。

    注意：键名与工具返回保持对齐（评分键实际为「评分（0-100）」，月相为
    「月相名称」+「月光影响建议」）——取键用前缀匹配，避免工具改键名即静默失效。
    """
    scores: dict[str, Any] = {}
    glow = data.get("sunset_glow_score")
    if isinstance(glow, dict):
        score_value = _first_value_of(glow, ("评分（0-100）",))
        if score_value is not None:
            scores["火烧云评分"] = score_value
        level = glow.get("等级", "")
        if level:
            scores["火烧云等级"] = level.split("（")[0]
    moon = data.get("moon_phase")
    if isinstance(moon, dict):
        phase = moon.get("月相名称", "")
        if phase:
            scores["月相"] = phase
        advice = moon.get("月光影响建议", "")
        if advice:
            scores["月光影响建议"] = advice
    return scores


def _first_value_of(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    """按键名列表返回首个存在的值（None 表示均不存在）。"""
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


_DECISION_CARD_INSTRUCTION = """请基于以上数据输出一份决策卡片，严格只输出如下 JSON 对象（不要任何其他文字）：

{
  "conclusion": "一句话结论：该不该去、几点去、去哪、带什么",
  "evidence": [{"tool": "工具名", "field": "字段", "confidence": "high|medium|low", "note": "依据说明"}],
  "confidence": "high|medium|low",
  "verdict": "go|wait|risk（go=值得出门按窗口去；wait=先观察临近数据；risk=不建议专程）",
  "time_window": "建议时间窗口",
  "window_start": "ISO 8601 含时区的窗口开始；无法确定则空字符串",
  "window_end": "ISO 8601 含时区的窗口结束；无法确定则空字符串",
  "locations": [{"name": "机位", "reason": "理由"}],
  "params": [{"name": "参数", "value": "值", "reason": "理由"}],
  "alternatives": ["备选方案1", "备选方案2"],
  "degraded": ""
}"""


def _build_synthesis_prompt(ctx: PipelineContext, env: PipelineEnv) -> str:
    """组装综合 prompt：意图 + 数据 + 评分 + 记忆（第④层对齐）。"""
    lines = [f"用户请求：{ctx.user_request}"]
    intent = ctx.intent
    if intent is not None:
        lines.append(f"规范意图：题材={intent.subject_type or '未指定'}，地点={intent.location or '未指定'}，时间={intent.time_hint or '未指定'}")
    memory_text = ""
    if env.memory is not None:
        memory_text = "\n".join(
            block.text
            for block in env.memory.build_injections(RequestContext(), intent.subject_type if intent else "")
        )
    if memory_text:
        lines.append(f"用户记忆：\n{memory_text}")
    if ctx.data:
        lines.append("采集数据（JSON）：\n" + json.dumps(ctx.data, ensure_ascii=False, default=str)[:3000])
    if ctx.scores:
        lines.append("代码化评分：" + json.dumps(ctx.scores, ensure_ascii=False))
    return "\n\n".join(lines) + "\n\n" + _DECISION_CARD_INSTRUCTION


def finalize_card(card: DecisionCard) -> DecisionCard:
    """补全卡片的规则推导字段（M2：置信度不让模型自评）。

    当前只补 `confidence_detail`（主值 + 区间 + 依据构成，规则见 infra/confidence.py）——
    前端铁律①直接渲染该字段，不再自算区间（B4-3）。

    Args:
        card: 管线/反推产出的决策卡。

    Returns:
        同一张卡（就地补全后返回，便于链式调用）。
    """
    card.confidence_detail = confidence_detail(card.confidence, card.evidence)
    return card


class Pipeline:
    """管线基类：name 供路由；run 收口公共后处理，子类实现 _run 产出卡片。"""

    name = ""
    description = ""

    def run(self, ctx: PipelineContext, env: PipelineEnv) -> DecisionCard:
        """执行管线，并统一补全规则推导字段（置信度明细）。"""
        if env.quota is not None and not env.quota.check():
            env.recorder.record_step("配额_拒绝", input_summary=self.name, output_summary="quota_exhausted")
            return finalize_card(
                DecisionCard(
                    conclusion="当前配额已达上限，本次未发起模型调用。",
                    evidence=[],
                    confidence="low",
                    verdict="wait",
                    degraded="quota_exhausted",
                )
            )
        if env.quota is not None and env.router is not None:
            warning = env.quota.degrade(RouteIntent.DEEP_REASONING, env.router)
            if warning is not None:
                env.recorder.record_step("配额_降级", input_summary=self.name, output_summary=warning[:120])
                return finalize_card(
                    DecisionCard(
                        conclusion="当前配额水位较高，已降级为不发起深推理的保守建议。",
                        evidence=[],
                        confidence="low",
                        verdict="wait",
                        degraded="quota_high_water",
                    )
                )
        return finalize_card(self._run(ctx, env))

    def _run(self, ctx: PipelineContext, env: PipelineEnv) -> DecisionCard:
        """子类实现：执行确定步骤序列并产出卡片。"""
        raise NotImplementedError


class InspirationPipeline(Pipeline):
    """灵感管线（D1.1 一句话出方案）：意图 → 采集 → 评分 → 深推理综合 → 卡片。"""

    name = "inspiration"
    description = "一句话出方案（灵感）"

    def _run(self, ctx: PipelineContext, env: PipelineEnv) -> DecisionCard:
        intent = ctx.intent or Intent(subject_type="")
        sites = _candidate_sites(env.memory)
        if env.memory is not None and not sites:
            return _missing_site_card()
        if not sites:
            return _missing_site_card()
        site = sites[0]
        date_iso = _target_date(intent.time_hint)
        steps = _collection_steps(intent.subject_type, site["纬度"], site["经度"], date_iso)
        ctx.data = _collect(env, ctx, steps)
        ctx.data["候选机位"] = sites
        ctx.scores = _score(ctx.data)
        prompt = _build_synthesis_prompt(ctx, env)
        env.recorder.record_step("综合_reason", input_summary=prompt[:80], output_summary="")
        ctx.card = parse_with_retry(DecisionCard, lambda p: env.reason(p, ""), prompt)
        env.recorder.record_step("卡片_产出", input_summary=ctx.card.conclusion, output_summary="")
        return ctx.card


class PlanningPipeline(Pipeline):
    """规划管线（D2.3 计划生成）：天气 7 天 + 月相 + 机位匹配 → 编排卡片。"""

    name = "planning"
    description = "多机位拍摄计划编排（规划）"

    def _run(self, ctx: PipelineContext, env: PipelineEnv) -> DecisionCard:
        intent = ctx.intent or Intent(subject_type="")
        sites = _candidate_sites(env.memory)
        if not sites:
            return _missing_site_card()
        site = sites[0]
        date_iso = _target_date(intent.time_hint)
        steps = _planning_steps(intent.subject_type, site["纬度"], site["经度"], date_iso)
        ctx.data = _collect(env, ctx, steps)
        ctx.data["候选机位"] = sites
        ctx.data["match_sites"] = _match_candidate_sites(env, sites, date_iso)
        ctx.scores = _score(ctx.data)
        prompt = _build_synthesis_prompt(ctx, env)
        ctx.card = parse_with_retry(DecisionCard, lambda p: env.reason(p, ""), prompt)
        return ctx.card


class LiveDecisionPipeline(Pipeline):
    """临场决策管线（D3.1 火烧云赌注等）：当前评分 → 去/等/放弃卡片。"""

    name = "live"
    description = "临场赌注决策（临场）"

    def _run(self, ctx: PipelineContext, env: PipelineEnv) -> DecisionCard:
        if ctx.intent is None:
            ctx.intent = Intent(subject_type="火烧云", time_hint="今晚")
        today = datetime.now(_LOCAL_TZ).date().isoformat()
        sites = _candidate_sites(env.memory)
        if not sites:
            return _missing_site_card()
        site = sites[0]
        steps = _collection_steps(ctx.intent.subject_type, site["纬度"], site["经度"], today)
        ctx.data = _collect(env, ctx, steps)
        ctx.data["候选机位"] = sites
        ctx.data["match_sites"] = _match_candidate_sites(env, sites, today)
        ctx.scores = _score(ctx.data)
        prompt = _build_synthesis_prompt(ctx, env)
        ctx.card = parse_with_retry(DecisionCard, lambda p: env.reason(p, ""), prompt)
        return ctx.card


class ReviewPipeline(Pipeline):
    """复盘管线（D4 闭环，E6-4）：照片分析 + 与计划对账差异 + reason 复盘卡。

    评分键对齐教训（E5 真实联调）：EXIF 与计划参数一律按中文键（光圈/快门/ISO）
    宽松数值对比，任何一方缺失即跳过该项，不让键名错位导致静默失真。
    """

    name = "review"
    description = "照片复盘（EXIF + 多模态 → 处方，与历史计划对账）"

    def _run(self, ctx: PipelineContext, env: PipelineEnv) -> DecisionCard:
        image_path = str(ctx.data.get("image_path") or "")
        if not image_path:
            ctx.card = DecisionCard(
                conclusion="请提供照片路径后再复盘（image_path 缺失）。",
                evidence=[],
                confidence="low",
                degraded="review_missing_image",
            )
            env.recorder.record_step("复盘_缺图", input_summary=ctx.user_request, output_summary="")
            return ctx.card
        focus = str(ctx.data.get("focus") or "")
        if env.photo_analyze is None:
            raise RuntimeError("复盘管线未装配照片分析工具")
        analyzer = env.photo_analyze
        env.recorder.record_step("复盘_照片分析", input_summary=image_path[:80], output_summary=focus[:60])
        report = analyzer(image_path, focus)
        exif = report.get("已识别EXIF") if isinstance(report, dict) else {}
        diffs = _diff_exif_vs_plan(exif, ctx.data.get("plan_params"))
        ctx.scores = {"实拍参数": exif, "对账差异": diffs}
        prompt = _build_review_prompt(report, exif, diffs)
        env.recorder.record_step("复盘_综合", input_summary=prompt[:80], output_summary="")
        ctx.card = parse_with_retry(DecisionCard, lambda p: env.reason(p, ""), prompt)
        env.recorder.record_step("复盘_卡片", input_summary=ctx.card.conclusion, output_summary="")
        return ctx.card


def _diff_exif_vs_plan(exif: dict[str, Any], plan_params: Any) -> list[str]:
    """把 EXIF 实拍参数与计划参数做宽松数值对账，返回差异行（键名一致化）。"""
    diffs: list[str] = []
    if not isinstance(plan_params, list):
        return diffs
    plan_map: dict[str, str] = {}
    for item in plan_params:
        if not isinstance(item, dict):
            continue
        name = str(item.get("参数") or item.get("name") or "")
        value = str(item.get("值") or item.get("value") or "")
        if name:
            plan_map[name] = value
    for label in ("光圈", "快门", "ISO"):
        actual = exif.get(label)
        if not actual:
            continue
        planned = next((value for name, value in plan_map.items() if label in name), None)
        if planned is None or _values_equivalent(planned, actual):
            continue
        diffs.append(f"计划{label} {planned}，实拍 {actual}")
    return diffs


def _values_equivalent(left: str, right: str) -> bool:
    """数值宽松相等（忽略单位/前缀，如 f/8 vs 8、1/125s vs 1/125）。"""
    import re

    numbers = lambda text: re.findall(r"\d+\.?\d*", str(text))
    return bool(numbers(left) and numbers(left) == numbers(right))


def _build_review_prompt(report: dict[str, Any], exif: dict[str, Any], diffs: list[str]) -> str:
    """组装复盘综合 prompt（分析 + 实拍参数 + 对账差异 → DecisionCard JSON）。"""
    lines = [
        "照片复盘分析结果：",
        json.dumps(report, ensure_ascii=False, default=str)[:2000],
        f"实拍 EXIF 参数：{json.dumps(exif, ensure_ascii=False, default=str)}",
    ]
    if diffs:
        lines.append("与历史计划的参数差异：" + "；".join(diffs))
    else:
        lines.append("与历史计划无参数差异（或计划缺失，仅按实拍复盘）。")
    lines.append("conclusion 须写复盘结论与下次处方；params 给下次拍摄参数建议。")
    return "\n\n".join(lines) + "\n\n" + _DECISION_CARD_INSTRUCTION


# 管线注册表（路由按 intent.mode 选择）
PIPELINES: dict[str, Pipeline] = {
    pipeline.name: pipeline
    for pipeline in (InspirationPipeline(), PlanningPipeline(), LiveDecisionPipeline(), ReviewPipeline())
}


def default_mode(subject_type: str) -> str:
    """题材 → 缺省管线模式（意图未给 mode 时用）。"""
    if subject_type in ("火烧云", "晚霞", "日落"):
        return "live"
    return "inspiration"
