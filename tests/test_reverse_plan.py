"""照片反推方案（E6-2，PRD D1.2）的 pytest 用例。

验证（全部离线，Fake 多模态 + Fake 数据源 + FakeChatClient）：
- reverse_engineer_photo：多模态消息含 image_url 与 note、VISION 路由、tools=None；
  返回含「复刻计划」等中文字段；自愈重试；
- Orchestrator.reverse_plan：参考图 → 反推 → 候选日采集 → reason 综合 → 复刻卡片（含三要素）。
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

import lighttrail.tools.photo_analysis as photo
from lighttrail.agent import Agent
from lighttrail.agent.tools import registry
from lighttrail.orchestrator import Orchestrator
from lighttrail.tools import (  # noqa: F401
    astronomy,
    basic,
    exposure,
    memory_tool,
    photo_analysis,
    site_match,
    weather,
)


@pytest.fixture()
def workdir() -> Path:
    directory = Path(tempfile.mkdtemp(prefix="lt_reverse_"))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


class FakeChatClient:
    """按脚本预置响应（含多模态图片消息）的伪客户端。"""

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def chat(self, messages, **kwargs) -> dict:
        self.calls.append({"messages": messages, "kwargs": kwargs})
        return self._responses.pop(0)


_REVERSE_JSON = json.dumps(
    {
        "scene": "海边日落",
        "light_direction": "逆光（日落方向）",
        "estimated_time": "夏季傍晚日落前 20 分钟",
        "site_features": "低机位，有礁石前景",
        "post_style": "暖调、轻度 HDR",
        "replication_plan": "周末傍晚去临港海边：日落前 40 分钟到位，低机位带礁石前景，f/11 ISO 100",
        "suggestions": [{"name": "光圈", "value": "f/11", "reason": "前景礁石与太阳都实"}],
        "confidence": "medium",
    },
    ensure_ascii=False,
)

_CARD_JSON = json.dumps(
    {
        "conclusion": "周末傍晚去临港海边复刻，日落前 40 分钟到机位。",
        "evidence": [{"tool": "reverse_engineer_photo", "field": "复刻计划", "confidence": "medium", "note": "参考图反推"}],
        "confidence": "medium",
        "time_window": "16:30-17:10",
        "locations": [{"name": "临港海边", "reason": "开阔东南向，符合逆光日落特征"}],
        "params": [{"name": "光圈", "value": "f/11", "reason": "景深"}],
        "alternatives": [],
        "degraded": "",
    },
    ensure_ascii=False,
)


def _make_png(path: Path) -> None:
    from PIL import Image

    Image.new("RGB", (200, 150), (220, 120, 40)).save(path, format="PNG")


def _fake_dispatch(name: str, args: str) -> str:
    data = {
        "weather_forecast": {
            "每日预报": [
                {"日期": "2026-09-08", "平均云量（%）": 35},
                {"日期": "2026-09-09", "平均云量（%）": 70},
                {"日期": "2026-09-10", "平均云量（%）": 20},
            ],
            "数据来源": "Open-Meteo（免费）",
        },
        "sun_times": {"日出": "05:42", "日落": "18:06"},
        "moon_phase": {"月相名称": "蛾眉月", "月光影响建议": "低"},
    }
    return json.dumps(data.get(name, {"error": f"未知工具 {name}"}), ensure_ascii=False)


# ------ reverse_engineer_photo（工具层）------
def test_reverse_tool_sends_image_and_returns_plan(workdir: Path, monkeypatch) -> None:
    """多模态消息含 image_url + note；VISION 路由默认 plus；返回复刻计划。"""
    image = workdir / "ref.png"
    _make_png(image)
    fake = FakeChatClient([{"role": "assistant", "content": _REVERSE_JSON}])
    monkeypatch.setattr(photo, "_get_client", lambda: fake)
    monkeypatch.setattr(photo, "load_settings", lambda: type("S", (), {"data_dir": str(workdir)})())
    result = photo.reverse_engineer_photo(str(image), note="我在杭州，只能周末去")
    assert result["复刻计划"].startswith("周末傍晚去临港海边")
    assert "机位特征" in result
    assert result["后期风格"] == "暖调、轻度 HDR"
    call = fake.calls[0]
    assert call["kwargs"]["model"] == "ecnu-plus"
    assert call["kwargs"]["tools"] is None
    content = call["messages"][-1]["content"]
    assert "我在杭州" in content[0]["text"]
    assert content[1]["type"] == "image_url"


def test_reverse_tool_retry_then_succeed(workdir: Path, monkeypatch) -> None:
    """首次非法输出 → 纠错重试 → 成功（2 次调用）。"""
    image = workdir / "ref.png"
    _make_png(image)
    fake = FakeChatClient(
        [
            {"role": "assistant", "content": "描述文字而非 JSON"},
            {"role": "assistant", "content": _REVERSE_JSON},
        ]
    )
    monkeypatch.setattr(photo, "_get_client", lambda: fake)
    monkeypatch.setattr(photo, "load_settings", lambda: type("S", (), {"data_dir": str(workdir)})())
    photo.reverse_engineer_photo(str(image))
    assert len(fake.calls) == 2


def test_reverse_tool_registered() -> None:
    """reverse_engineer_photo 已注册。"""
    names = {s["function"]["name"] for s in registry.to_openai_schema()}
    assert "reverse_engineer_photo" in names


# ------ Orchestrator.reverse_plan（端到端）------
def test_orchestrator_reverse_plan_full_flow(workdir: Path, monkeypatch) -> None:
    """参考图 → 反推 → 候选日采集 → reason 综合 → 复刻卡片（含三要素）。"""
    image = workdir / "ref.png"
    _make_png(image)
    # 多模态走 photo 模块客户端；agent.reason 走编排客户端
    photo_fake = FakeChatClient([{"role": "assistant", "content": _REVERSE_JSON}])
    monkeypatch.setattr(photo, "_get_client", lambda: photo_fake)
    monkeypatch.setattr(photo, "load_settings", lambda: type("S", (), {"data_dir": str(workdir)})())
    agent_fake = FakeChatClient([{"role": "assistant", "content": _CARD_JSON}])
    agent = Agent(agent_fake, registry, model="ecnu-plus")
    orc = Orchestrator(agent_fake, registry, agent, dispatch=_fake_dispatch)
    text = orc.reverse_plan(str(image), note="只能周末去")

    assert "## 拍摄方案" in text
    assert "临港海边" in text
    assert orc.last_card is not None
    assert len(photo_fake.calls) == 1  # 多模态一次
    assert len(agent_fake.calls) == 1  # reason 综合一次
    # reason 调用走深推理模型、不带工具
    assert agent_fake.calls[0]["kwargs"]["model"] == "ecnu-max"


def test_orchestrator_reverse_plan_fallback_on_failure(workdir: Path, monkeypatch) -> None:
    """反推链路失败 → 降级自由对话（agent.run）。"""
    image = workdir / "ref.png"
    _make_png(image)
    photo_fake = FakeChatClient(
        [
            {"role": "assistant", "content": "坏输出"},
            {"role": "assistant", "content": "还是坏的"},
            {"role": "assistant", "content": "依旧坏"},
        ]
    )
    monkeypatch.setattr(photo, "_get_client", lambda: photo_fake)
    monkeypatch.setattr(photo, "load_settings", lambda: type("S", (), {"data_dir": str(workdir)})())
    agent_fake = FakeChatClient([{"role": "assistant", "content": "复刻不了的话我可以帮你找类似的机位。"}])
    # photo_fake 恒坏 → reverse_engineer_photo 抛 PhotoError → 降级 agent.run
    agent = Agent(agent_fake, registry, model="ecnu-plus")
    orc = Orchestrator(agent_fake, registry, agent, dispatch=_fake_dispatch)
    text = orc.reverse_plan(str(image))
    assert isinstance(text, str)
    assert len(text) > 0
    # 降级路径确实走了一次 agent.run（历史有消息）
    assert agent.history  # user 消息已入历史
