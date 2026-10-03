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
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from lighttrail.contracts.context import RequestContext
from lighttrail.contracts.models import DecisionCard, Intent, LocationSuggestion
from lighttrail.contracts.plan import DEFAULT_PIPELINE_MAX_STEPS
from lighttrail.infra.confidence import confidence_detail
from lighttrail.infra.quota import QuotaLedger
from lighttrail.infra.trace import Recorder, null_trace
from lighttrail.infra.validation import parse_with_retry
from lighttrail.llm.router import ModelRouter
from lighttrail.memory import MemoryManager
from lighttrail.orchestrator.context import PipelineContext

logger = logging.getLogger("lighttrail.orchestrator.pipelines")


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
    request: RequestContext = field(default_factory=RequestContext)
    max_steps: int = DEFAULT_PIPELINE_MAX_STEPS


class PipelineStepLimitError(Exception):
    """管线数据采集步数超过当前部署上限。"""


class DateResolutionError(ValueError):
    """请求日期或时区无法确定，禁止猜测拍摄日。"""


# ------ 确定性工具 ------
_ISO_DATE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
_TZ_OFFSET = re.compile(r"([+-])(\d{2}):(\d{2})\Z")
_MATCH_SUBJECT = {
    "火烧云": "日落",
    "晚霞": "日落",
    "朝霞": "日出",
    "黄金时刻": "黄金",
    "星轨": "星空",
    "城市风光": "夜景",
}


def _request_timezone(request: RequestContext) -> timezone:
    """把请求的 ISO 时区偏移转为本地日期与工具共用的时区。"""
    matched = _TZ_OFFSET.fullmatch(request.tz_offset)
    if matched is None:
        raise DateResolutionError("请求时区格式无效，请使用 +08:00 这样的偏移")
    hours, minutes = int(matched[2]), int(matched[3])
    if hours > 14 or minutes > 59 or (hours == 14 and minutes != 0):
        raise DateResolutionError("请求时区偏移超出有效范围")
    delta = timedelta(hours=hours, minutes=minutes)
    return timezone(delta if matched[1] == "+" else -delta)


def _target_date(time_hint: str, request: RequestContext) -> str:
    """依据请求时区和基准时间解析 ISO、相对日及跨周周末。"""
    if request.requested_at.tzinfo is None:
        raise DateResolutionError("请求基准时间必须包含时区")
    today = request.requested_at.astimezone(_request_timezone(request)).date()
    hint = time_hint.strip()
    explicit = _ISO_DATE.search(hint)
    if explicit is not None:
        try:
            return date.fromisoformat(explicit.group()).isoformat()
        except ValueError as exc:
            raise DateResolutionError("日期格式无效，请使用 YYYY-MM-DD") from exc
    if "后天" in hint:
        return (today + timedelta(days=2)).isoformat()
    if "明天" in hint:
        return (today + timedelta(days=1)).isoformat()
    if "下周末" in hint or "下周六" in hint or "下周日" in hint:
        weekday = 6 if "周日" in hint else 5
        return (today + timedelta(days=7 - today.weekday() + weekday)).isoformat()
    if "这周末" in hint or "周末" in hint:
        weekday = 6 if today.weekday() == 6 else 5
        return (today + timedelta(days=(weekday - today.weekday()) % 7)).isoformat()
    if "这周六" in hint or "本周六" in hint or "这周日" in hint or "本周日" in hint:
        weekday = 6 if "周日" in hint else 5
        target = today + timedelta(days=weekday - today.weekday())
        if target < today:
            raise DateResolutionError("本周指定日期已过去，请确认目标日期")
        return target.isoformat()
    if "周六" in hint or "周日" in hint:
        weekday = 6 if "周日" in hint else 5
        return (today + timedelta(days=(weekday - today.weekday()) % 7)).isoformat()
    if not hint or any(
        word in hint for word in ("今天", "今晚", "清晨", "傍晚", "日出前", "日落后")
    ):
        return today.isoformat()
    raise DateResolutionError("无法确认拍摄日期，请提供 YYYY-MM-DD 或明确的相对日期")


