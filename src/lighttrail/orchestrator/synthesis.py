"""综合模型的数据摘要：先按目标筛选字段，再序列化完整 JSON；原始证据不变。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from lighttrail.contracts.models import DecisionCard
from lighttrail.infra.forecast import aware_time
from lighttrail.orchestrator.evidence import qualify_card

MAX_SUMMARY_CHARS = 12000
MAX_HOURLY_ROWS = 7
MAX_TEXT_CHARS = 400
_METADATA = {
    "位置",
    "日期",
    "时区",
    "获取时间",
    "有效开始时间",
    "有效结束时间",
    "数据来源",
    "error",
    "说明",
}
_SUN_FIELDS = {
    "日出",
    "日落",
    "日出_iso",
    "日落_iso",
    "日出方位（度）",
    "日落方位（度）",
    "事件状态",
    "黄金时刻（晨）",
    "黄金时刻（暮）",
    "蓝调时刻（晨）",
    "蓝调时刻（暮）",
    "民用晨光始",
    "民用昏影终",
    "航海晨光始",
    "航海昏影终",
    "天文晨光始",
    "天文昏影终",
    "提示",
}
_GLOW_FIELDS = {
    "评分（0-100）",
    "等级",
    "置信度",
    "缺失字段",
    "窗口样本数",
    "依据",
    "日落时间",
    "目标时刻",
}
_DAILY_FIELDS = {
    "日期",
    "平均云量（%）",
    "云量范围（%）",
    "最低能见度（km）",
    "最高降水概率（%）",
    "最大风速（km/h）",
    "傍晚云量序列",
}
_HOURLY_FIELDS = {
    "时间",
    "cloud_cover",
    "cloud_cover_high",
    "cloud_cover_low",
    "visibility",
    "precipitation_probability",
    "wind_speed_10m",
}


class SynthesisSummaryError(Exception):
    """关键数据或省略记录超过摘要边界，禁止发送残缺 JSON。"""


def build_synthesis_json(
    data: dict[str, Any], *, now: datetime, tz_offset: str, subject: str
) -> str:
    """构造最多 12000 字符的完整工具数据 JSON，不修改原始结果。

    Args:
        data: 完整实际工具结果及目标事实。
        now: 请求基准时间。
        tz_offset: 请求时区。
        subject: 当前题材，用于展示完整证据的资格与缺失原因。

    Returns:
        完整 JSON；小时样本最多七条，日摘要仅保留目标日期。

    Raises:
        SynthesisSummaryError: 完整摘要超过边界或缺失信息无法完整保留。
    """
    omissions: list[dict[str, Any]] = []

    def bound(value: Any, path: str, depth: int = 0) -> Any:
        """对单字段设置结构边界并留下省略路径、数量和内容哈希。"""
        if isinstance(value, str) and len(value) > MAX_TEXT_CHARS:
            omissions.append(
                {
                    "路径": path,
                    "省略字符": len(value) - MAX_TEXT_CHARS,
                    "原文_sha256": hashlib.sha256(value.encode()).hexdigest(),
                }
            )
            return value[:MAX_TEXT_CHARS] + "（摘要缩短，原文见快照）"
        if depth > 6:
            raise SynthesisSummaryError(f"摘要字段结构过深：{path}")
        if isinstance(value, dict):
            keys = sorted(value)
            if len(keys) > 24:
                omissions.append({"路径": path, "省略字段": keys[24:]})
            return {key: bound(value[key], f"{path}.{key}", depth + 1) for key in keys[:24]}
        if isinstance(value, list):
            if len(value) > 8:
                omissions.append({"路径": path, "省略条数": len(value) - 8})
            return [bound(item, f"{path}[{i}]", depth + 1) for i, item in enumerate(value[:8])]
        return value

    def select(value: dict[str, Any], keys: set[str], path: str) -> dict[str, Any]:
        """按字段白名单保留数据，不依赖上游字典顺序。"""
        omitted = sorted(set(value) - keys)
        if omitted:
            omissions.append({"路径": path, "省略字段": omitted})
        return {key: bound(value[key], f"{path}.{key}") for key in sorted(keys & set(value))}

    summary = {
        key: bound(data[key], key)
        for key in sorted(data)
        if key.startswith("目标") or key == "请求题材"
    }
    summary.setdefault("目标时区", tz_offset)
    summary["请求基准时间"] = now.isoformat()
    target_date = str(data.get("目标日期", ""))
    sun = data.get("sun_times", {})
    event = {"sunrise": "日出", "sunset": "日落"}.get(
        data.get("目标事件"), "日出" if subject in {"日出", "朝霞"} else "日落"
    )
    event_time = sun.get(event) if isinstance(sun, dict) else None
    target_tz = str(summary["目标时区"])
    window = data.get("目标拍摄窗口")
    explicit_window = isinstance(window, dict)
    center = (
        aware_time(window.get("事件时刻"))
        if explicit_window
        else aware_time(f"{target_date}T{event_time or '18:00'}:00{target_tz}")
    )
    if explicit_window:
        start, end = aware_time(window.get("开始")), aware_time(window.get("结束"))
        select_start = (
            start.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1) if start else None
        )
        select_end = (
            end.replace(minute=0, second=0, microsecond=0) + timedelta(hours=2) if end else None
        )
    else:
        select_start = center - timedelta(hours=3) if center else None
        select_end = center + timedelta(hours=3) if center else None
    summary["小时筛选规则"] = {
        "事件": event,
        "中心": center.isoformat() if center else None,
        "中心为替代时刻": not explicit_window and not bool(event_time),
        "前后小时": None if explicit_window else 3,
        "取样开始": select_start.isoformat() if select_start else None,
        "取样结束": select_end.isoformat() if select_end else None,
        "依据": "目标天文拍摄窗口，附前后一小时预报" if explicit_window else "事件前后三小时",
        "最多样本": MAX_HOURLY_ROWS,
    }
    for name in sorted(data):
        value = data[name]
        if name in summary:
            continue
        if name == "候选机位":
            summary[name] = bound(value, name)
        elif name == "sun_times" and isinstance(value, dict):
            summary[name] = select(value, _METADATA | _SUN_FIELDS, name)
        elif name == "sunset_glow_score" and isinstance(value, dict):
            summary[name] = select(value, _METADATA | _GLOW_FIELDS, name)
            # 缺失原因不得因摘要裁剪而隐藏。
            if isinstance(value.get("缺失字段"), list) and len(value["缺失字段"]) > 8:
                raise SynthesisSummaryError("缺失字段过多，不能完整保留")
        elif name == "weather_forecast" and isinstance(value, dict):
            summary[name] = select(
                {key: item for key, item in value.items() if key not in {"每日预报", "逐小时预报"}},
                _METADATA,
                name,
            )
            daily = value.get("每日预报", [])
            hours = value.get("逐小时预报", [])
            summary[name]["每日预报"] = (
                bound(daily, f"{name}.每日预报") if not isinstance(daily, list) else []
            )
            summary[name]["逐小时预报"] = (
                bound(hours, f"{name}.逐小时预报") if not isinstance(hours, list) else []
            )
            if isinstance(daily, list):
                selected_days = [
                    row for row in daily if isinstance(row, dict) and row.get("日期") == target_date
                ][:1]
                summary[name]["每日预报"] = [
                    select(
                        row,
                        _DAILY_FIELDS - {"傍晚云量序列"} if explicit_window else _DAILY_FIELDS,
                        f"{name}.每日预报",
                    )
                    for row in selected_days
                ]
                omissions.append(
                    {"路径": f"{name}.每日预报", "省略条数": len(daily) - len(selected_days)}
                )
            if isinstance(hours, list):
                eligible = []
                for row in hours:
                    timestamp = aware_time(row.get("时间")) if isinstance(row, dict) else None
                    if (
                        select_start
                        and select_end
                        and timestamp
                        and select_start <= timestamp <= select_end
                    ):
                        eligible.append(
                            (
                                timestamp,
                                json.dumps(row, sort_keys=True, ensure_ascii=False, default=str),
                                row,
                            )
                        )
                # 同一时刻只取一个确定样本，其他仍留在原始证据中接受资格校验。
                unique = {
                    timestamp: row
                    for timestamp, _, row in sorted(eligible, key=lambda item: (item[0], item[1]))
                }
                selected_times = sorted(
                    unique,
                    key=lambda at: (
                        0
                        if explicit_window
                        and start
                        and end
                        and start.replace(minute=0, second=0, microsecond=0)
                        <= at
                        <= end.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
                        else 1,
                        abs((at - center).total_seconds()),
                        at,
                    ),
                )[:MAX_HOURLY_ROWS]
                summary[name]["逐小时预报"] = [
                    select(unique[at], _HOURLY_FIELDS, f"{name}.逐小时预报")
                    for at in sorted(selected_times)
                ]
                omissions.append(
                    {
                        "路径": f"{name}.逐小时预报",
                        "省略条数": len(hours) - len(selected_times),
                        "保留条数": len(selected_times),
                    }
                )
        else:
            summary[name] = bound(value, name)
    if target_date:
        qualified = qualify_card(
            DecisionCard(
                conclusion="资格预检",
                evidence=[],
                confidence="low",
                subject_type=subject,
                target_date=target_date,
            ),
            {"目标时区": tz_offset, **data},
            now,
            check_model_window=False,
        )
        summary["完整证据资格"] = {
            "状态": qualified.data_status,
            "缺失原因": qualified.missing_data,
            "有效截至": qualified.valid_until,
        }
    summary["摘要边界"] = {
        "最多字符": MAX_SUMMARY_CHARS,
        "原始证据": "完整工具结果保留在管线与历史快照，资格校验不使用摘要",
        "省略": omissions,
    }
    encoded = json.dumps(summary, ensure_ascii=False, sort_keys=True, default=str)
    if len(encoded) > MAX_SUMMARY_CHARS:
        raise SynthesisSummaryError("完整综合摘要超过 12000 字符，停止综合并保守降级")
    return encoded
