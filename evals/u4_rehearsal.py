"""U4 离线准备：生产决策链路、供应商发送账本与同目录重建恢复。

仅使用内存 HTTP 传输、合成档案和专用目录；禁止业务 socket 连接。该脚本不是
真实调用入口，不读取 .env 或个人档案，不改变生产实现或增加回流功能。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import socket
import uuid
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
from openai import AsyncOpenAI
from tenacity import wait_none

import lighttrail.adapters.llm.client as llm_module
import lighttrail.infra.http as http_module
from lighttrail.adapters.llm.client import ChatClient
from lighttrail.api.app import ApiDeps, create_app
from lighttrail.api.routes import get_request_context
from lighttrail.api.session import SessionManager
from lighttrail.composition import build_application, build_client, build_ledger
from lighttrail.config import Settings
from lighttrail.contracts.context import RequestContext
from lighttrail.infra.quota import QuotaLedger

NOW = datetime(2026, 10, 7, 8, tzinfo=timezone.utc)
QUESTION = "今晚火烧云是否值得去"
FAKE_KEY = "u4-offline-private-placeholder"
SPOT = {
    "name": "U4合成西岸",
    "latitude": 31.0,
    "longitude": 121.0,
    "coordinate_system": "WGS84",
    "tz_offset": "+08:00",
    "subject_types": ["火烧云"],
    "source": "人工合成，非个人机位",
}
EQUIPMENT = {
    "camera_body": "松下 S5M2",
    "lenses": ["24-105mm F4", "70-300mm", "契卡 14mm 定焦"],
    "preferences": ["风光", "火烧云"],
}


def write_json(path: Path, value: Any) -> None:
    """写入可审阅的合成证据。

    Args:
        path: 专用证据文件路径。
        value: JSON 可序列化数据。
    """
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class OfflineWire:
    """捕获真实 SDK / 天气工具最终 HTTP 内容，并注入可计数故障。"""

    def __init__(self, mode: str) -> None:
        """初始化独立场景账本。

        Args:
            mode: 正常、重试、缺失或非重试错误场景。
        """
        self.mode = mode
        self.requests: list[dict[str, Any]] = []
        self.counts: Counter[str] = Counter()

    def respond(self, request: httpx.Request) -> httpx.Response:
        """返回纯合成 HTTP 响应，任何未知地址立即报错。

        Args:
            request: 生产代码构造的最终 HTTP 请求。

        Returns:
            不涉及网络的合成响应。
        """
        model = request.url.host == "127.0.0.1" and request.url.port == 1
        weather = request.url.host == "api.open-meteo.com"
        if not model and not weather:
            raise AssertionError("演练出现未授权接收方")
        provider = "ECNU（内存替身）" if model else "Open-Meteo（内存替身）"
        key = "model" if model else str(request.url)
        self.counts[key] += 1
        attempt = self.counts[key]
        status = 200
        if model and self.mode == "non_retry_error":
            status = 401
        elif (
            weather
            and self.mode == "weather_failed"
            or self.mode == "retry_and_schema"
            and attempt % 3 != 0
        ):
            status = 503
        body = json.loads(request.content) if model else None
        self.requests.append(
            {
                "provider": provider,
                "method": request.method,
                "url": str(request.url),
                "headers": {
                    "Authorization": "[隐藏]" if model else "未使用",
                    "Content-Type": request.headers.get("content-type"),
                    "User-Agent": request.headers.get("user-agent"),
                },
                "body": body,
                "response_status": status,
            }
        )
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "合成故障"}})
        if weather:
            first = NOW.replace(hour=0)
            hours = 24 * int(request.url.params["forecast_days"])
            return httpx.Response(
                200,
                json={
                    "latitude": 31.0,
                    "longitude": 121.0,
                    "utc_offset_seconds": 0,
                    "hourly": {
                        "time": [
                            (first + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M")
                            for i in range(hours)
                        ],
                        "cloud_cover": [50] * hours,
                        "cloud_cover_high": [30] * hours,
                        "cloud_cover_low": [10] * hours,
                        "visibility": [20000] * hours,
                        "precipitation_probability": [10] * hours,
                        "wind_speed_10m": [12] * hours,
                    },
                },
            )
        invalid = self.mode == "schema_failed" or (self.mode == "retry_and_schema" and attempt <= 6)
        card = {
            "conclusion": "合成西岸窗口可观察；先核对通勤和现场遮挡，再决定是否专程出门。",
            "verdict": "go",
            "confidence": "high",
            "evidence": [],
            "locations": [{"name": SPOT["name"], "reason": "唯一合成收藏"}],
            "time_window": "2026-10-07 17:00–18:00（+08:00），合成建议",
            "window_start": "2026-10-07T17:00:00+08:00",
            "window_end": "2026-10-07T18:00:00+08:00",
            "params": [{"name": "器材", "value": "S5M2 + 24-105mm F4", "reason": "合成演练"}],
            "alternatives": ["若无法及时到场，改为附近观察；没有路线查询"],
        }
        return httpx.Response(
            200,
            json={
                "id": "u4-synthetic-response",
                "object": "chat.completion",
                "created": int(NOW.timestamp()),
                "model": "ecnu-max",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "合成非法 JSON"
                            if invalid
                            else json.dumps(card, ensure_ascii=False),
                        },
                        "finish_reason": "length" if self.mode == "output_truncated" else "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10000,
                    "completion_tokens": 2000,
                    "total_tokens": 12000,
                    "prompt_tokens_details": {"cached_tokens": 0},
                    "completion_tokens_details": {"reasoning_tokens": 1500},
                },
            },
        )


async def run_case(root: Path, evidence: Path, mode: str) -> dict[str, Any]:
    """经生产 API 演练生成、落盘、重建及恢复。

    Args:
        root: 本场景隔离数据目录。
        evidence: 本场景证据目录。
        mode: 离线故障模式。

    Returns:
        调用计数及关键验收断言。
    """
    evidence.mkdir(parents=True)
    wire = OfflineWire(mode)
    transport = httpx.MockTransport(wire.respond)
    original_get = http_module.get_json

    async def weather_get(url: str, **kwargs: Any) -> dict[str, Any]:
        """复用真实天气重试器，仅替换底层内存传输。"""
        async with httpx.AsyncClient(transport=transport, timeout=kwargs["timeout"]) as client:
            return await original_get(url, client=client, **kwargs)

    def assemble(at: datetime) -> tuple[Any, Any, ChatClient]:
        """重新创建生产装配与 FastAPI，不读取个人配置。"""
        settings = Settings(
            _env_file=None,
            LLM_API_KEY=FAKE_KEY,
            LLM_BASE_URL="http://127.0.0.1:1",
            LLM_MODEL="ecnu-plus",
            LLM_MODEL_REASON="ecnu-max",
            LLM_CONCURRENCY=1,
            LLM_MAX_OUTPUT_TOKENS=4096,
            LLM_OUTPUT_LIMIT_FIELD="max_tokens",
            LIGHTTRAIL_DATA_DIR=str(root),
            AMAP_WEB_SERVICE_KEY="",
            AMAP_JS_API_KEY="",
            AMAP_SECURITY_JS_CODE="",
            BAIDU_MAP_AK="",
        )
        client = build_client(settings, quota=build_ledger(settings, now=at.timestamp()))
        client._async_client = AsyncOpenAI(
            api_key=FAKE_KEY,
            base_url="http://127.0.0.1:1",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=transport),
        )
        services = build_application(settings, client=client)
        app = create_app(ApiDeps(services, SessionManager(root)))
        app.dependency_overrides[get_request_context] = lambda: RequestContext(requested_at=at)
        return app, services, client

    with (
        patch("lighttrail.tools.weather._now", return_value=NOW),
        patch(
            "lighttrail.tools.weather._base_url",
            return_value="https://api.open-meteo.com/v1/forecast",
        ),
        patch.object(http_module, "get_json", weather_get),
        patch.object(http_module, "wait_exponential", lambda **_: wait_none()),
        patch.object(http_module, "wait_random", lambda *_: wait_none()),
        patch.object(llm_module, "wait_exponential", lambda **_: wait_none()),
        patch.object(llm_module, "wait_random", lambda *_: wait_none()),
    ):
        app, services, client = assemble(NOW)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://localhost"
            ) as api:
                assert (await api.put("/api/profile", json=EQUIPMENT)).status_code == 200
                spot_response = await api.post("/api/favorite-spots", json=SPOT)
                assert spot_response.status_code == 201, spot_response.text
                response = await api.post("/api/decide", json={"request": QUESTION})
                frames = [
                    json.loads(line[6:])
                    for line in response.text.splitlines()
                    if line.startswith("data: ")
                ]
                done = frames[-1]
                assert done["type"] == "done" and done["saved"] is True
                url = f"/api/sessions/{done['session_id']}/results/{done['result_id']}"
                original = (await api.get(url)).json()
            write_json(
                evidence / "local-inputs.json",
                {"profile": EQUIPMENT, "favorite": SPOT, "decide": {"request": QUESTION}},
            )
            (evidence / "response.sse.txt").write_text(response.text, encoding="utf-8")
            write_json(evidence / "original-result.json", original)
            observed_usage = asdict(client.quota.usage())
            observed_usage["total_credits"] = client.quota.usage().total_credits
        finally:
            services.close()
            await client.aclose()
        before = len(wire.requests)
        app, services, client = assemble(NOW + timedelta(days=1))
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://localhost"
            ) as api:
                restored = await api.get(url)
                assert restored.status_code == 200 and restored.json() == original
                assert len(wire.requests) == before
                assert restored.json()["card"]["target_date"] == "2026-10-07"
            write_json(evidence / "restored-result.json", restored.json())
        finally:
            services.close()
            await client.aclose()
    counts = Counter("model" if r["body"] is not None else "weather" for r in wire.requests)
    expected = {
        "normal": (1, 2),
        "retry_and_schema": (9, 6),
        "weather_failed": (1, 6),
        "schema_failed": (3, 2),
        "non_retry_error": (1, 2),
        "output_truncated": (1, 2),
    }[mode]
    assert (counts["model"], counts["weather"]) == expected, counts
    card = original["card"]
    if mode in {"weather_failed", "schema_failed", "non_retry_error", "output_truncated"}:
        assert card["verdict"] == "wait" and card["confidence"] == "low"
    else:
        assert card["data_status"] == "ready" and card["verdict"] == "go"
    encoded = json.dumps(wire.requests, ensure_ascii=False)
    assert FAKE_KEY not in encoded
    write_json(evidence / "supplier-requests.json", wire.requests)
    synthesis = []
    for index, sent in enumerate(wire.requests):
        if sent["body"] is None:
            continue
        assert sent["body"]["max_tokens"] == 4096
        for message in sent["body"]["messages"]:
            text = message.get("content", "")
            marker = "采集数据（JSON）：\n"
            if isinstance(text, str) and marker in text:
                encoded_summary = text.split(marker)[1].split("\n\n")[0]
                collected = json.loads(encoded_summary)
                assert len(encoded_summary) <= 12000
                assert collected["目标日期"] == "2026-10-07"
                assert collected["目标坐标"] == "31.0,121.0"
                assert len(collected["weather_forecast"].get("逐小时预报", [])) <= 7
                synthesis.append(
                    {"request_index": index, "chars": len(encoded_summary), "data": collected}
                )
    assert len(synthesis) == counts["model"]
    write_json(evidence / "synthesis-summary.json", synthesis)
    files = {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }
    summary = {
        "mode": mode,
        "http_attempts": dict(counts),
        "map_attempts": 0,
        "restoration_attempts": len(wire.requests) - before,
        "same_directory_new_fastapi_instance": True,
        "restore_exact_snapshot_next_day": True,
        "card_status": card["data_status"],
        "verdict": card["verdict"],
        "sdk_max_retries": 0,
        "LLM_CONCURRENCY": 1,
        "output_token_limit_sent": True,
        "output_limit": {
            "field": "max_tokens",
            "value": 4096,
            "thinking_enforcement": "供应商未明确确认，非费用硬上限",
        },
        "observed_usage": observed_usage,
        "synthesis_json_chars": [item["chars"] for item in synthesis],
        "data_directory": str(root),
        "local_files_sha256": files,
    }
    write_json(evidence / "summary.json", summary)
    return summary


async def rehearse(evidence: Path) -> None:
    """执行限定场景及故障演练，输出费用偏差反例。

    Args:
        evidence: 新的合成证据目录。
    """
    if evidence.exists():
        raise ValueError("证据目录已存在，请另选新目录，禁止覆盖历史证据")
    evidence.mkdir(parents=True)
    root = Path.cwd() / ".pytest-run-u4" / uuid.uuid4().hex
    results = []
    for mode in (
        "normal",
        "retry_and_schema",
        "weather_failed",
        "schema_failed",
        "non_retry_error",
    ):
        results.append(await run_case(root / mode, evidence / mode, mode))
    ledger = QuotaLedger(now=NOW.timestamp())
    local_cost = ledger.record("ecnu-max", 10000, 2000)
    assert local_cost == 5.4
    write_json(
        evidence / "rehearsal-summary.json",
        {
            "synthetic_only": True,
            "business_socket_connections_permitted": False,
            "real_business_requests": 0,
            "cases": results,
            "pricing_verification": {
                "synthetic_usage": {"input_miss": 10000, "output_including_thinking": 2000},
                "current_local_credits": local_cost,
                "official_valley_credits": 5.4,
                "official_peak_credits": 10.8,
                "source": "https://developer.ecnu.edu.cn/vitepress/llm/limit.html",
                "local_windows": "本应用已观测用量：7d/20000，非平台余额；未知尝试不记零费用",
                "official_default_window": "7d/20000；实际令牌额度待本人核对",
                "production_changed": True,
            },
        },
    )


def main() -> None:
    """运行离线证据脚本，仅允许 Windows 事件循环的本机唤醒连接。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    boundary = (Path.cwd() / "docs" / "audit" / "2026-10-07").resolve()
    if not evidence.is_relative_to(boundary):
        raise ValueError("证据目录须位于本批 audit 目录内")
    original_connect = socket.socket.connect

    def connect(sock: socket.socket, address: Any) -> None:
        """拒绝外部连接和模型替身端口，允许事件循环的内部 socketpair。"""
        if (
            not isinstance(address, tuple)
            or address[0] not in {"127.0.0.1", "::1"}
            or address[1] <= 1024
        ):
            raise AssertionError("演练禁止业务网络连接")
        original_connect(sock, address)

    with patch.object(socket.socket, "connect", connect):
        asyncio.run(rehearse(evidence))
    print("PASS: U4 五种离线场景、最终 HTTP 内容与重建恢复证据")


if __name__ == "__main__":
    main()
