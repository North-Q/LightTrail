"""编排层（E5-1）的 pytest 用例。

验证：
- 四管线名称与题材缺省模式映射；
- 灵感管线 Fake 数据源 + Fake reason 走通：采集步骤落 TraceRecorder、卡片解析；
- Orchestrator.plan 端到端（意图 → 管线 → 渲染）；
- 意图/卡片解析失败 → 降级 ReAct 自由对话（含卡片上下文）；
- 复盘管线骨架卡片。
"""

from __future__ import annotations

import json

from lighttrail.agent import Agent
from lighttrail.agent.tools import registry
from lighttrail.infra.trace import TraceRecorder
from lighttrail.orchestrator import Orchestrator
from lighttrail.orchestrator.context import PipelineContext
from lighttrail.orchestrator.orchestrator import _render_card
from lighttrail.orchestrator.pipelines import (
    PIPELINES,
    PipelineEnv,
    ReviewPipeline,
    default_mode,
)
from lighttrail.orchestrator.schemas import DecisionCard, Intent
from lighttrail.tools import (  # noqa: F401  触发注册
    astronomy,
    basic,
    exposure,
    memory_tool,
    site_match,
    weather,
)


class FakeChatClient:
    """按脚本预置响应序列的伪客户端（可接受 thinking 等扩展参数）。"""

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def chat(self, messages, **kwargs) -> dict:
        self.calls.append({"messages": messages, **kwargs})
        return self._responses.pop(0)


_INTENT_JSON = json.dumps({"subject_type": "银河", "location": "", "time_hint": "这周末", "mode": "inspiration"}, ensure_ascii=False)
_CARD_JSON = json.dumps(
    {
        "conclusion": "周末银河可见，建议 20:30-23:00 前往崇明东滩。",
        "evidence": [{"tool": "galaxy_visibility", "field": "可见窗口", "confidence": "high", "note": "窗口 20:10-23:50"}],
        "confidence": "medium",
        "time_window": "20:30-23:00",
        "locations": [{"name": "崇明东滩", "reason": "光害低，银心朝向开阔"}],
        "params": [{"name": "快门", "value": "20s", "reason": "14mm 全画幅 NPF 上限"}],
        "alternatives": ["临港南汇嘴"],
        "degraded": "",
    },
    ensure_ascii=False,
)


def _fake_dispatch(name: str, args: str) -> str:
    """Fake 数据源：按工具名回罐装 JSON（不触网）。"""
    data = {
        "moon_phase": {"月相名称": "新月", "照亮比例（%）": 2, "月光影响建议": "低"},
        "galaxy_visibility": {"可见窗口": [{"开始": "20:10", "结束": "23:50"}], "最高高度角": 55, "提示": "好"},
        "weather_forecast": {"每日预报": [{"日期": "2026-09-08", "平均云量（%）": 30}], "数据来源": "Open-Meteo（免费）"},
        "sun_times": {"日出": "05:42", "日落": "18:06"},
        "sunset_glow_score": {"评分（0-100）": 62, "等级": "中等（可看趋势再定）", "数据来源": "Open-Meteo（免费）"},
    }
    return json.dumps(data.get(name, {"error": f"未知工具 {name}"}), ensure_ascii=False)


def _make_env(recorder: TraceRecorder | None = None) -> PipelineEnv:
    return PipelineEnv(
        dispatch=_fake_dispatch,
        reason=lambda prompt, system: _CARD_JSON,
        recorder=recorder or TraceRecorder(),
    )


# ------ 管线注册与模式映射 ------
def test_pipelines_registered() -> None:
    """四管线全部注册。"""
    assert set(PIPELINES) == {"inspiration", "planning", "live", "review"}


def test_default_mode_mapping() -> None:
    """题材缺省模式：星空/银河 → 灵感，火烧云/晚霞 → 临场。"""
    assert default_mode("银河") == "inspiration"
    assert default_mode("星空") == "inspiration"
    assert default_mode("火烧云") == "live"
    assert default_mode("晚霞") == "live"
    assert default_mode("海报") == "inspiration"


# ------ 灵感管线直跑 ------
def test_inspiration_pipeline_walks_steps_and_records_trace() -> None:
    """灵感管线：采集 → 评分 → reason → 卡片，各步骤落 TraceRecorder。"""
    recorder = TraceRecorder()
    env = _make_env(recorder)
    pipeline = PIPELINES["inspiration"]
    ctx = PipelineContext(
        user_request="这周末想去拍银河",
        intent=Intent(subject_type="银河", time_hint="这周末"),
    )
    card = pipeline.run(ctx, env)

    assert card.conclusion.startswith("周末银河可见")
    assert card.evidence[0].tool == "galaxy_visibility"
    assert ctx.data["moon_phase"]["月相名称"] == "新月"
    assert ctx.scores["月相"] == "新月"
    step_names = [s.name for s in recorder.to_report().steps]
    assert "采集_moon_phase" in step_names
    assert "采集_galaxy_visibility" in step_names
    assert "采集_weather_forecast" in step_names
    assert "综合_reason" in step_names
    assert "卡片_产出" in step_names