def _candidate_sites(memory: MemoryManager | None, request: RequestContext) -> list[dict[str, Any]]:
    """从档案的真实 favorite_spots 返回候选机位；空档案不编造坐标。"""
    if memory is None:
        return []
    return [
        {
            "名称": item["name"],
            "纬度": item["latitude"],
            "经度": item["longitude"],
            "题材": item["subject"],
        }
        for item in memory.profile_for(request).valid_favorite_spots()
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


def _clarification_card(message: str, reason: str) -> DecisionCard:
    """地点或日期不确定时直接追问，禁止调用错误坐标的数据源。"""
    return DecisionCard(
        conclusion=message,
        evidence=[],
        confidence="low",
        verdict="wait",
        degraded=reason,
    )


def _select_site(
    sites: list[dict[str, Any]], intent: Intent, user_request: str
) -> dict[str, Any] | DecisionCard:
    """按原文明确机位、规范意图、唯一题材匹配、唯一收藏点的顺序选址。"""
    if not sites:
        return _missing_site_card()
    location = intent.location.strip()
    if location:
        exact = [site for site in sites if site["名称"] == location]
        if len(exact) == 1:
            return exact[0]
        return _clarification_card(
            f"尚无「{location}」的有效机位坐标，请选择已收藏机位或补充经纬度。",
            "missing_requested_location",
        )
    mentioned = [site for site in sites if site["名称"] in user_request]
    if len(mentioned) == 1:
        return mentioned[0]
    if len(mentioned) > 1:
        return _clarification_card(
            "请求涉及多个收藏机位，请明确本次要拍的机位。", "ambiguous_location"
        )
    if len(sites) == 1:
        return sites[0]
    exact_subject = [site for site in sites if site["题材"] == intent.subject_type]
    if len(exact_subject) == 1:
        return exact_subject[0]
    if not exact_subject:
        canonical = _MATCH_SUBJECT.get(intent.subject_type, intent.subject_type)
        compatible = [
            site for site in sites if _MATCH_SUBJECT.get(site["题材"], site["题材"]) == canonical
        ]
        if len(compatible) == 1:
            return compatible[0]
    return _clarification_card(
        "有多个可用收藏机位，请明确本次拍摄地点；不会默认使用第一个机位。",
        "ambiguous_location",
    )


def _prepare_target(
    ctx: PipelineContext, env: PipelineEnv
) -> tuple[dict[str, Any], list[dict[str, Any]], str] | DecisionCard:
    """在采集前确定目标日期和有坐标机位，避免位置或日期静默猜测。"""
    intent = ctx.intent or Intent(subject_type="")
    try:
        date_iso = _target_date(intent.time_hint, env.request)
    except DateResolutionError as exc:
        return _clarification_card(str(exc), "invalid_target_date")
    sites = _candidate_sites(env.memory, env.request)
    selected = _select_site(sites, intent, ctx.user_request)
    if isinstance(selected, DecisionCard):
        selected.target_date = date_iso
        selected.subject_type = intent.subject_type
        return selected
    return selected, sites, date_iso


def _set_target_data(
    ctx: PipelineContext, sites: list[dict[str, Any]], site: dict[str, Any], date_iso: str
) -> None:
    """把本次请求事实与收藏机位元数据分开交给综合模型。"""
    subject = ctx.intent.subject_type if ctx.intent is not None else ""
    ctx.data["目标日期"] = date_iso
    ctx.data["目标机位"] = site["名称"]
    ctx.data["请求题材"] = subject
    ctx.data["候选机位"] = [
        {**candidate, "档案题材": candidate["题材"], "题材": subject} for candidate in sites
    ]


def _align_card(
    card: DecisionCard,
    intent: Intent,
    site: dict[str, Any],
    date_iso: str,
    request: RequestContext,
    sites: list[dict[str, Any]],
) -> DecisionCard:
    """固定结构化目标；模型地点或窗口冲突时给保守提示。"""
    location_mismatch = any(location.name != site["名称"] for location in card.locations)
    subject_mismatch = bool(card.subject_type and card.subject_type != intent.subject_type)
    date_mismatch = bool(card.target_date and card.target_date != date_iso)
    target_day = date.fromisoformat(date_iso)
    visible_text = card.conclusion + " " + card.time_window
    text_mismatch = any(
        candidate["名称"] in visible_text
        for candidate in sites
        if candidate["名称"] != site["名称"]
    ) or any(value != date_iso for value in _ISO_DATE.findall(visible_text))
    remaining_text = visible_text
    for keyword in (
        "下周末",
        "下周六",
        "下周日",
        "这周末",
        "这周六",
        "这周日",
        "本周六",
        "本周日",
        "后天",
        "明天",
        "今晚",
    ):
        if keyword in remaining_text:
            try:
                matches_date = _target_date(keyword, request) == date_iso
            except DateResolutionError:
                matches_date = False
            if not matches_date:
                text_mismatch = True
            remaining_text = remaining_text.replace(keyword, "")
    if "周末" in remaining_text and target_day.weekday() not in (5, 6):
        text_mismatch = True
    if "周六" in remaining_text and target_day.weekday() != 5:
        text_mismatch = True
    if "周日" in remaining_text and target_day.weekday() != 6:
        text_mismatch = True
    window_mismatch = bool(card.window_start) != bool(card.window_end)
    parsed_window: list[datetime] = []
    for index, value in enumerate((card.window_start, card.window_end)):
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                window_mismatch = True
                break
            local_date = parsed.astimezone(_request_timezone(request)).date()
            allowed_dates = (
                {target_day} if index == 0 else {target_day, target_day + timedelta(days=1)}
            )
            if local_date not in allowed_dates:
                window_mismatch = True
                break
            parsed_window.append(parsed)
        except ValueError:
            window_mismatch = True
            break
    if len(parsed_window) == 2 and parsed_window[1] <= parsed_window[0]:
        window_mismatch = True
    card.target_date = date_iso
    card.subject_type = intent.subject_type
    matching = [location for location in card.locations if location.name == site["名称"]]
    card.locations = matching[:1] or [
        LocationSuggestion(name=site["名称"], reason="本次请求确定的收藏机位")
    ]
    if location_mismatch or subject_mismatch or date_mismatch or window_mismatch or text_mismatch:
        card.degraded = "semantic_mismatch"
        card.verdict = "wait"
        card.confidence = "low"
        card.evidence = []
        card.params = []
        card.alternatives = []
        card.window_start = ""
        card.window_end = ""
        card.time_window = ""
        card.conclusion = (
            f"已确认 {date_iso} 在{site['名称']}拍摄{intent.subject_type}；"
            "模型生成的地点、题材或时间窗口与请求不一致，请核对后再出发。"
        )
    return card


def _collection_steps(
    subject_type: str, lat: float, lon: float, date_iso: str, tz_offset: str = "+08:00"
) -> list[tuple[str, str]]:
    """题材 → 数据采集步骤（工具名, 参数 JSON）。"""
    subject = subject_type or ""
    if subject in ("星空", "银河", "星轨"):
        return [
            (
                "moon_phase",
                json.dumps({"date": date_iso, "tz_offset": tz_offset}, ensure_ascii=False),
            ),
            (
                "moon_events",
                json.dumps(
                    {"latitude": lat, "longitude": lon, "date": date_iso, "tz_offset": tz_offset},
                    ensure_ascii=False,
                ),
            ),
            (
                "galaxy_visibility",
                json.dumps(
                    {"latitude": lat, "longitude": lon, "date": date_iso, "tz_offset": tz_offset},
                    ensure_ascii=False,
                ),
            ),
            (
                "weather_forecast",
                json.dumps(
                    {"latitude": lat, "longitude": lon, "days": 3, "tz_offset": tz_offset},
                    ensure_ascii=False,
                ),
            ),
        ]
    if subject in ("日出", "朝霞", "日落", "晚霞", "火烧云", "蓝调", "黄金时刻"):
        return [
            (
                "sun_times",
                json.dumps(
                    {"latitude": lat, "longitude": lon, "date": date_iso, "tz_offset": tz_offset},
                    ensure_ascii=False,
                ),
            ),
            (
                "sunset_glow_score",
                json.dumps(
                    {"latitude": lat, "longitude": lon, "date": date_iso, "tz_offset": tz_offset},
                    ensure_ascii=False,
                ),
            ),
            (
                "weather_forecast",
                json.dumps(
                    {"latitude": lat, "longitude": lon, "days": 3, "tz_offset": tz_offset},
                    ensure_ascii=False,
                ),
            ),
        ]
    return [
        (
            "weather_forecast",
            json.dumps(
                {"latitude": lat, "longitude": lon, "days": 3, "tz_offset": tz_offset},
                ensure_ascii=False,
            ),
        )
    ]


def _planning_steps(
    subject_type: str, lat: float, lon: float, date_iso: str, tz_offset: str = "+08:00"
) -> list[tuple[str, str]]:
    """规划管线按题材采集，并把天气窗口扩至七天。"""
    steps = _collection_steps(subject_type, lat, lon, date_iso, tz_offset)
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
    env: PipelineEnv, sites: list[dict[str, Any]], date_iso: str, subject_type: str
) -> dict[str, Any]:
    """调用确定性机位匹配，并把异常收敛为可解释的降级数据。"""
    subject = _MATCH_SUBJECT.get(subject_type, subject_type)
    if subject not in {"日出", "日落", "蓝调", "黄金", "银河", "星空", "夜景"}:
        return {"error": f"题材「{subject_type}」暂不支持机位天象匹配"}
    normalized = [{**site, "题材": subject} for site in sites]
    args = json.dumps(
        {"sites": normalized, "date": date_iso, "tz_offset": env.request.tz_offset},
        ensure_ascii=False,
    )
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


