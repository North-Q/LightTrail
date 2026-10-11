"""U1 决策资格反例：生产装配、合成收藏及离线模型，不访问外部服务。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from lighttrail.api.app import ApiDeps, create_app
from lighttrail.api.session import SessionManager
from lighttrail.composition import build_application
from lighttrail.config import Settings
from lighttrail.contracts.models import DecisionCard
from lighttrail.memory import MemoryManager
from lighttrail.orchestrator.evidence import qualify_card


class FalseGoClient:
    """坚持返回无依据 go/high 的离线模型。"""

    def __init__(self, broken: bool = False) -> None:
        """设置综合输出是否无法解析。"""
        self.broken = broken

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        """返回刻意捏造的依据和结论。"""
        return {
            "role": "assistant",
            "content": "invalid"
            if self.broken
            else json.dumps(
                {
                    "conclusion": "今晚值得冲，天气完美。",
                    "verdict": "go",
                    "confidence": "high",
                    "evidence": [
                        {
                            "tool": "weather_forecast",
                            "field": "不存在的字段",
                            "confidence": "high",
                            "note": "云量理想，可信度 100%",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        }

    async def acall(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        """异步离线通道。"""
        return self.chat(messages, **kwargs)

    def queue_position(self) -> int:
        """离线模型无需排队。"""
        return 0


@pytest.mark.parametrize("broken", [False, True])
async def test_failed_tools_cannot_be_go_high(tmp_path: Path, broken: bool) -> None:
    """失败工具与非法综合均须输出数据不足卡，禁止回落自由对话建议出发。"""
    memory = MemoryManager(tmp_path)
    memory.update_profile(
        {
            "favorite_spots": [
                {
                    "name": "合成西岸",
                    "latitude": 0.0,
                    "longitude": 0.0,
                    "coordinate_system": "WGS84",
                }
            ]
        }
    )
    services = build_application(
        Settings(_env_file=None, data_dir=str(tmp_path), reason_thinking=False),
        client=FalseGoClient(broken),
        memory=memory,
        dispatch=lambda name, args: json.dumps({"error": "离线替身：数据源不可用"}),
    )
    try:
        app = create_app(ApiDeps(services=services, sessions=SessionManager(tmp_path)))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://localhost"
        ) as client:
            response = await client.post("/api/decide", json={"request": "今晚火烧云是否值得去"})
        frames = [
            json.loads(line[5:].strip())
            for line in response.text.splitlines()
            if line.startswith("data:")
        ]
        cards = [frame["card"] for frame in frames if "card" in frame]
        assert len(cards) == 1, response.text
        card = cards[0]
        assert card["verdict"] == "wait"
        assert card["confidence"] == "low"
        assert card["data_status"] == "needs_data"
        assert card["degraded"]
        assert card["missing_data"]
        assert card["evidence"] == []
        assert "值得冲" not in card["conclusion"]
    finally:
        services.close()


def _qualified_data() -> dict[str, Any]:
    """固定目标、地点、太阳和完整天气，供资格反例单独篡改。"""
    return {
        "目标机位": "合成西岸",
        "目标坐标": "0.0,0.0",
        "目标时区": "+08:00",
        "sun_times": {"日期": "2026-10-06", "时区": "+08:00", "日落": "18:00"},
        "weather_forecast": {
            "位置": "0.0,0.0",
            "时区": "+08:00",
            "获取时间": "2026-10-06T07:00:00+00:00",
            "有效开始时间": "2026-10-06T00:00:00+08:00",
            "有效结束时间": "2026-10-07T00:00:00+08:00",
            "每日预报": [
                {
                    "日期": "2026-10-06",
                    "平均云量（%）": 50,
                    "最低能见度（km）": 25,
                    "最高降水概率（%）": 10,
                    "最大风速（km/h）": 15,
                }
            ],
        },
        "sunset_glow_score": {
            "日期": "2026-10-06",
            "位置": "0.0,0.0",
            "时区": "+08:00",
            "获取时间": "2026-10-06T07:00:00+00:00",
            "缺失字段": [],
            "目标时刻": "2026-10-06T18:00:00+08:00",
            "评分（0-100）": 90,
            "有效开始时间": "2026-10-06T00:00:00+08:00",
            "有效结束时间": "2026-10-07T00:00:00+08:00",
        },
    }


@pytest.mark.parametrize(
    "failure",
    [
        "sun",
        "weather",
        "glow",
        "wrong_date",
        "wrong_location",
        "wrong_tz",
        "expired",
        "future_fetch",
        "range",
        "null",
        "partial",
        "shape",
        "wrong_event",
        "bad_number",
        "future_range",
    ],
)
def test_single_missing_or_invalid_source(failure: str) -> None:
    """单源失败、错目标、过期或空值均不能被模型 high 掩盖。"""
    data = _qualified_data()
    now = datetime(2026, 10, 6, 8, tzinfo=timezone.utc)
    if failure in {"sun", "weather", "glow"}:
        key = {"sun": "sun_times", "weather": "weather_forecast", "glow": "sunset_glow_score"}[
            failure
        ]
        data[key] = {"error": "离线失败"}
    elif failure == "wrong_date":
        data["sun_times"]["日期"] = "2026-10-05"
    elif failure == "wrong_location":
        data["weather_forecast"]["位置"] = "1,1"
    elif failure == "wrong_tz":
        data["weather_forecast"]["时区"] = "-04:00"
    elif failure == "expired":
        data["weather_forecast"]["获取时间"] = (now - timedelta(hours=7)).isoformat()
    elif failure == "future_fetch":
        data["weather_forecast"]["获取时间"] = (now + timedelta(hours=1)).isoformat()
    elif failure == "range":
        data["weather_forecast"]["有效结束时间"] = "2026-10-06T01:00:00+08:00"
    elif failure == "null":
        data["weather_forecast"]["每日预报"][0]["平均云量（%）"] = None
    elif failure == "partial":
        data["sunset_glow_score"]["缺失字段"] = ["能见度"]
    elif failure == "wrong_event":
        data["sunset_glow_score"]["目标时刻"] = "2026-10-06T19:00:00+08:00"
    elif failure == "bad_number":
        data["weather_forecast"]["每日预报"][0]["平均云量（%）"] = "天气完美"
    elif failure == "future_range":
        data["weather_forecast"]["有效开始时间"] = "2026-10-06T20:00:00+08:00"
    else:
        data["weather_forecast"] = ["不是结果对象"]
    card = qualify_card(
        DecisionCard(
            conclusion="冲",
            evidence=[],
            confidence="high",
            verdict="go",
            target_date="2026-10-06",
            subject_type="火烧云",
        ),
        data,
        now,
    )
    assert card.data_status == "needs_data"
    assert card.verdict == "wait" and card.confidence == "low"
    assert card.missing_data
    assert card.confidence_detail is None


def test_qualified_data_overrides_model_claims() -> None:
    """补齐重查后可恢复资格，依据内容和等级由实际结果替换。"""
    card = qualify_card(
        DecisionCard(
            conclusion="按窗口出发",
            evidence=[],
            confidence="high",
            verdict="go",
            target_date="2026-10-06",
            subject_type="火烧云",
        ),
        _qualified_data(),
        datetime(2026, 10, 6, 8, tzinfo=timezone.utc),
    )
    assert card.data_status == "ready" and card.verdict == "go"
    assert card.confidence == "medium"
    assert {item.tool for item in card.evidence} == {
        "weather_forecast",
        "sun_times",
        "sunset_glow_score",
    }
    assert card.updated_at == "2026-10-06T07:00:00+00:00"
    assert datetime.fromisoformat(card.valid_until) == datetime(
        2026, 10, 6, 10, tzinfo=timezone.utc
    )
