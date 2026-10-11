"""拍摄建议的最终可执行性护栏：核验行程、器材前提与出发前有效期。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from lighttrail.contracts.models import DecisionCard, Source
from lighttrail.domain.shooting_constraints import focal_key, normalize_clock_text
from lighttrail.infra.forecast import aware_time

_CLOCK = re.compile(r"(?<![\d+:])(?:[01]?\d|2[0-3]):[0-5]\d(?!\d)")
_FOCAL = re.compile(r"\d+(?:\s*[-–—]\s*\d+)?\s*mm", re.IGNORECASE)
_APERTURE = re.compile(r"(?<![A-Za-z])f\s*/?\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
_ND = re.compile(r"(?<![A-Za-z])ND\d*(?![A-Za-z\d])|减光镜", re.IGNORECASE)


def _visible_text(card: DecisionCard) -> list[str]:
    """覆盖全部模型可见建议；来源、目标和结构化时间另由事实护栏负责。"""
    return [
        card.conclusion,
        card.time_window,
        *card.alternatives,
        *(item.reason for item in card.locations),
        *(f"{item.name} {item.value} {item.reason}" for item in card.params),
    ]


def _timeline_problems(text: str, facts: dict[str, Any], now: datetime) -> list[str]:
    """自由文本行程只接受可核验的时刻；既检查下界，也检查同一行程的间隔。"""
    earliest = aware_time(facts["最早出发"])
    commute, setup = facts["单程通勤分钟"], facts["布置分钟"]
    if commute is None or setup is None:
        return []
    ready = aware_time(facts["最早开拍"])
    floor = max(value for value in (now, earliest) if value is not None)
    ready = max(floor + timedelta(minutes=commute + setup), ready or floor)
    departures: list[datetime] = []
    arrivals: list[datetime] = []
    shoots: list[datetime] = []
    unknown = False
    for clause in re.split(r"[，,。；;、\n]", normalize_clock_text(text)):
        role_count = sum(
            bool(re.search(role, clause))
            for role in ("出发", "到达|抵达|到机位", "开拍|拍摄|先拍|拍蓝调")
        )
        if role_count > 1 and len(_CLOCK.findall(clause)) > 1:
            unknown = True  # 多事件未分隔时不能把所有钟点误认成出发。
        if re.search(r"[零〇一二两三四五六七八九十百\d]+[点时]", clause):
            unknown = True
        for match in _CLOCK.finditer(clause):
            # ISO绝对时刻直接使用；普通钟点固定在目标日，禁止自动换日。
            iso = re.search(
                r"\d{4}-\d{2}-\d{2}T"
                + re.escape(match[0])
                + r"(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})",
                clause,
            )
            at = aware_time(iso[0]) if iso else None
            at = at or aware_time(f"{facts['目标日期']}T{match[0]}:00{facts['目标时区']}")
            if at is None:
                unknown = True
            elif "出发" in clause and "返程" not in clause:
                departures.append(at)
            elif any(word in clause for word in ("到达", "抵达", "到机位")):
                arrivals.append(at)
            elif "返程" in clause:
                continue
            else:
                shoots.append(at)  # 无事件标签的时间也不能绕过最早开拍下界。
    bad = unknown or any(at < floor for at in departures)
    depart = max(departures, default=floor)
    bad = bad or any(at < depart + timedelta(minutes=commute) for at in arrivals)
    shot_floor = max(ready, depart + timedelta(minutes=commute + setup))
    if arrivals:
        shot_floor = max(shot_floor, max(arrivals) + timedelta(minutes=setup))
    bad = bad or any(at < shot_floor for at in shoots)
    return ["模型行程不满足最早出发、单程通勤和布置时间，相关建议已撤下"] if bad else []


def _equipment_problems(texts: list[str], facts: dict[str, Any]) -> list[str]:
    """拒绝未知支撑条件、错误ND型号和超出已确认镜头光圈的建议。"""
    text = "；".join(texts)
    problems: list[str] = []
    seconds = [
        float(value)
        for value in re.findall(r"(?<![/\d])(\d+(?:\.\d+)?)\s*(?:秒|s\b)", text, re.IGNORECASE)
    ]
    if (
        "三脚架" in text or "长曝" in text or "慢门" in text or any(s >= 1 for s in seconds)
    ) and facts["三脚架"] is not True:
        problems.append("三脚架未确认可用，不能执行依赖三脚架或秒级长曝光的建议")
    if _ND.search(text):
        if facts["ND"] is not True:
            problems.append("ND未确认可用，不能将减光镜作为已具备条件")
        elif any(
            model.upper() not in [item.upper() for item in facts["ND型号"]]
            for model in re.findall(r"(?<![A-Za-z])ND\d+(?![A-Za-z\d])", text, re.IGNORECASE)
        ):
            problems.append("建议ND型号未确认，不能用其他减光镜代替")
    apertures = [float(value) for value in _APERTURE.findall(text)]
    apertures.extend(
        float(value)
        for value in re.findall(r"光圈\s*(?:开到|设为|设置为|为|是|[:：])?\s*(\d+(?:\.\d+)?)", text)
    )
    if apertures:
        focals = {focal_key(match[0]) for match in _FOCAL.finditer(text)}
        limits = facts["镜头最大光圈"]
        limits = {focal: _lens_limit(focal, limits) for focal in focals} if focals else limits
        if not focals and len(limits) == 1:
            focals = set(limits)
        if not focals or any(limits.get(focal) is None for focal in focals):
            problems.append("所用镜头最大光圈未确认，不猜测14mm或其他镜头的可用光圈")
        elif any(aperture < limits[focal] for aperture in apertures for focal in focals):
            problems.append("建议光圈超出所用镜头已确认的最大光圈")
    return problems


def _lens_limit(focal: str, limits: dict[str, float | None]) -> float | None:
    """点焦距只能使用覆盖该焦距且唯一的已确认镜头，不借用其他镜头光圈。"""
    if focal in limits:
        return limits[focal]
    point = re.fullmatch(r"(\d+)mm", focal)
    if point is None:
        return None
    candidates = [
        limit
        for name, limit in limits.items()
        if (span := re.fullmatch(r"(\d+)-(\d+)mm", name))
        and int(span[1]) <= int(point[1]) <= int(span[2])
    ]
    return candidates[0] if len(candidates) == 1 else None


def constrain_card(card: DecisionCard, facts: dict[str, Any], now: datetime) -> DecisionCard:
    """撤下违约建议，保留数据资格护栏、真实时间及可核验的行程提示。

    Args:
        card: 已按完整实际数据核验的本轮卡片；不用于历史恢复。
        facts: 本轮原请求与本人镜头档案确定的前提。
        now: 本次请求的绝对时间。

    Returns:
        无违约建议或明确待确认的同一张卡；不调用模型、工具或存储。
    """
    texts = _visible_text(card)
    problems = list(facts["待确认"])
    problems.extend(_equipment_problems(texts, facts))
    # 同一主方案跨字段核验；各备选独立核验，避免混合两个不同的可行行程。
    main_texts = [
        card.conclusion,
        card.time_window,
        *(item.reason for item in card.locations),
        *(f"{item.name} {item.value} {item.reason}" for item in card.params),
    ]
    for text in ["；".join(main_texts), *card.alternatives]:
        problems.extend(_timeline_problems(text, facts, now))
    commute, setup = facts["单程通勤分钟"], facts["布置分钟"]
    start = aware_time(card.window_start)
    earliest = aware_time(facts["最早出发"])
    itinerary: list[str] = []
    if commute is not None and setup is not None:
        departure_floor = max(value for value in (now, earliest) if value is not None)
        arrival = departure_floor + timedelta(minutes=commute)
        ready = arrival + timedelta(minutes=setup)
        itinerary.append(
            f"单程通勤{commute:g}分钟、布置{setup:g}分钟；最早{departure_floor.isoformat()}出发，{arrival.isoformat()}到达，{ready.isoformat()}布置完毕后方可开拍。"
        )
        if start:
            departure = start - timedelta(minutes=commute + setup)
            if departure < departure_floor:
                problems.append("主拍摄窗口开始前无法完成通勤和布置，不自动推迟窗口或更换日期")
            else:
                itinerary.append(f"按所选拍摄窗口，最晚{departure.isoformat()}出发。")
            expiry = aware_time(card.valid_until)
            if expiry is not None and expiry <= max(departure, departure_floor):
                problems.append(
                    f"本卡有效期{card.valid_until}在出发前或出发时到期，出发前需手动更新天气与建议；回看不续期，不会自动查询"
                )
    if itinerary or facts["三脚架"] is not None or facts["ND"] is not None:
        card.evidence.append(
            Source(
                tool="user_constraints",
                field="拍摄前提",
                confidence="medium",
                note="本轮明确声明及镜头档案；三脚架/ND缺失时保持未知，不从机身或偏好推断。"
                + " ".join(itinerary),
            )
        )
    if problems:
        already_missing = card.data_status == "needs_data"
        card.missing_data = list(dict.fromkeys([*card.missing_data, *problems]))
        card.verdict, card.confidence, card.data_status = "wait", "low", "needs_data"
        if not already_missing and card.degraded not in {
            "semantic_mismatch",
            "synthesis_failed",
            "distant_forecast",
        }:
            card.degraded = card.degraded or "shooting_constraints"
            card.conclusion = f"{card.target_date}拍摄{card.subject_type}的建议尚不满足通勤、布置时间、器材或出发时效前提，暂缓执行；请核对所列原因。"
        card.time_window = card.window_start = card.window_end = ""
        card.params = []
        for location in card.locations:
            location.reason = "本轮已确认的目标机位，尚需核对拍摄前提。"
        card.alternatives = [
            *itinerary,
            "确认所需器材；出发前手动核对有效数据。本次不会自动查询或延长有效期。",
        ]
    else:
        card.alternatives.extend(itinerary)
    return card
