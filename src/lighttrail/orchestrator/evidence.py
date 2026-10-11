"""决策证据资格：只采用本次实际采集结果，缺少关键依据时收紧行动建议。"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from typing import Any

from lighttrail.contracts.models import DecisionCard, Source
from lighttrail.domain.solar_window import resolve_solar_window
from lighttrail.infra.confidence import confidence_for_tool
from lighttrail.infra.forecast import MAX_FORECAST_AGE, aware_time, forecast_problem

_SUN_SUBJECTS = {"日出", "朝霞", "日落", "晚霞", "火烧云", "蓝调", "黄金时刻"}
_GLOW_SUBJECTS = {"火烧云", "晚霞"}


def _valid_number(value: Any, maximum: float | None = None) -> bool:
    """拒绝非数值、布尔值、非有限值及超出天气字段范围的值。"""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
        and (maximum is None or value <= maximum)
    )


def _window_problems(
    card: DecisionCard, data: dict[str, Any], now: datetime, check_model: bool
) -> list[str]:
    """核验实际天文窗口、窗口小时样本及模型拍摄区间。

    Args:
        card: 本轮模型卡；资格预检允许没有建议区间。
        data: 完整实际工具结果及已固定的事件事实。
        now: 请求基准时间。
        check_model: 最终卡需核对建议窗口，摘要预检只核对实际证据。

    Returns:
        缺失、错配或过期原因；日平均与下午样本不能补齐晨间缺口。
    """
    window = data.get("目标拍摄窗口")
    if not isinstance(window, dict):
        return []
    sun = data.get("sun_times")
    expected = resolve_solar_window(
        sun if isinstance(sun, dict) else {},
        card.target_date,
        str(data.get("目标时区", "")),
        str(data.get("目标事件", "")),
        str(window.get("阶段", "")),
    )
    missing = [f"目标拍摄窗口：{reason}" for reason in expected["缺失原因"]]
    if any(window.get(key) != expected.get(key) for key in ("事件", "事件时刻", "开始", "结束")):
        missing.append("目标拍摄窗口与实际太阳事件不一致")
    start, end = aware_time(expected["开始"]), aware_time(expected["结束"])
    if start is None or end is None:
        return missing or ["目标拍摄窗口缺少绝对时间"]
    if end - start > timedelta(hours=3):
        missing.append("目标拍摄窗口超过三小时摘要边界，不能证明关键小时完整")
        return missing
    if end <= now:
        missing.append("目标拍摄窗口已结束；本轮不自动更换日期")
    weather = data.get("weather_forecast")
    rows = weather.get("逐小时预报", []) if isinstance(weather, dict) else []
    by_time: dict[datetime, list[dict[str, Any]]] = {}
    if isinstance(rows, list):
        for row in rows:
            at = aware_time(row.get("时间")) if isinstance(row, dict) else None
            if at is not None:
                by_time.setdefault(at, []).append(row)
    cursor = start.replace(minute=0, second=0, microsecond=0)
    last = end.replace(minute=0, second=0, microsecond=0)
    if last < end:
        last += timedelta(hours=1)
    while cursor <= last:
        samples = by_time.get(cursor, [])
        if not samples:
            missing.append(f"weather_forecast：目标窗口缺少 {cursor.isoformat()} 小时样本")
        elif len(samples) != 1:
            missing.append(f"weather_forecast：{cursor.isoformat()} 重复样本无法核验")
        else:
            fields = {
                "cloud_cover": 100,
                "visibility": None,
                "precipitation_probability": 100,
                "wind_speed_10m": None,
            }
            absent = [
                key
                for key, maximum in fields.items()
                if not _valid_number(samples[0].get(key), maximum)
            ]
            if absent:
                missing.append(
                    f"weather_forecast：{cursor.isoformat()} 缺少或无效 {','.join(absent)}"
                )
        cursor += timedelta(hours=1)
    if check_model and card.degraded != "synthesis_failed":
        proposed_start, proposed_end = aware_time(card.window_start), aware_time(card.window_end)
        moment = aware_time(expected["事件时刻"])
        tolerance = timedelta(minutes=5)  # 天文窗口本身以五分钟采样。
        good = (
            proposed_start is not None
            and proposed_end is not None
            and start - tolerance <= proposed_start < proposed_end <= end + tolerance
            and proposed_start < end
            and proposed_end > start
        )
        if good and expected["阶段"] == "blue_through_event" and moment is not None:
            good = proposed_start < moment < proposed_end
            period = "晨" if expected["事件"] == "日出" else "暮"
            blue = sun[f"蓝调时刻（{period}）"][0]
            blue_start, blue_end = aware_time(blue["开始_iso"]), aware_time(blue["结束_iso"])
            good = good and proposed_start < blue_end and proposed_end > blue_start
        wrong_label = any(
            word in card.time_window
            for word in (
                ("日落", "傍晚", "暮间") if expected["事件"] == "日出" else ("日出", "清晨", "晨间")
            )
        )
        if not good or wrong_label:
            missing.append("模型拍摄窗口与目标晨/暮事件或蓝调至日出/日落区间不一致")
    return missing


def qualify_card(
    card: DecisionCard, data: dict[str, Any], now: datetime, *, check_model_window: bool = True
) -> DecisionCard:
    """按目标事实核对工具状态及必需字段，并替换模型自报依据。

    Args:
        card: 已对齐请求目标的卡片。
        data: 本次采集结果及确定性目标事实。
        now: 含时区的请求基准时间。
        check_model_window: 最终建议核对窗口；综合前预检仅核对实际数据。

    Returns:
        写入资格、真实依据与保守降级结果的卡片。
    """
    required = {"weather_forecast": "每日预报"}
    event = {"sunrise": "日出", "sunset": "日落"}.get(
        data.get("目标事件"), "日出" if card.subject_type in {"日出", "朝霞"} else "日落"
    )
    if card.subject_type in _SUN_SUBJECTS:
        required["sun_times"] = event
    if card.subject_type in _GLOW_SUBJECTS and event == "日落":
        required["sunset_glow_score"] = "评分（0-100）"
    if card.subject_type in {"银河", "星空", "星轨"}:
        required.update({"moon_phase": "月相名称", "galaxy_visibility": "可见窗口"})
    sources: list[Source] = []
    missing: list[str] = []
    window_problems = _window_problems(card, data, now, check_model_window)
    sun = data.get("sun_times")
    clock_time = sun.get(event, "12:00") if isinstance(sun, dict) else "12:00"
    target = aware_time(f"{card.target_date}T{clock_time}:00{data.get('目标时区', '')}")
    for name, field in required.items():
        result = data.get(name)
        reason = ""
        if not isinstance(result, dict) or not result:
            reason = "未返回有效对象"
        elif "error" in result:
            reason = str(result["error"])
        elif result.get(field) is None or result.get(field) == [] or result.get(field) == "":
            reason = f"缺少 {field}"
        elif result.get("日期", card.target_date) != card.target_date:
            reason = "数据日期与目标日期不一致"
        if not reason and name in {"weather_forecast", "sunset_glow_score"}:
            reason = forecast_problem(result, now)
            position = data.get("目标坐标")
            if not reason and result.get("位置") != position:
                reason = "数据地点与请求机位不一致"
            if not reason and result.get("时区") != data.get("目标时区"):
                reason = "数据时区与请求不一致"
            if not reason and (
                target is None
                or not (
                    aware_time(result.get("有效开始时间"))
                    <= target
                    < aware_time(result.get("有效结束时间"))
                )
            ):
                reason = "预报未覆盖目标事件时刻"
            if not reason and name == "sunset_glow_score":
                if result.get("缺失字段") is None or result.get("缺失字段"):
                    reason = "日落窗口天气字段缺失或覆盖不完整"
                glow_target = aware_time(result.get("目标时刻"))
                if glow_target is None or glow_target != target:
                    reason = "目标日落时刻缺失或日期不一致"
                elif glow_target <= now:
                    reason = "目标日落时刻已过去"
                elif not _valid_number(result.get(field), 100):
                    reason = "启发式评分不是有效数值"
            if not reason and name == "weather_forecast":
                daily = result.get(field)
                if not isinstance(daily, list):
                    missing.append(f"{name}：每日预报格式无效")
                    continue
                target_day = next(
                    (
                        row
                        for row in daily
                        if isinstance(row, dict) and row.get("日期") == card.target_date
                    ),
                    None,
                )
                fields = (
                    "平均云量（%）",
                    "最低能见度（km）",
                    "最高降水概率（%）",
                    "最大风速（km/h）",
                )
                if target_day is None:
                    reason = "预报未覆盖目标日期"
                elif any(
                    not _valid_number(
                        target_day.get(key),
                        100 if key in {"平均云量（%）", "最高降水概率（%）"} else None,
                    )
                    for key in fields
                ):
                    reason = "目标日期的云量、能见度、降水或风速缺失"
        if (
            not reason
            and name == "sun_times"
            and (
                result.get("日期") != card.target_date
                or result.get("时区") != data.get("目标时区")
                or target is None
            )
        ):
            reason = "太阳数据日期或时区无法核对"
        if reason:
            missing.append(f"{name}：{reason}")
            continue
        if name == "weather_forecast" and any(
            problem.startswith("weather_forecast：") for problem in window_problems
        ):
            continue  # 日平均可读，不代表目标窗口的实际小时证据合格。
        window = data.get("目标拍摄窗口")
        if (
            name == "weather_forecast"
            and isinstance(window, dict)
            and (window.get("缺失原因") or "逐小时预报" not in result)
        ):
            continue  # 未确定目标窗口或没有小时字段，禁止伪造合格小时依据。
        source_field = field
        note = json.dumps(result[field], ensure_ascii=False)[:700]
        if name == "weather_forecast" and isinstance(window, dict):
            source_field = "逐小时预报"
            note = f"{window.get('事件')}；实际窗口 {window.get('开始')} 至 {window.get('结束')}，关键小时云量/能见度/降水/风速逐项已核验；完整值见工具快照。"
        sources.append(
            Source(
                tool=name,
                field=source_field,
                confidence=confidence_for_tool(name, result, now=now),
                note=note,
            )
        )
    missing.extend(window_problems)
    card.evidence = sources
    profile = data.get("用户器材")
    if profile:
        card.evidence.append(
            Source(tool="user_profile", field="器材", confidence="medium", note=profile)
        )
    card.missing_data = missing
    card.evaluated_at = now.isoformat()
    qualified_names = {source.tool for source in sources}
    forecasts = [
        data[name] for name in ("weather_forecast", "sunset_glow_score") if name in qualified_names
    ]
    fetched = [aware_time(result.get("获取时间")) for result in forecasts]
    if fetched:
        card.updated_at = min(value for value in fetched if value is not None).isoformat()
        ends = [aware_time(result.get("有效结束时间")) for result in forecasts]
        card.valid_until = min(
            [value for value in ends if value is not None]
            + [value + MAX_FORECAST_AGE for value in fetched if value is not None]
            + ([target] if target is not None and target > now else [])
        ).isoformat()
    else:
        card.updated_at = card.valid_until = ""
    card.confidence_detail = None
    card.data_status = "needs_data" if missing else "ready"
    card.confidence = "low" if missing else "medium"
    if (
        not missing
        and not card.degraded
        and target is not None
        and target - now > timedelta(hours=72)
    ):
        card.confidence = "low"
        card.verdict = "wait"
        card.degraded = "distant_forecast"
        card.conclusion = (
            f"{card.target_date} 在{data.get('目标机位', '')}拍摄{card.subject_type}的远期预报"
            "仅供规划参考，请临近目标日更新后再决定出发。"
        )
    if missing:
        card.verdict = "wait"
        card.degraded = card.degraded if card.degraded == "semantic_mismatch" else "needs_data"
        card.conclusion = (
            f"{card.target_date} 在{data.get('目标机位', '')}拍摄{card.subject_type}的关键数据不足，"
            "暂不能判断是否值得出门。请补齐或更新数据后重新查询。"
        )
        card.time_window = card.window_start = card.window_end = ""
        card.params = []
        card.alternatives = ["补齐或更新缺失数据后重新查询；临场自行观察天空。"]
    # 已有语义护栏的低等级不得被成功证据重新抬高。
    if card.degraded in {"semantic_mismatch", "synthesis_failed"}:
        card.verdict = "wait"
        card.confidence = "low"
    return card
