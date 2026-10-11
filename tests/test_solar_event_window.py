"""U4 晨暮错配回归：实际生产 API/SDK、小时窗口资格与零外调恢复。"""

from __future__ import annotations

import copy
import json
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from openai import AsyncOpenAI

from evals import u4_rehearsal as rehearsal
from lighttrail import composition
from lighttrail.api.app import ApiDeps, create_app
from lighttrail.api.routes import get_request_context
from lighttrail.api.session import SessionManager
from lighttrail.application.router import RuleIntentRouter
from lighttrail.composition import build_application, build_client, build_ledger
from lighttrail.config import Settings
from lighttrail.contracts.context import RequestContext
from lighttrail.domain.solar_window import resolve_solar_window
from lighttrail.infra import http as http_module
from lighttrail.tools import astronomy

NOW = datetime(2026, 10, 9, 8, tzinfo=timezone.utc)
SPOT = {**rehearsal.SPOT, "name": "U4合成晨景", "subject_types": ["日出", "蓝调"]}
QUESTION = "2026-10-10 在U4合成晨景拍日出前蓝调加日出，持续到日出后是否值得去？"


async def exercise_case(root: Path, mode: str, tz: str = "+08:00") -> dict[str, Any]:
    """复用真实装配，仅替换供应商传输，落盘保留前后请求和完整快照。

    Args:
        root: 独立合成证据目录。
        mode: 完整、天气缺失、错误模型窗口或太阳缺失反例。
        tz: 明确请求偏移，用于跨 UTC 日期核验。

    Returns:
        最终摘要、卡片和供应商尝试数。
    """
    root.mkdir(parents=True, exist_ok=True)
    day = "2026-10-10"
    raw_sun = astronomy.sun_times(SPOT["latitude"], SPOT["longitude"], day, tz)
    evening = mode == "evening"
    period = "暮" if evening else "晨"
    blue = raw_sun[f"蓝调时刻（{period}）"][0]
    gold = raw_sun[f"黄金时刻（{period}）"][0]
    card_start = gold["开始_iso"] if evening else blue["开始_iso"]
    card_end = blue["结束_iso"] if evening else gold["结束_iso"]
    if mode == "wrong_model":
        card_start, card_end = f"{day}T17:00:00{tz}", f"{day}T18:00:00{tz}"
    if mode == "skips_blue":
        card_start = raw_sun["日出_iso"]
    question = (
        QUESTION if not evening else QUESTION.replace("日出前", "日落前").replace("日出", "日落")
    )
    wire = rehearsal.OfflineWire("normal")
    respond_parent = wire.respond

    def respond(request: httpx.Request) -> httpx.Response:
        """截获实际请求，返回完整或有针对性的离线反例。"""
        response = respond_parent(request)
        payload = response.json()
        if request.url.host == "api.open-meteo.com":
            hourly = payload["hourly"]
            local = [
                datetime.fromisoformat(at)
                .replace(tzinfo=timezone.utc)
                .astimezone(datetime.fromisoformat(card_start).tzinfo)
                for at in hourly["time"]
            ]
            indices = [
                i for i, at in enumerate(local) if mode != "afternoon_only" or 15 <= at.hour <= 20
            ]
            if mode == "missing_sample":
                indices = [
                    i
                    for i in indices
                    if not (local[i].date().isoformat() == day and local[i].hour == 6)
                ]
            for key, values in hourly.items():
                hourly[key] = [values[i] for i in indices]
            if mode == "null_sample":
                for i, at in enumerate([local[j] for j in indices]):
                    if at.date().isoformat() == day and at.hour == 6:
                        hourly["visibility"][i] = None
        else:
            payload["choices"][0]["message"]["content"] = json.dumps(
                {
                    "conclusion": "合成窗口建议，未查询路线或现场条件。",
                    "verdict": "go",
                    "confidence": "high",
                    "evidence": [],
                    "locations": [{"name": SPOT["name"]}],
                    "time_window": f"{card_start} 至 {card_end}",
                    "window_start": card_start,
                    "window_end": card_end,
                    "params": [{"name": "器材", "value": "S5M2 + 14mm"}],
                },
                ensure_ascii=False,
            )
        return httpx.Response(200, json=payload)

    def solar(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """仅太阳缺失反例替换本地计算结果，正常分支使用真实 Astral。"""
        value = copy.deepcopy(raw_sun)
        if mode in {"event_missing", "event_failed"}:
            value["日出"] = value["日出_iso"] = None
            value["事件状态"]["日出"] = {
                "状态": "事件不存在" if mode == "event_missing" else "计算失败",
                "原因": "合成极夜" if mode == "event_missing" else "合成计算故障",
            }
        return value

    transport = httpx.MockTransport(respond)
    original_get = http_module.get_json

    async def weather_get(url: str, **kwargs: Any) -> dict[str, Any]:
        """保留生产天气重试和解析，只替换 HTTP。"""
        async with httpx.AsyncClient(transport=transport) as client:
            return await original_get(url, client=client, **kwargs)

    sdks: list[AsyncOpenAI] = []

    def assemble(at: datetime) -> tuple[Any, Any, Any]:
        """从同一隔离目录重建真实应用，不读取 .env。"""
        settings = Settings(
            _env_file=None,
            LLM_API_KEY=rehearsal.FAKE_KEY,
            LLM_BASE_URL="http://127.0.0.1:1",
            LLM_CONCURRENCY=1,
            LLM_MAX_OUTPUT_TOKENS=4096,
            LIGHTTRAIL_DATA_DIR=str(root / "data"),
            AMAP_WEB_SERVICE_KEY="",
            AMAP_JS_API_KEY="",
            AMAP_SECURITY_JS_CODE="",
            BAIDU_MAP_AK="",
        )
        client = build_client(settings, quota=build_ledger(settings, now=at.timestamp()))
        sdk = AsyncOpenAI(
            api_key=rehearsal.FAKE_KEY,
            base_url="http://127.0.0.1:1",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=transport),
        )
        sdks.append(sdk)
        client._async_client = sdk
        services = build_application(settings, client=client)
        app = create_app(ApiDeps(services, SessionManager(root / "data")))
        app.dependency_overrides[get_request_context] = lambda: RequestContext(
            requested_at=at, tz_offset=tz
        )
        return app, services, client

    with (
        patch.object(rehearsal, "NOW", NOW),
        patch("lighttrail.tools.weather._now", return_value=NOW),
        patch.object(http_module, "get_json", weather_get),
        patch.object(
            composition,
            "TOOLS",
            tuple(
                replace(tool, func=solar) if tool.spec.name == "sun_times" else tool
                for tool in composition.TOOLS
            ),
        ),
    ):
        app, services, client = assemble(NOW)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://localhost"
            ) as api:
                assert (await api.put("/api/profile", json=rehearsal.EQUIPMENT)).status_code == 200
                assert (
                    await api.post("/api/favorite-spots", json={**SPOT, "tz_offset": tz})
                ).status_code == 201
                response = await api.post("/api/decide", json={"request": question})
                frames = [
                    json.loads(line[6:])
                    for line in response.text.splitlines()
                    if line.startswith("data: ")
                ]
                done = frames[-1]
                assert done["type"] == "done" and done["saved"] is True
                result_url = f"/api/sessions/{done['session_id']}/results/{done['result_id']}"
                original = (await api.get(result_url)).json()
                (root / "response.sse.txt").write_text(response.text, encoding="utf-8")
        finally:
            services.close()
            await client.aclose()
            await sdks[-1].close()
        before = len(wire.requests)
        app, services, client = assemble(NOW + timedelta(days=2))
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://localhost"
            ) as api:
                restored = await api.get(result_url)
                assert restored.status_code == 200 and restored.json() == original
                assert len(wire.requests) == before
        finally:
            services.close()
            await client.aclose()
            await sdks[-1].close()
    summaries = []
    for sent in wire.requests:
        if sent["body"] is None:
            continue
        assert sent["body"]["max_tokens"] == 4096
        for message in sent["body"]["messages"]:
            text = message.get("content", "")
            marker = "采集数据（JSON）：\n"
            if isinstance(text, str) and marker in text:
                summaries.append(json.loads(text.split(marker)[1].split("\n\n")[0]))
    assert summaries
    counts = Counter("model" if row["body"] is not None else "weather" for row in wire.requests)
    result = {
        "card": original["card"],
        "summary": summaries[0],
        "counts": dict(counts),
        "restore_calls": len(wire.requests) - before,
    }
    for name, data in {
        "supplier-requests.json": wire.requests,
        "synthesis-summary.json": summaries,
        "original-result.json": original,
        "restored-result.json": restored.json(),
        "summary.json": result,
    }.items():
        (root / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


@pytest.mark.parametrize(
    "mode",
    [
        "normal",
        "afternoon_only",
        "missing_sample",
        "null_sample",
        "wrong_model",
        "skips_blue",
        "event_missing",
        "event_failed",
        "evening",
    ],
)
async def test_production_solar_window(tmp_path: Path, mode: str) -> None:
    """目标天文窗口、实际小时天气与模型建议必须一致，恢复不得外调。"""
    result = await exercise_case(tmp_path, mode)
    summary, card = result["summary"], result["card"]
    assert summary["小时筛选规则"]["事件"] == ("日落" if mode == "evening" else "日出")
    assert result["restore_calls"] == 0
    assert result["counts"] == {"weather": 1, "model": 1}
    if mode in {"normal", "evening"}:
        assert card["data_status"] == "ready" and card["verdict"] == "go"
        assert "sunset_glow_score" not in summary
        if mode == "normal":
            hours = [
                datetime.fromisoformat(row["时间"]).hour
                for row in summary["weather_forecast"]["逐小时预报"]
            ]
            assert hours and all(4 <= hour <= 8 for hour in hours)
            assert 5 in hours and 6 in hours
    else:
        assert card["data_status"] == "needs_data" and card["verdict"] == "wait"
        assert card["confidence"] == "low" and card["missing_data"]
        assert not card["window_start"] and not card["params"]
        if mode in {"event_missing", "event_failed"}:
            assert ("事件不存在" if mode == "event_missing" else "计算失败") in str(
                card["missing_data"]
            )


@pytest.mark.parametrize(
    "text", ["日出前蓝调加日出，拍到日出后", "蓝调从日出前拍到日出后", "日出之后结束，之前拍蓝调"]
)
def test_explicit_morning_survives_keyword_order(text: str) -> None:
    """事件和阶段与题材关键词顺序无关。"""
    intent = RuleIntentRouter().route("2026-10-10 " + text)
    assert intent is not None
    assert intent.solar_event == "sunrise" and intent.solar_window == "blue_through_event"


def test_unspecified_blue_does_not_guess_sunset() -> None:
    """晨暮不明时必须保持歧义，模型分类不能覆盖原文事实。"""
    router = RuleIntentRouter()
    intent = router.route("想去拍蓝调")
    assert intent is not None and intent.solar_event == "ambiguous"
    assert (
        router.ground(intent.model_copy(update={"solar_event": "sunset"}), "想去拍蓝调").solar_event
        == "ambiguous"
    )


async def test_cross_utc_day_uses_absolute_window(tmp_path: Path) -> None:
    """+14晨间在前一UTC日，按绝对时刻筛选而不是截取时间字符串。"""
    result = await exercise_case(tmp_path, "normal", "+14:00")
    assert result["card"]["data_status"] == "ready"
    rows = result["summary"]["weather_forecast"]["逐小时预报"]
    assert rows
    assert all(
        datetime.fromisoformat(row["时间"]).utcoffset() == timedelta(hours=14) for row in rows
    )


def test_wrong_absolute_event_date_is_rejected() -> None:
    """本地目标日与太阳绝对事件日不一致时不能用显示钟点冒充。"""
    sun = astronomy.sun_times(31.0, 121.0, "2026-10-10", "+08:00")
    sun["日出_iso"] = "2026-10-09T06:00:00+08:00"
    resolved = resolve_solar_window(sun, "2026-10-10", "+08:00", "sunrise", "blue_through_event")
    assert resolved["缺失原因"] and resolved["开始"] is None