def test_inspiration_pipeline_records_scores() -> None:
    """代码化评分（火烧云题材）确定性写入上下文。"""
    env = _make_env()
    pipeline = PIPELINES["inspiration"]
    ctx = PipelineContext(
        user_request="明天傍晚火烧云值得冲吗",
        intent=Intent(subject_type="火烧云", time_hint="明天傍晚"),
    )
    pipeline.run(ctx, env)
    assert ctx.scores["火烧云评分"] == 62


def test_review_pipeline_missing_image_returns_skeleton() -> None:
    """复盘管线缺图时返回骨架卡（提示提供照片路径，不触 LLM）。"""
    env = _make_env()
    card = ReviewPipeline().run(PipelineContext(user_request="帮我复盘这张照片"), env)
    assert "image_path" in card.conclusion
    assert card.degraded == "review_missing_image"


# ------ Orchestrator 端到端与降级 ------
def test_orchestrator_plan_full_flow() -> None:
    """plan：意图理解（默认模型）→ 灵感管线（深推理）→ 卡片渲染。"""
    recorder = TraceRecorder()
    fake = FakeChatClient(
        [
            {"role": "assistant", "content": _INTENT_JSON},
            {"role": "assistant", "content": _CARD_JSON},
        ]
    )
    agent = Agent(fake, registry, model="ecnu-plus")
    orc = Orchestrator(fake, registry, agent, recorder=recorder, dispatch=_fake_dispatch)
    text = orc.plan("这周末想去拍银河")

    assert "## 拍摄方案" in text
    assert "周末银河可见" in text
    assert orc.last_card is not None
    # 意图理解用默认模型；综合走深推理（reason 通道）
    intent_call = fake.calls[0]
    assert intent_call["model"] == "ecnu-plus"
    assert intent_call["tools"] is None
    reason_call = fake.calls[1]
    assert reason_call["model"] == "ecnu-max"
    assert reason_call["tools"] is None
    # 默认不携带 thinking 扩展参数（真实 OpenAI 兼容接口可用）
    assert reason_call.get("thinking") is None
    # 管线启动/降级步骤记录
    step_names = [s.name for s in recorder.to_report().steps]
    assert "管线_inspiration" in step_names or "管线_live" in step_names


def test_orchestrator_plan_falls_back_to_react_on_bad_card() -> None:
    """卡片解析连续失败 → 降级 ReAct 自由对话（携带卡片上下文剩余历史）。"""
    recorder = TraceRecorder()
    bad_card = {"role": "assistant", "content": "这不是 JSON"}
    reply = {"role": "assistant", "content": "我建议先看云图再决定。"}
    fake = FakeChatClient([{"role": "assistant", "content": _INTENT_JSON}, bad_card, bad_card, bad_card, reply])
    agent = Agent(fake, registry, model="ecnu-plus")
    orc = Orchestrator(fake, registry, agent, recorder=recorder, dispatch=_fake_dispatch)
    text = orc.plan("今晚火烧云值得冲吗")

    assert text == "我建议先看云图再决定。"
    assert any(step.name == "管线降级" for step in recorder.to_report().steps)
    # 最后一次调用是 agent.run（ReAct，无 thinking）
    last_call = fake.calls[-1]
    assert last_call.get("thinking") is None


def test_fallback_carries_card_context() -> None:
    """降级对话携带最近卡片上下文（追问可在此基础上调整）。"""
    fake = FakeChatClient(
        [
            {"role": "assistant", "content": _INTENT_JSON},
            {"role": "assistant", "content": _CARD_JSON},
            {"role": "assistant", "content": "好，参数改激进一些。"},
        ]
    )
    agent = Agent(fake, registry, model="ecnu-plus")
    orc = Orchestrator(fake, registry, agent, dispatch=_fake_dispatch)
    orc.plan("这周末想去拍银河")  # 成功产出卡片
    reply = orc._fallback("参数激进一点")
    assert reply == "好，参数改激进一些。"
    last_user = fake.calls[-1]["messages"][-1]["content"]
    assert "参数激进一点" in last_user
    assert "背景" in last_user


def test_render_card_includes_evidence_and_confidence() -> None:
    """渲染包含结论/时间窗口/置信度/依据/备选（M2 元素不缺）。"""
    card = DecisionCard.model_validate_json(_CARD_JSON)
    text = _render_card(card)
    assert "结论" in text
    assert "置信度：medium" in text
    assert "依据" in text
    assert "备选" in text
