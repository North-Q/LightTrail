"""一句话出方案端到端（E5-3）的 pytest 用例。

验证（全部用 Fake 数据源 + FakeChatClient，不依赖真实 API Key、不触网）：
- 黄金场景「这周末想去拍银河」：意图 → 采集 → 评分 → reason 综合 → 卡片；
- 临场场景「火烧云值得冲吗」走 live 管线出决策卡；
- 追问「参数激进一点」转入 ReAct 自由对话且携带 DecisionCard 上下文；
- cli --pipeline 入口打印结构化方案（Fake 编排器）。
"""

from __future__ import annotations

import json

from lighttrail.agent import Agent
from lighttrail.agent.tools import registry
from lighttrail.orchestrator import Orchestrator
from lighttrail.tools import (  # noqa: F401  触发注册
    astronomy,
    basic,
    exposure,
    memory_tool,
    site_match,
    weather,
)

# E8 黄金用例集雏形：典型请求 + 预期题材 + 关键断言词
GOLDEN_CASES = [
    {"request": "这周末想去拍银河", "subject": "银河", "expect": ["银河", "结论"]},
    {"request": "明天傍晚去拍火烧云值得吗", "subject": "火烧云", "expect": ["火烧云", "结论"]},
    {"request": "今晚星空怎么样，能拍吗", "subject": "星空", "expect": ["星空", "结论"]},
]


class FakeChatClient:
    """按脚本预置响应序列的伪客户端。"""

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def chat(self, messages, **kwargs) -> dict:
        self.calls.append({"messages": messages, **kwargs})
        return self._responses.pop(0)


def _intent_json(subject: str, mode: str = "inspiration") -> str:
    return json.dumps({"subject_type": subject, "location": "", "time_hint": "这周末", "mode": mode}, ensure_ascii=False)


def _card_json(conclusion: str = "周末银河可见，建议 20:30-23:00 前往崇明东滩。") -> str:
    return json.dumps(
        {
            "conclusion": conclusion,
            "evidence": [{"tool": "galaxy_visibility", "field": "可见窗口", "confidence": "high", "note": "20:10-23:50"}],
            "confidence": "medium",
            "time_window": "20:30-23:00",
            "locations": [{"name": "崇明东滩", "reason": "光害低"}],
            "params": [{"name": "快门", "value": "20s", "reason": "14mm 全画幅 NPF 上限"}],
            "alternatives": ["临港南汇嘴"],
            "degraded": "",
        },
        ensure_ascii=False,
    )


def _fake_dispatch(name: str, args: str) -> str:
    """Fake 数据源（不触网）。"""
    data = {
        "moon_phase": {"月相": "新月", "照亮比例": 2, "月光干扰": "低"},
        "galaxy_visibility": {"可见窗口": [{"开始": "20:10", "结束": "23:50"}], "最高高度角": 55, "提示": "好"},
        "weather_forecast": {"每日预报": [{"日期": "2026-09-08", "平均云量（%）": 30}], "数据来源": "Open-Meteo（免费）"},
        "sun_times": {"日出": "05:42", "日落": "18:06"},
        "sunset_glow_score": {"评分": 62, "等级": "中等（可看趋势再定）", "数据来源": "Open-Meteo（免费）"},
    }
    return json.dumps(data.get(name, {"error": f"未知工具 {name}"}), ensure_ascii=False)


def test_e2e_galaxy_plan_and_followup_react() -> None:
    """黄金场景：一句话 → 卡片；追问 → ReAct 且携带卡片上下文。"""
    fake = FakeChatClient(
        [
            {"role": "assistant", "content": _intent_json("银河")},
            {"role": "assistant", "content": _card_json()},
            {"role": "assistant", "content": "好，快门改 25s、ISO 提到 4000。"},
        ]
    )
    agent = Agent(fake, registry, model="ecnu-plus")
    orc = Orchestrator(fake, registry, agent, dispatch=_fake_dispatch)

    text = orc.plan("这周末想去拍银河")
    assert "## 拍摄方案" in text
    assert "周末银河可见" in text
    assert "20:30-23:00" in text
    assert orc.last_card is not None

    # 追问「参数激进一点」转入自由对话（ReAct），携带卡片上下文
    followup = orc._fallback("参数激进一点")
    assert followup == "好，快门改 25s、ISO 提到 4000。"
    last_user = fake.calls[-1]["messages"][-1]["content"]
    assert "参数激进一点" in last_user
    assert "背景" in last_user
    assert "周末银河可见" in last_user  # 卡片结论随上下文带入


def test_e2e_fire_cloud_live_decision() -> None:
    """临场场景：火烧云 → live 管线 → 去/等/放弃决策卡。"""
    card = _card_json("建议 18:10 到机位蹲守，风险中等可接受。")
    fake = FakeChatClient(
        [
            {"role": "assistant", "content": _intent_json("火烧云", mode="live")},
            {"role": "assistant", "content": card},
        ]
    )
    agent = Agent(fake, registry, model="ecnu-plus")
    orc = Orchestrator(fake, registry, agent, dispatch=_fake_dispatch)
    text = orc.plan("明天傍晚去拍火烧云值得吗")
    assert "建议 18:10" in text
    assert "置信度：medium" in text


def test_golden_cases_are_parsable() -> None:
    """黄金用例集雏形：全部请求可被意图解析（供 E8 展开）。"""
    fake = FakeChatClient([{"role": "assistant", "content": _intent_json(c["subject"])} for c in GOLDEN_CASES])
    agent = Agent(fake, registry, model="ecnu-plus")
    orc = Orchestrator(fake, registry, agent, dispatch=_fake_dispatch)
    for case in GOLDEN_CASES:
        intent = orc.parse_intent(case["request"])
        assert intent.subject_type == case["subject"], case
    assert sum(1 for _ in GOLDEN_CASES) == 3


def test_cli_pipeline_entry_prints_card(monkeypatch, capsys) -> None:
    """cli --pipeline：Fake 编排器打印结构化方案（不触网）。"""
    import lighttrail.cli as cli_mod

    class _FakeSettings:
        api_key = "sk-test"
        base_url = "http://127.0.0.1:1"
        model = "ecnu-plus"
        model_reason = "ecnu-max"
        serial_llm = False
        data_dir = "data"
        quota_warn_threshold = 0.9

        @property
        def has_api_key(self) -> bool:
            return True

    class _FakeOrchestrator:
        def __init__(self, *args, **kwargs) -> None:
            self.last_request = ""

        def plan(self, request: str) -> str:
            self.last_request = request
            return "## 拍摄方案（Fake）\n- 结论：这周末银河可拍。"

    monkeypatch.setattr(cli_mod, "load_settings", lambda: _FakeSettings())
    monkeypatch.setattr(cli_mod, "Orchestrator", _FakeOrchestrator)

    code = cli_mod.main(["--pipeline", "这周末想去拍银河"])
    assert code == 0
    assert "## 拍摄方案（Fake）" in capsys.readouterr().out
