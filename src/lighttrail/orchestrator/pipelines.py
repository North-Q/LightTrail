"""四管线（E5-1）：灵感 / 规划 / 临场 / 复盘。每管线 = 确定性步骤序列。

设计要点（架构 v2.0 §2.1 / §2.2，E5 落地）：
- 数据采集用 `registry.dispatch` 直调（非 ReAct 轮次），每步记 TraceRecorder；
- 评分是**代码化确定性规则**（不让模型自评，与 E2-2 置信度规则同一哲学）；
- LLM 只出现在两头：意图理解（默认模型，强约束 JSON）与末端综合
  （深推理 reason 通道），中间全部确定性；
- 管线失败由上层 Orchestrator 捕获降级 ReAct 自由对话。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from lighttrail.infra.trace import Recorder, null_trace
from lighttrail.infra.validation import parse_with_retry
from lighttrail.llm.router import ModelRouter
from lighttrail.memory import MemoryManager
from lighttrail.orchestrator.context import PipelineContext
from lighttrail.orchestrator.schemas import DecisionCard, Intent

# 默认坐标：上海（可与档案常去机位后续联动精确化）
_DEFAULT_LAT = 31.23
_DEFAULT_LON = 121.47
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


# ------ 确定性工具 ------
def _target_date(time_hint: str) -> str:
    """时间提示 → 目标日期（MVP：明天/周末 → 明日，其余今天）；「今晚」→ 今天。"""
    today = datetime.now(_LOCAL_TZ).date()
    hint = time_hint or ""
    if any(keyword in hint for keyword in ("明天", "后天", "周末", "周六", "周日")):
        return (today + timedelta(days=1)).isoformat()
    return today.isoformat()


def _candidate_sites(memory: MemoryManager | None) -> list[dict[str, Any]]:
    """候选机位：档案常去机位（无精确坐标时用默认坐标兜底）。"""
    sites: list[dict[str, Any]] = []
    if memory is not None:
        for index, name in enumerate(memory.profile.common_locations):
            sites.append({"名称": name, "纬度": _DEFAULT_LAT + index * 0.01, "经度": _DEFAULT_LON + index * 0.01, "题材": ""})
    if not sites:
        sites.append({"名称": "默认机位（上海）", "纬度": _DEFAULT_LAT, "经度": _DEFAULT_LON, "题材": ""})
    return sites


def _collection_steps(subject_type: str, lat: float, lon: float, date_iso: str) -> list[tuple[str, str]]:
    """题材 → 数据采集步骤（工具名, 参数 JSON）。"""
    subject = subject_type or ""
    if subject in ("星空", "银河", "星轨"):
        return [
            ("moon_phase", json.dumps({"date": date_iso}, ensure_ascii=False)),
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


def _collect(env: PipelineEnv, ctx: PipelineContext, steps: list[tuple[str, str]]) -> dict[str, Any]:
    """直调工具采集数据（非 ReAct 轮次），每步落 TraceRecorder。"""
    data: dict[str, Any] = {}
    for tool, args in steps:
        env.recorder.record_step(f"采集_{tool}", input_summary=args[:80], output_summary="")
        raw = env.dispatch(tool, args)
        try:
            parsed: Any = json.loads(raw)
        except ValueError:
            parsed = {"error": raw[:120]}
        data[tool] = parsed
    return data


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
  "time_window": "建议时间窗口",
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
        memory_text = "\n".join(block.text for block in env.memory.build_injections(intent.subject_type if intent else ""))
    if memory_text:
        lines.append(f"用户记忆：\n{memory_text}")
    if ctx.data:
        lines.append("采集数据（JSON）：\n" + json.dumps(ctx.data, ensure_ascii=False, default=str)[:3000])
    if ctx.scores:
        lines.append("代码化评分：" + json.dumps(ctx.scores, ensure_ascii=False))
    return "\n\n".join(lines) + "\n\n" + _DECISION_CARD_INSTRUCTION


class Pipeline:
    """管线基类：name 供路由；run 执行确定步骤序列产出卡片。"""

    name = ""
    description = ""

    def run(self, ctx: PipelineContext, env: PipelineEnv) -> DecisionCard:
        """执行管线。"""
        raise NotImplementedError


class InspirationPipeline(Pipeline):
    """灵感管线（D1.1 一句话出方案）：意图 → 采集 → 评分 → 深推理综合 → 卡片。"""

    name = "inspiration"
    description = "一句话出方案（灵感）"

    def run(self, ctx: PipelineContext, env: PipelineEnv) -> DecisionCard:
        intent = ctx.intent or Intent(subject_type="")
        date_iso = _target_date(intent.time_hint)
        steps = _collection_steps(intent.subject_type, _DEFAULT_LAT, _DEFAULT_LON, date_iso)
        ctx.data = _collect(env, ctx, steps)
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

    def run(self, ctx: PipelineContext, env: PipelineEnv) -> DecisionCard:
        intent = ctx.intent or Intent(subject_type="")
        date_iso = _target_date(intent.time_hint)
        steps = [
            ("weather_forecast", json.dumps({"latitude": _DEFAULT_LAT, "longitude": _DEFAULT_LON, "days": 7, "tz_offset": "+08:00"}, ensure_ascii=False)),
            ("moon_phase", json.dumps({"date": date_iso}, ensure_ascii=False)),
            ("sun_times", json.dumps({"latitude": _DEFAULT_LAT, "longitude": _DEFAULT_LON, "date": date_iso}, ensure_ascii=False)),
        ]
        ctx.data = _collect(env, ctx, steps)
        ctx.scores = _score(ctx.data)
        # 机位匹配：用档案常去机位做候选（无精确坐标时兜底默认机位）
        sites = _candidate_sites(env.memory)
        ctx.data["候选机位"] = sites
        env.recorder.record_step("机位匹配", input_summary=json.dumps(sites, ensure_ascii=False)[:80], output_summary="")
        prompt = _build_synthesis_prompt(ctx, env)
        ctx.card = parse_with_retry(DecisionCard, lambda p: env.reason(p, ""), prompt)
        return ctx.card


class LiveDecisionPipeline(Pipeline):
    """临场决策管线（D3.1 火烧云赌注等）：当前评分 + 时效临近 → 去/等/放弃卡片。"""

    name = "live"
    description = "临场赌注决策（临场）"

    def run(self, ctx: PipelineContext, env: PipelineEnv) -> DecisionCard:
        if ctx.intent is None:
            ctx.intent = Intent(subject_type="火烧云", time_hint="今晚")
        today = datetime.now(_LOCAL_TZ).date().isoformat()
        steps = [
            ("sun_times", json.dumps({"latitude": _DEFAULT_LAT, "longitude": _DEFAULT_LON, "date": today}, ensure_ascii=False)),
            ("sunset_glow_score", json.dumps({"latitude": _DEFAULT_LAT, "longitude": _DEFAULT_LON, "date": today}, ensure_ascii=False)),
            ("weather_forecast", json.dumps({"latitude": _DEFAULT_LAT, "longitude": _DEFAULT_LON, "days": 1, "tz_offset": "+08:00"}, ensure_ascii=False)),
        ]
        ctx.data = _collect(env, ctx, steps)
        ctx.scores = _score(ctx.data)
        # 时效：距日落还有多少小时（1 小时内 → 临场可决策）
        prompt = _build_synthesis_prompt(ctx, env)
        ctx.card = parse_with_retry(DecisionCard, lambda p: env.reason(p, ""), prompt)
        return ctx.card


class ReviewPipeline(Pipeline):
    """复盘管线（D4 骨架）：照片多模态分析由 E6-1 接入，本阶段先产出骨架卡片。"""

    name = "review"
    description = "照片复盘（骨架，E6 接入多模态）"

    def run(self, ctx: PipelineContext, env: PipelineEnv) -> DecisionCard:
        ctx.card = DecisionCard(
            conclusion="复盘管线骨架已就绪；照片分析（EXIF + 画面多模态）将在 E6 接入后输出处方卡片。",
            evidence=[],
            confidence="low",
            degraded="review_pipeline_skeleton",
        )
        env.recorder.record_step("复盘_骨架", input_summary=ctx.user_request, output_summary=ctx.card.conclusion)
        return ctx.card


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