def _collect(
    env: PipelineEnv, ctx: PipelineContext, steps: list[tuple[str, str]]
) -> dict[str, Any]:
    """同步门面：在独立事件循环里跑并发采集（管线在 worker 线程中同步调用）。

    Args:
        env: 管线运行环境。
        ctx: 管线上下文。
        steps: (工具名, 参数 JSON) 列表。

    Returns:
        与 `_collect_async` 相同的采集结果。
    """
    _check_step_limit(env, len(steps))
    return asyncio.run(_collect_async(env, ctx, steps))


def _check_step_limit(env: PipelineEnv, count: int) -> None:
    """采集前检查步数，避免超限时只执行部分工具。"""
    if count > env.max_steps:
        reason = f"需要 {count} 步，配置上限 {env.max_steps} 步"
        env.recorder.record_step("管线_步数超限", output_summary=reason)
        raise PipelineStepLimitError(reason)


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
  "degraded": "",
  "target_date": "本次目标日期 YYYY-MM-DD",
  "subject_type": "本次用户请求题材"
}"""


def _build_synthesis_prompt(ctx: PipelineContext, env: PipelineEnv) -> str:
    """组装综合 prompt：意图 + 数据 + 评分 + 记忆（第④层对齐）。"""
    lines = [f"用户请求：{ctx.user_request}"]
    intent = ctx.intent
    if intent is not None:
        lines.append(
            f"规范意图：题材={intent.subject_type or '未指定'}，地点={intent.location or '未指定'}，时间={intent.time_hint or '未指定'}"
        )
    if "目标日期" in ctx.data and "目标机位" in ctx.data:
        lines.append(
            f"已验证目标：{ctx.data['目标日期']} 在 {ctx.data['目标机位']} 拍摄"
            f" {ctx.data.get('请求题材', '')}。结论、时间窗口和 locations 必须与此一致；"
            "收藏机位的档案题材仅是历史偏好，不得覆盖本次请求题材。"
        )
    memory_text = ""
    if env.memory is not None:
        memory_text = "\n".join(
            block.text
            for block in env.memory.build_injections(
                env.request, intent.subject_type if intent else ""
            )
        )
    if memory_text:
        lines.append(f"用户记忆：\n{memory_text}")
    if ctx.data:
        lines.append(
            "采集数据（JSON）：\n" + json.dumps(ctx.data, ensure_ascii=False, default=str)[:3000]
        )
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
        target = _prepare_target(ctx, env)
        if isinstance(target, DecisionCard):
            return target
        site, sites, date_iso = target
        steps = _collection_steps(
            intent.subject_type, site["纬度"], site["经度"], date_iso, env.request.tz_offset
        )
        ctx.data = _collect(env, ctx, steps)
        _set_target_data(ctx, sites, site, date_iso)
        ctx.scores = _score(ctx.data)
        prompt = _build_synthesis_prompt(ctx, env)
        env.recorder.record_step("综合_reason", input_summary=prompt[:80], output_summary="")
        ctx.card = parse_with_retry(DecisionCard, lambda p: env.reason(p, ""), prompt)
        env.recorder.record_step("卡片_产出", input_summary=ctx.card.conclusion, output_summary="")
        return _align_card(ctx.card, intent, site, date_iso, env.request, sites)


class PlanningPipeline(Pipeline):
    """规划管线（D2.3 计划生成）：天气 7 天 + 月相 + 机位匹配 → 编排卡片。"""

    name = "planning"
    description = "多机位拍摄计划编排（规划）"

    def _run(self, ctx: PipelineContext, env: PipelineEnv) -> DecisionCard:
        intent = ctx.intent or Intent(subject_type="")
        target = _prepare_target(ctx, env)
        if isinstance(target, DecisionCard):
            return target
        site, sites, date_iso = target
        steps = _planning_steps(
            intent.subject_type, site["纬度"], site["经度"], date_iso, env.request.tz_offset
        )
        _check_step_limit(env, len(steps) + 1)
        ctx.data = _collect(env, ctx, steps)
        _set_target_data(ctx, sites, site, date_iso)
        ctx.data["match_sites"] = _match_candidate_sites(env, sites, date_iso, intent.subject_type)
        ctx.scores = _score(ctx.data)
        prompt = _build_synthesis_prompt(ctx, env)
        ctx.card = parse_with_retry(DecisionCard, lambda p: env.reason(p, ""), prompt)
        return _align_card(ctx.card, intent, site, date_iso, env.request, sites)


class LiveDecisionPipeline(Pipeline):
    """临场决策管线（D3.1 火烧云赌注等）：当前评分 → 去/等/放弃卡片。"""

    name = "live"
    description = "临场赌注决策（临场）"

    def _run(self, ctx: PipelineContext, env: PipelineEnv) -> DecisionCard:
        if ctx.intent is None:
            ctx.intent = Intent(subject_type="火烧云", time_hint="今晚")
        target = _prepare_target(ctx, env)
        if isinstance(target, DecisionCard):
            return target
        site, sites, date_iso = target
        steps = _collection_steps(
            ctx.intent.subject_type,
            site["纬度"],
            site["经度"],
            date_iso,
            env.request.tz_offset,
        )
        _check_step_limit(env, len(steps) + 1)
        ctx.data = _collect(env, ctx, steps)
        _set_target_data(ctx, sites, site, date_iso)
        ctx.data["match_sites"] = _match_candidate_sites(
            env, sites, date_iso, ctx.intent.subject_type
        )
        ctx.scores = _score(ctx.data)
        prompt = _build_synthesis_prompt(ctx, env)
        ctx.card = parse_with_retry(DecisionCard, lambda p: env.reason(p, ""), prompt)
        return _align_card(ctx.card, ctx.intent, site, date_iso, env.request, sites)


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
        env.recorder.record_step(
            "复盘_照片分析", input_summary=image_path[:80], output_summary=focus[:60]
        )
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
    for pipeline in (
        InspirationPipeline(),
        PlanningPipeline(),
        LiveDecisionPipeline(),
        ReviewPipeline(),
    )
}


def default_mode(subject_type: str) -> str:
    """题材 → 缺省管线模式（意图未给 mode 时用）。"""
    if subject_type in ("火烧云", "晚霞", "日落"):
        return "live"
    return "inspiration"
