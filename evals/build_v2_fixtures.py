"""生成 R0-7 独立离线夹具；数据均为合成样本，不代表真实 API 观测。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
GOLDEN = ROOT / "golden" / "L2-cases.json"
CASSETTES = ROOT / "cassettes" / "v2"
REQUESTED_AT = "2026-09-28T02:00:00+00:00"
GALAXY_TOOLS = ["moon_phase", "moon_events", "galaxy_visibility", "weather_forecast"]
SUN_TOOLS = ["sun_times", "sunset_glow_score", "weather_forecast"]

# id、分组、请求、模式、日期、机位、经纬度、题材、场景、预期行动。
SPECS: tuple[
    tuple[str, str, str, str, str, str, tuple[float, float] | None, str, str, str], ...
] = (
    (
        "L2-001",
        "galaxy",
        "这周末去天荒坪拍银河，制定拍摄计划",
        "planning",
        "2026-10-03",
        "天荒坪",
        (30.47, 119.53),
        "银河",
        "normal",
        "go",
    ),
    (
        "L2-002",
        "galaxy",
        "2026-10-03去天荒坪拍星空，制定拍摄计划",
        "planning",
        "2026-10-03",
        "天荒坪",
        (30.47, 119.53),
        "星空",
        "normal",
        "go",
    ),
    (
        "L2-003",
        "galaxy",
        "今晚去海堤拍银河现在走还来得及吗",
        "live",
        "2026-09-28",
        "海堤",
        (31.40, 121.80),
        "银河",
        "normal",
        "go",
    ),
    (
        "L2-004",
        "live",
        "今晚去东岸拍火烧云值得冲吗",
        "live",
        "2026-09-28",
        "东岸",
        (31.25, 121.52),
        "火烧云",
        "normal",
        "go",
    ),
    (
        "L2-005",
        "live",
        "明天去崇明东滩拍晚霞值得冲吗",
        "live",
        "2026-09-29",
        "崇明东滩",
        (31.52, 121.95),
        "晚霞",
        "normal",
        "go",
    ),
    (
        "L2-006",
        "planning",
        "2026-09-30去西岸拍日落，制定拍摄计划",
        "planning",
        "2026-09-30",
        "西岸",
        (31.21, 121.47),
        "日落",
        "normal",
        "go",
    ),
    (
        "L2-007",
        "sun",
        "2026-10-01去西岸拍日出",
        "inspiration",
        "2026-10-01",
        "西岸",
        (31.21, 121.47),
        "日出",
        "normal",
        "go",
    ),
    (
        "L2-008",
        "planning",
        "2026-10-02去西岸拍蓝调，制定拍摄计划",
        "planning",
        "2026-10-02",
        "西岸",
        (31.21, 121.47),
        "蓝调",
        "normal",
        "go",
    ),
    (
        "L2-009",
        "galaxy",
        "2026-10-03去海堤拍银河",
        "inspiration",
        "2026-10-03",
        "海堤",
        (31.40, 121.80),
        "银河",
        "normal",
        "go",
    ),
    (
        "L2-010",
        "polar",
        "2027-06-04去北极圈拍日落",
        "inspiration",
        "2027-06-04",
        "北极圈",
        (78.0, 15.0),
        "日落",
        "polar",
        "risk",
    ),
    (
        "L2-011",
        "galaxy",
        "2026-10-05想拍银河",
        "inspiration",
        "2026-10-05",
        "",
        None,
        "银河",
        "normal",
        "wait",
    ),
    (
        "L2-012",
        "live",
        "今晚去东岸拍火烧云值得冲吗",
        "live",
        "2026-09-28",
        "东岸",
        (31.25, 121.52),
        "火烧云",
        "missing_weather",
        "wait",
    ),
)


def _fixture(spec: tuple[Any, ...]) -> tuple[dict[str, Any], dict[str, Any]]:
    """由一个场景定义构造黄金期望和独占工具/模型响应。"""
    case_id, group, request, mode, day, site, coordinates, subject, scenario, verdict = spec
    galaxy = subject in {"银河", "星空"}
    tools = (GALAXY_TOOLS if galaxy else SUN_TOOLS).copy() if site else []
    if subject in {"日出", "朝霞", "蓝调"}:
        tools = [name for name in tools if name != "sunset_glow_score"]
    if site and mode in {"planning", "live"}:
        tools.append("match_sites")
    profile: dict[str, Any] = {}
    if site and coordinates:
        profile = {
            "camera_body": "松下 S5M2" if case_id == "L2-001" else "",
            "favorite_spots": [
                {
                    "name": site,
                    "latitude": coordinates[0],
                    "longitude": coordinates[1],
                    "coordinate_system": "WGS84",
                    "tz_offset": "+08:00",
                    "subject": "银河",
                }
            ],
        }
    context = {"user_id": f"eval-{case_id}", "requested_at": REQUESTED_AT, "tz_offset": "+08:00"}
    degraded = {
        "L2-010": "needs_data",
        "L2-011": "missing_favorite_spot",
        "L2-012": "needs_data",
    }.get(case_id, "")
    if scenario == "polar":
        verdict = "wait"
    if case_id in {"L2-001", "L2-002", "L2-009"}:
        degraded = "distant_forecast"
        verdict = "wait"
    if case_id == "L2-008":
        degraded = "needs_data"  # 未明确晨暮的蓝调不能静默默认日落。
        verdict = "wait"
    semantic: dict[str, Any] = {
        "target_date": day,
        "location": site,
        "subject_type": subject,
        "verdict": verdict,
        "degraded": degraded,
        "tools": tools,
        "require_action": bool(site),
        "forbidden_locations": ["上海"] if site else [],
        "forbidden_subjects": ["银河"] if subject == "火烧云" else [],
    }
    if coordinates:
        semantic["coordinates"] = list(coordinates)
    if "match_sites" in tools:
        semantic["match_subject"] = {"火烧云": "日落", "晚霞": "日落"}.get(subject, subject)
    if case_id == "L2-001":
        semantic["profile_camera"] = "松下 S5M2"
    if scenario == "missing_weather":
        semantic["unavailable_tools"] = ["weather_forecast", "sunset_glow_score"]
    fact_tool = "moon_phase" if galaxy else "sun_times"
    fact_field = (
        "月相名称"
        if galaxy
        else "日出"
        if subject == "日出"
        else "提示"
        if scenario == "polar"
        else "日落"
    )
    fact_value = (
        "新月"
        if galaxy
        else "05:45"
        if subject == "日出"
        else "极昼期间无日出日落"
        if scenario == "polar"
        else "18:05"
    )
    if site:
        semantic["facts"] = [
            {
                "tool": fact_tool,
                "path": [fact_field],
                "value": fact_value,
                "claim_in_conclusion": not degraded,
            }
        ]
    expect = {
        "min_evidence": 0 if not site or scenario == "polar" else 1,
        "max_locations": 1,
        "allow_degraded": bool(degraded),
    }
    case = {
        "id": case_id,
        "group": group,
        "request": request,
        "mode": mode,
        "scenario": scenario,
        "request_context": context,
        "profile": profile,
        "semantic": semantic,
        "expect": expect,
    }
    if not site:
        return case, {
            "case_id": case_id,
            "kind": "offline_fixture",
            "request": request,
            "responses": {},
            "tool_results": {},
        }
    result: dict[str, dict[str, Any]] = {
        "weather_forecast": {
            "每日预报": [{"日期": day, "平均云量（%）": 18}],
            "数据来源": "离线合成",
        },
    }
    if galaxy:
        result.update(
            {
                "moon_phase": {"月相名称": "新月", "照亮比例（%）": 2, "数据来源": "离线合成"},
                "moon_events": {"月升": "09:00", "月落": "20:00", "数据来源": "离线合成"},
                "galaxy_visibility": {
                    "可见窗口": [{"开始": "20:10", "结束": "23:50"}],
                    "数据来源": "离线合成",
                },
            }
        )
    else:
        result.update(
            {
                "sun_times": {"日出": "05:45", "日落": "18:05", "数据来源": "离线合成"},
                "sunset_glow_score": {"评分（0-100）": 62, "数据来源": "离线合成"},
            }
        )
    if scenario == "polar":
        result["sun_times"] = {"提示": fact_value, "数据来源": "离线合成"}
        result["sunset_glow_score"] = {
            "评分（0-100）": 0,
            "提示": "极昼无日落窗口",
            "数据来源": "离线合成",
        }
        semantic["facts"] = []
    if scenario == "missing_weather":
        result["weather_forecast"] = {"error": "离线夹具：天气不可用"}
        result["sunset_glow_score"] = {"error": "离线夹具：评分不可用"}
    if "match_sites" in tools:
        result["match_sites"] = {"匹配结果": [], "数据来源": "离线合成"}
    # 补齐正常样本的时间与完整度；失败夹具保持失败，不能被元数据修饰为成功。
    for name in ("weather_forecast", "sunset_glow_score"):
        if name not in result or "error" in result[name]:
            continue
        result[name].update(
            {
                "位置": f"{coordinates[0]},{coordinates[1]}",
                "时区": "+08:00",
                "获取时间": REQUESTED_AT,
                "有效开始时间": "2026-09-28T00:00:00+08:00",
                "有效结束时间": "2026-10-06T00:00:00+08:00",
            }
        )
    if scenario != "missing_weather":
        result["weather_forecast"]["每日预报"][0].update(
            {
                "最低能见度（km）": 25,
                "最高降水概率（%）": 10,
                "最大风速（km/h）": 15,
            }
        )
    if "sun_times" in result:
        result["sun_times"].update({"日期": day, "时区": "+08:00"})
    if scenario == "normal" and not galaxy:
        result["sunset_glow_score"].update(
            {
                "日期": day,
                "目标时刻": f"{day}T18:05:00+08:00",
                "缺失字段": [],
            }
        )
    evidence = [
        {
            "tool": fact_tool,
            "field": fact_field,
            "confidence": "high",
            "note": f"离线合成：{fact_field} {fact_value}",
        }
    ]
    if scenario == "missing_weather":
        evidence = [
            {
                "tool": "weather_forecast",
                "field": "虚构天气",
                "confidence": "high",
                "note": "错误模型自报：天气完美，值得出发",
            }
        ]
    if case_id == "L2-001":
        evidence.append(
            {
                "tool": "user_profile",
                "field": "器材",
                "confidence": "medium",
                "note": "用户档案：松下 S5M2",
            }
        )
    conclusion = f"{day}去{site}拍{subject}，依据{fact_field}{fact_value}，"
    conclusion += (
        "建议暂缓并补查天气。"
        if scenario == "missing_weather"
        else "建议谨慎评估。"
        if scenario == "polar"
        else "建议按计划出发。"
    )
    card: dict[str, Any] = {
        "conclusion": conclusion,
        "evidence": evidence,
        "confidence": "high" if scenario == "missing_weather" else "low" if degraded else "medium",
        "verdict": "go" if scenario == "missing_weather" else verdict,
        "time_window": f"{day} 17:30-18:30" if not galaxy else f"{day} 20:00-23:30",
        "locations": [{"name": site, "reason": "用户档案中带坐标的机位"}],
        "params": [],
        "alternatives": [],
        "degraded": "" if scenario == "missing_weather" else degraded,
        "target_date": day,
        "subject_type": subject,
    }
    return case, {
        "case_id": case_id,
        "kind": "offline_fixture",
        "request": request,
        "responses": {"card": card},
        "tool_results": result,
    }


def main() -> None:
    """保留既有 10 个工具用例，仅更新 R0-7 的管线期望与独立夹具。"""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    rows = [_fixture(spec) for spec in SPECS]
    golden["说明"] = (
        "L2 语义评估 v2：12 条独立离线管线夹具、10 条工具断言；所有 v2 数据为合成样本。"
    )
    golden["版本"] = "2026-09-28-r0-7"
    golden["pipeline_cases"] = [case for case, _ in rows]
    GOLDEN.write_text(json.dumps(golden, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    CASSETTES.mkdir(parents=True, exist_ok=True)
    for _, cassette in rows:
        (CASSETTES / f"{cassette['case_id']}.json").write_text(
            json.dumps(cassette, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
