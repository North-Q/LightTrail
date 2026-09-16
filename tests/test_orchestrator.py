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
import tempfile
from pathlib import Path

import pytest

from lighttrail.adapters.llm.provider import ChatClientProvider
from lighttrail.adapters.llm.pydantic_bridge import LightTrailModel
from lighttrail.composition import build_registry
from lighttrail.contracts.models import DecisionCard, Intent
from lighttrail.infra.trace import TraceRecorder
from lighttrail.memory import MemoryManager, UserProfile
from lighttrail.orchestrator import Orchestrator
from lighttrail.orchestrator.context import PipelineContext
from lighttrail.orchestrator.orchestrator import _render_card
from lighttrail.orchestrator.pipelines import (
    PIPELINES,
    PipelineEnv,
    ReviewPipeline,
    default_mode,
)
from lighttrail.runtime.agent import AgentRuntime
from lighttrail.tools import (  # noqa: F401  触发注册
    astronomy,
    basic,
    exposure,
    memory_tool,
    site_match,
    weather,
)

registry = build_registry()
_MEMORY = MemoryManager(
    Path(tempfile.mkdtemp(prefix="lt_pipeline_memory_")),
    profile=UserProfile(favorite_spots=[{"name": "测试机位", "latitude": 31.2, "longitude": 121.5}]),
)


class FakeChatClient:
    """按脚本预置响应序列的伪客户端（可接受 thinking 等扩展参数）。"""

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def chat(self, messages, **kwargs) -> dict:
        self.calls.append({"messages": messages, **kwargs})
        return self._responses.pop(0)

    async def acall(self, messages, **kwargs) -> dict:
        """async 通道：B2-7 起编排器经 AgentRuntime → Model 桥走 acall。"""
        return self.chat(messages, **kwargs)


_INTENT_JSON = json.dumps(
    {
        "intent": {"subject_type": "银河", "location": "", "time_hint": "这周末", "mode": "inspiration"},
        "confidence": 0.9,
        "reason": "题材和时间明确",
    },
    ensure_ascii=False,
)
def _runtime(
    fake: FakeChatClient,
    *,
    recorder: TraceRecorder | None = None,
    model: str = "ecnu-plus",
    reason_model: str = "ecnu-max",
) -> AgentRuntime:
    """把伪客户端经自定义 Model 桥接成 AgentRuntime（B2-7：替代旧 Agent 门面）。"""
    provider = ChatClientProvider(fake)
    return AgentRuntime(
        LightTrailModel(provider, model_name=model, recorder=recorder),
        registry,
        reason_model=LightTrailModel(provider, model_name=reason_model, recorder=recorder),
        recorder=recorder,
    )


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
        memory=_MEMORY,
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


def test_empty_profile_honestly_degrades_without_coordinates(tmp_path: Path) -> None:
    """空档案不再回退上海占位坐标，而是提示先录入机位。"""
    env = PipelineEnv(dispatch=_fake_dispatch, reason=lambda prompt, system: _CARD_JSON, memory=MemoryManager(tmp_path))
    card = PIPELINES["inspiration"].run(
        PipelineContext(user_request="今晚拍银河", intent=Intent(subject_type="银河")), env
    )
    assert card.degraded == "missing_favorite_spot"
    assert "录入" in card.conclusion


def test_review_pipeline_missing_image_returns_skeleton() -> None:
    """复盘管线缺图时返回骨架卡（提示提供照片路径，不触 LLM）。"""
    env = _make_env()
    card = ReviewPipeline().run(PipelineContext(user_request="帮我复盘这张照片"), env)
    assert "image_path" in card.conclusion
    assert card.degraded == "review_missing_image"


# ------ Orchestrator 端到端与降级 ------
def test_orchestrator_plan_full_flow() -> None:
    """明确银河请求由规则直达灵感管线，不额外调用意图模型。"""
    recorder = TraceRecorder()
    fake = FakeChatClient(
        [{"role": "assistant", "content": _CARD_JSON}]
    )
    runtime = _runtime(fake, recorder=recorder)
    orc = Orchestrator(registry, runtime, memory=_MEMORY, recorder=recorder, dispatch=_fake_dispatch)
    text = orc.plan("这周末想去拍银河")

    assert "## 拍摄方案" in text
    assert "周末银河可见" in text
    assert orc.last_card is not None
    # 规则命中不调用默认意图模型；只保留管线综合的深推理调用。
    assert len(fake.calls) == 1
    reason_call = fake.calls[0]
    assert reason_call["model"] == "ecnu-max"
    assert reason_call["tools"] is None
    # 默认不携带 thinking 扩展参数（真实 OpenAI 兼容接口可用）
    assert reason_call.get("thinking") is None
    # 管线启动/降级步骤记录
    step_names = [s.name for s in recorder.to_report().steps]
    assert "意图路由_规则" in step_names
    assert "管线_inspiration" in step_names or "管线_live" in step_names


