"""U4 前置反例：现行计价、未知用量、输出限制与完整综合 JSON。"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from openai import AsyncOpenAI

from lighttrail.adapters.llm.client import ChatClient, LLMError
from lighttrail.composition import build_client, build_ledger
from lighttrail.config import Settings
from lighttrail.contracts.context import RequestContext
from lighttrail.contracts.models import DecisionCard, Intent
from lighttrail.infra.quota import QuotaLedger
from lighttrail.infra.validation import parse_with_retry
from lighttrail.orchestrator.context import PipelineContext
from lighttrail.orchestrator.pipelines import PipelineEnv, _build_synthesis_prompt
from lighttrail.orchestrator.synthesis import (
    MAX_SUMMARY_CHARS,
    SynthesisSummaryError,
    build_synthesis_json,
)

NOW = datetime(2026, 10, 7, 8, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("day", "hour", "expected"),
    [(8, 0, 10.8), (8, 9, 10.8), (8, 10, 5.4), (8, 23, 5.4), (7, 8, 5.4)],
)
def test_peak_boundaries_and_holiday(day: int, hour: int, expected: float) -> None:
    """北京时间 08 含、18 不含；明确列出的国庆日期不执行峰价。"""
    ledger = QuotaLedger(now=datetime(2026, 10, day, hour, tzinfo=timezone.utc).timestamp())
    assert ledger.record("ecnu-max", 10000, 2000) == pytest.approx(expected)


def test_current_pricing_cache_and_configuration(tmp_path: Path) -> None:
    """缓存是输入子集；max 谷价全缓存 0.6+2.4=3，生产装配可覆盖。"""
    ledger = QuotaLedger(now=NOW.timestamp())
    assert ledger.record("ecnu-max", 10000, 2000, cached_input_tokens=10000) == pytest.approx(3)
    configured = build_ledger(
        Settings(
            _env_file=None,
            data_dir=str(tmp_path),
            quota_weekly_credits=42,
            quota_pricing={"synthetic": {"input_per_m": 2, "output_per_m": 4}},
            quota_peak_hours=None,
        ),
        now=NOW.timestamp(),
    )
    assert configured.record("synthetic", 1000000, 1000000) == 6
    assert configured.usage().weekly_ratio == pytest.approx(6 / 42)
    ledger._records.extend([(NOW.timestamp() - 7 * 86400, 100), (NOW.timestamp() + 1, 100)])
    assert ledger.usage().weekly_ratio == pytest.approx(3 / 20000)


def test_thinking_is_output_subset_not_added_twice() -> None:
    """completion 包含 reasoning，flat 缓存拆分兼容且不重复计费。"""
    resp = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=10000,
            completion_tokens=2000,
            prompt_cache_hit_tokens=4000,
            prompt_cache_miss_tokens=6000,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=1500),
        )
    )
    ledger = QuotaLedger(now=NOW.timestamp())
    client = ChatClient("fake-u4", "http://127.0.0.1:1", quota=ledger)
    try:
        observation = client._record_usage({"model": "ecnu-max"}, resp)
        assert observation["估算_credits"] == pytest.approx(4.44)
        assert observation["输出含思考_tokens"] == 2000
        assert observation["其中思考_tokens"] == 1500
        assert ledger.usage().total_credits == pytest.approx(4.44)
    finally:
        client.close()


@pytest.mark.parametrize(
    "usage",
    [
        None,
        SimpleNamespace(prompt_tokens=None, completion_tokens=2),
        SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=2,
            prompt_tokens_details=SimpleNamespace(cached_tokens=11),
        ),
        SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=2,
            prompt_tokens_details=SimpleNamespace(cached_tokens=2),
            prompt_cache_hit_tokens=3,
        ),
    ],
)
def test_invalid_usage_is_unknown_not_zero(usage: Any) -> None:
    """无效及缺失 usage 不记确定零费用。"""
    ledger = QuotaLedger(now=NOW.timestamp())
    client = ChatClient("fake-u4", "http://127.0.0.1:1", quota=ledger)
    try:
        obs = client._record_usage({"model": "ecnu-max"}, SimpleNamespace(usage=usage))
        assert obs["估算_credits"] is None and obs["费用状态"] == "未知"
        assert ledger.usage().total_credits is None
        assert ledger.usage().unknown_attempts == 1
        assert "非平台余额" in ledger.usage().scope
    finally:
        client.close()


def test_cache_unknown_is_estimate_not_confirmed_charge() -> None:
    """有总输入/输出但无缓存拆分，只能按未缓存价估算。"""
    ledger = QuotaLedger(now=NOW.timestamp())
    client = ChatClient("fake-u4", "http://127.0.0.1:1", quota=ledger)
    try:
        obs = client._record_usage(
            {"model": "ecnu-max"},
            SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10000, completion_tokens=2000)),
        )
        assert obs["缓存输入_tokens"] is None
        assert obs["估算_credits"] == pytest.approx(5.4)
        assert ledger.usage().observed_credits == pytest.approx(5.4)
        assert ledger.usage().total_credits is None
    finally:
        client.close()


@pytest.mark.parametrize("limit_field", ["max_tokens", "max_completion_tokens"])
def test_sync_sdk_cap_survives_json_repairs(tmp_path: Path, limit_field: str) -> None:
    """真实 SDK 内存 body：同步入口的三次 JSON 修复均携带同一输出限制。"""
    bodies: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        """两次非法结构后返回合成卡。"""
        bodies.append(json.loads(request.content))
        text = (
            "非法"
            if len(bodies) < 3
            else json.dumps({"conclusion": "合成", "evidence": [], "confidence": "low"})
        )
        return httpx.Response(
            200,
            json={
                "id": "offline",
                "object": "chat.completion",
                "created": 0,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    client = build_client(
        Settings(
            _env_file=None,
            api_key="fake-u4",
            data_dir=str(tmp_path),
            max_output_tokens=91,
            output_limit_field=limit_field,
        )
    )
    sdk = AsyncOpenAI(
        api_key="fake-u4",
        base_url="http://127.0.0.1:1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    client._async_client = sdk
    try:
        parse_with_retry(
            DecisionCard,
            lambda prompt: client.chat([{"role": "user", "content": prompt}], model="ecnu-max")[
                "content"
            ],
            "合成",
        )
        assert len(bodies) == 3
        assert all(body[limit_field] == 91 for body in bodies)
        assert all(
            ("max_completion_tokens" if limit_field == "max_tokens" else "max_tokens") not in body
            for body in bodies
        )
        assert sdk.max_retries == 0
    finally:
        client.close()
        import asyncio

        asyncio.run(sdk.close())


async def test_truncated_valid_json_rejected_and_usage_retained(tmp_path: Path) -> None:
    """即使 length 返回可解析 JSON，也必须失败；费用仅计一次，不 HTTP 重试。"""
    bodies: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        """返回已截断的合成成功响应。"""
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "offline",
                "object": "chat.completion",
                "created": 0,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "{}"},
                        "finish_reason": "length",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10000,
                    "completion_tokens": 2000,
                    "total_tokens": 12000,
                    "prompt_tokens_details": {"cached_tokens": 4000},
                    "completion_tokens_details": {"reasoning_tokens": 1500},
                },
            },
        )

    ledger = QuotaLedger(now=NOW.timestamp())
    client = build_client(
        Settings(_env_file=None, api_key="fake-u4", data_dir=str(tmp_path)), quota=ledger
    )
    sdk = AsyncOpenAI(
        api_key="fake-u4",
        base_url="http://127.0.0.1:1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    client._async_client = sdk
    try:
        with pytest.raises(LLMError, match="截断") as error:
            await client.acall([{"role": "user", "content": "合成"}], model="ecnu-max")
        assert len(bodies) == 1
        assert error.value.observation["估算_credits"] == pytest.approx(4.44)
        assert ledger.usage().observed_credits == pytest.approx(4.44)
    finally:
        await client.aclose()
        await sdk.close()


def test_long_summary_order_time_and_original_integrity() -> None:
    """一千小时与字段逆序均保留目标窗口、太阳与缺失；原始结果逐字不变。"""
    from datetime import timedelta

    first = NOW.replace(hour=0)
    data = {
        "目标日期": "2026-10-07",
        "目标时区": "+08:00",
        "目标坐标": "31.0,121.0",
        "目标机位": "合成",
        "sun_times": {
            "日期": "2026-10-07",
            "时区": "+08:00",
            "日落": "17:34",
            "日落_iso": "2026-10-07T17:34:00+08:00",
            "黄金时刻（暮）": [{"开始": "17:00", "结束": "17:40"}],
            "事件状态": {"天文昏影终": {"状态": "不存在", "原因": "合成"}},
        },
        "weather_forecast": {
            "位置": "31.0,121.0",
            "时区": "+08:00",
            "获取时间": NOW.isoformat(),
            "有效开始时间": first.isoformat(),
            "有效结束时间": (first + timedelta(hours=1000)).isoformat(),
            "每日预报": [
                {"日期": "2026-10-07", "平均云量（%）": None},
                {"日期": "2026-10-08", "平均云量（%）": 99},
            ],
            "逐小时预报": [
                {
                    "时间": (first + timedelta(hours=i)).isoformat(),
                    "cloud_cover": None,
                    "visibility": 20000,
                    "precipitation_probability": 10,
                    "wind_speed_10m": 12,
                    "irrelevant": "x" * 4000,
                }
                for i in range(1000)
            ],
        },
    }
    original = copy.deepcopy(data)
    encoded = build_synthesis_json(data, now=NOW, tz_offset="+08:00", subject="火烧云")
    assert len(encoded) <= MAX_SUMMARY_CHARS

    def reverse(value: Any) -> Any:
        """递归倒转字段顺序，不改变值。"""
        if isinstance(value, dict):
            return {key: reverse(value[key]) for key in reversed(value)}
        if isinstance(value, list):
            return [reverse(row) for row in value]
        return value

    assert (
        build_synthesis_json(reverse(data), now=NOW, tz_offset="+08:00", subject="火烧云")
        == encoded
    )
    parsed = json.loads(encoded)
    hours = parsed["weather_forecast"]["逐小时预报"]
    assert 1 <= len(hours) <= 7
    assert [row["时间"] for row in hours] == [
        (first + timedelta(hours=i)).isoformat() for i in range(7, 13)
    ]
    assert parsed["weather_forecast"]["每日预报"] == [{"日期": "2026-10-07", "平均云量（%）": None}]
    assert parsed["sun_times"]["黄金时刻（暮）"] == original["sun_times"]["黄金时刻（暮）"]
    assert parsed["完整证据资格"]["状态"] == "needs_data"
    assert parsed["完整证据资格"]["缺失原因"]
    assert any(row.get("省略条数") == 994 for row in parsed["摘要边界"]["省略"])
    assert data == original


def test_summary_oversize_fails_without_partial_json() -> None:
    """无法完整保存关键原因时拒绝综合，不能靠 JSON 字符截断通过。"""
    with pytest.raises(SynthesisSummaryError, match="12000"):
        build_synthesis_json(
            {
                "weather_forecast": {"error": "合成"},
                "目标日期": "2026-10-07",
                **{f"合成工具{i}": "x" * 400 for i in range(40)},
            },
            now=NOW,
            tz_offset="+08:00",
            subject="火烧云",
        )


@pytest.mark.parametrize("failure", ["401", "503", "timeout"])
async def test_unknown_error_attempts_are_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """真实 SDK 内存故障：401 一次、503/读取超时三次；未知扣费逐次登记。"""
    from tenacity import wait_none

    import lighttrail.adapters.llm.client as module

    monkeypatch.setattr(module, "wait_exponential", lambda **_: wait_none())
    monkeypatch.setattr(module, "wait_random", lambda *_: wait_none())
    attempts: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        """捕获每次 body，模拟无法确定是否扣费的发送。"""
        attempts.append(json.loads(request.content))
        if failure == "timeout":
            raise httpx.ReadTimeout("合成超时", request=request)
        return httpx.Response(int(failure), json={"error": {"message": "合成"}})

    ledger = QuotaLedger(now=NOW.timestamp())
    client = build_client(
        Settings(_env_file=None, api_key="fake-u4", data_dir=str(tmp_path), max_output_tokens=93),
        quota=ledger,
    )
    sdk = AsyncOpenAI(
        api_key="fake-u4",
        base_url="http://127.0.0.1:1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    client._async_client = sdk
    try:
        with pytest.raises(LLMError):
            await client.acall([{"role": "user", "content": "合成"}], model="ecnu-max")
        count = 1 if failure == "401" else 3
        assert len(attempts) == ledger.usage().unknown_attempts == count
        assert all(body["max_tokens"] == 93 for body in attempts)
        assert ledger.usage().total_credits is None
        assert client.queue_position() == 0
    finally:
        await client.aclose()
        await sdk.close()


async def test_truncation_production_pipeline_degrades_and_restores(tmp_path: Path) -> None:
    """长度截断贯穿真实装配/SDK/API后保守保存，重建恢复零调用。"""
    from evals.u4_rehearsal import run_case

    summary = await run_case(tmp_path / "data", tmp_path / "evidence", "output_truncated")
    assert summary["verdict"] == "wait" and summary["restoration_attempts"] == 0
    card = json.loads((tmp_path / "evidence" / "original-result.json").read_text(encoding="utf-8"))[
        "card"
    ]
    assert card["confidence"] == "low"
    assert "synthesis_failed" in card["degraded"]
    assert summary["observed_usage"]["observed_credits"] == pytest.approx(5.4)


def test_missing_price_preserves_response_usage_as_unknown() -> None:
    """未配置模型价格不猜零费用，也不丢弃可用模型结果。"""
    ledger = QuotaLedger(now=NOW.timestamp())
    client = ChatClient("fake-u4", "http://127.0.0.1:1", quota=ledger)
    try:
        obs = client._record_usage(
            {"model": "synthetic-unpriced"},
            SimpleNamespace(
                usage=SimpleNamespace(
                    prompt_tokens=10,
                    completion_tokens=2,
                    prompt_tokens_details=SimpleNamespace(cached_tokens=0),
                )
            ),
        )
        assert obs["输入_tokens"] == 10
        assert obs["估算_credits"] is None
        assert ledger.usage().total_credits is None
    finally:
        client.close()


def test_current_ecnu_price_and_weekly_window() -> None:
    """国庆谷价 10k 输入与 2k 输出为 5.4，7 天窗口为 20000。"""
    ledger = QuotaLedger(now=datetime(2026, 10, 7, 8, tzinfo=timezone.utc).timestamp())
    assert ledger.record("ecnu-max", 10000, 2000) == pytest.approx(5.4)
    assert ledger.usage().weekly_ratio == pytest.approx(5.4 / 20000)


async def test_sdk_output_cap_reaches_http_body(tmp_path: Path) -> None:
    """生产装配的自定义输出上限必须进入真实 SDK 请求 body。"""
    bodies: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        """记录内存请求，不连接网络。"""
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "offline",
                "object": "chat.completion",
                "created": 0,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    client = build_client(
        Settings(
            _env_file=None,
            LLM_API_KEY="fake-u4",
            LIGHTTRAIL_DATA_DIR=str(tmp_path),
            LLM_MAX_OUTPUT_TOKENS=77,
        )
    )
    client._async_client = AsyncOpenAI(
        api_key="fake-u4",
        base_url="http://127.0.0.1:1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    try:
        await client.acall([{"role": "user", "content": "合成"}])
        assert bodies[0]["max_tokens"] == 77
    finally:
        await client.aclose()


def test_long_weather_synthesis_is_complete_json() -> None:
    """长预报不能截断 JSON 或丢失目标与日期信息。"""
    ctx = PipelineContext(
        user_request="今晚火烧云是否值得去",
        intent=Intent(subject_type="火烧云", time_hint="今晚", mode="live"),
    )
    ctx.data = {
        "weather_forecast": {
            "每日预报": [{"日期": "2026-10-07", "平均云量（%）": None}],
            "逐小时预报": [{"时间": "2026-10-07T17:00:00+08:00", "cloud_cover": None}] * 1000,
        },
        "目标日期": "2026-10-07",
        "目标机位": "合成",
        "目标坐标": "31.0,121.0",
        "目标时区": "+08:00",
        "sun_times": {"日期": "2026-10-07", "时区": "+08:00", "日落": "17:34"},
    }
    env = PipelineEnv(dispatch=lambda *_: "{}", reason=lambda *_: "{}", request=RequestContext())
    prompt = _build_synthesis_prompt(ctx, env)
    fragment = prompt.split("采集数据（JSON）：\n")[1].split("\n\n")[0]
    data = json.loads(fragment)
    assert data["目标坐标"] == "31.0,121.0"
    assert data["weather_forecast"]["每日预报"][0]["平均云量（%）"] is None