def test_orchestrator_parse_intent_falls_back_to_default_model() -> None:
    """规则未覆盖时由默认模型给出带置信度的路由判定并落 trace。"""
    fake = FakeChatClient([{"role": "assistant", "content": _INTENT_JSON}])
    runtime = _runtime(fake)
    recorder = TraceRecorder()
    orc = Orchestrator(registry, runtime, recorder=recorder, dispatch=_fake_dispatch)

    intent = orc.parse_intent("帮我看看本周适合练什么摄影题材")

    assert intent.subject_type == "银河"
    assert len(fake.calls) == 1
    assert fake.calls[0]["model"] == "ecnu-plus"
    steps = recorder.to_report().steps
    assert any(step.name == "意图路由_LLM" and "0.90" in step.output_summary for step in steps)


def test_orchestrator_rejects_low_confidence_llm_route() -> None:
    """低于部署阈值的 LLM 判定不应被静默接受。"""
    low_confidence = json.dumps(
        {"intent": {"subject_type": "其他", "mode": "inspiration"}, "confidence": 0.3, "reason": "模糊"},
        ensure_ascii=False,
    )
    runtime = _runtime(FakeChatClient([{"role": "assistant", "content": low_confidence}]))
    orc = Orchestrator(registry, runtime, dispatch=_fake_dispatch, routing_confidence_threshold=0.6)

    with pytest.raises(ValueError, match="低于阈值"):
        orc.parse_intent("帮我看看最近该练什么")


def test_orchestrator_plan_falls_back_to_react_on_bad_card() -> None:
    """卡片解析连续失败 → 降级 ReAct 自由对话（携带卡片上下文剩余历史）。"""
    recorder = TraceRecorder()
    bad_card = {"role": "assistant", "content": "这不是 JSON"}
    reply = {"role": "assistant", "content": "我建议先看云图再决定。"}
    fake = FakeChatClient([bad_card, bad_card, bad_card, reply])
    runtime = _runtime(fake, recorder=recorder)
    orc = Orchestrator(registry, runtime, memory=_MEMORY, recorder=recorder, dispatch=_fake_dispatch)
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
            {"role": "assistant", "content": _CARD_JSON},
            {"role": "assistant", "content": "好，参数改激进一些。"},
        ]
    )
    runtime = _runtime(fake)
    orc = Orchestrator(registry, runtime, memory=_MEMORY, dispatch=_fake_dispatch)
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


# ------ 卡片规则字段（B4-3：置信度明细 / 三态结论）------
def test_card_carries_rule_based_confidence_detail() -> None:
    """卡片自动补规则推导的置信度明细（主值来自依据级别，不让模型自评）。"""
    env = _make_env()
    ctx = PipelineContext(
        user_request="这周末想去拍银河",
        intent=Intent(subject_type="银河", time_hint="这周末"),
    )
    card = PIPELINES["inspiration"].run(ctx, env)
    assert card.confidence_detail is not None
    assert card.confidence_detail.level == "medium"
    assert card.confidence_detail.high_count == 1  # evidence 里 galaxy_visibility 为 high
    assert card.confidence_detail.score == 85


def test_card_verdict_comes_from_structured_output() -> None:
    """三态结论透传模型结构化字段（前端不再用中文正则猜结论）。"""
    payload = json.loads(_CARD_JSON)
    payload["verdict"] = "go"
    env = PipelineEnv(
        dispatch=_fake_dispatch,
        reason=lambda prompt, system: json.dumps(payload, ensure_ascii=False),
        recorder=TraceRecorder(),
        memory=_MEMORY,
    )
    ctx = PipelineContext(
        user_request="这周末想去拍银河",
        intent=Intent(subject_type="银河", time_hint="这周末"),
    )
    card = PIPELINES["inspiration"].run(ctx, env)
    assert card.verdict == "go"


def test_review_skeleton_card_also_gets_confidence_detail() -> None:
    """复盘缺图骨架卡同样带明细（模板方法收口，不走 LLM）。"""
    card = ReviewPipeline().run(PipelineContext(user_request="帮我复盘这张照片"), _make_env())
    assert card.confidence_detail is not None
    assert card.confidence_detail.score == 30  # low 等级兜底带
