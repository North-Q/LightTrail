"""评估体系（E8）的 pytest 用例（全部离线，不触网、不依赖 API Key）。

覆盖：
- assert_card 结构化断言（M2：evidence 非空 / 机位数 / 置信度枚举 / 降级标注）；
- LocalRubric 4 维打分、ToolCrossCheck 抓「违反 500 法则」假阳性；
- CassetteChatClient 回放确定性与 LLM 判官降级；
- runner L2 全量回放（零 LLM 成本、字节级可重复）与 L3 报告结构。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# evals 是项目根下的顶层包：把仓库根加入 sys.path 以导入
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.fake_data import CassetteChatClient, card, intents
from evals.judge import LLMJudge, LocalRubric, ToolCrossCheck
from evals.runner import assert_card, run_l2, run_l3
from lighttrail.orchestrator.schemas import DecisionCard, ParamSuggestion, Source
from lighttrail.tools import exposure  # noqa: F401  触发注册


def _card(**overrides) -> DecisionCard:
    """构造测试卡片（可覆盖字段）。"""
    base = {
        "conclusion": "建议 18:10 到达崇明东滩，值得出门。",
        "evidence": [Source(tool="weather_forecast", field="每日预报", confidence="medium", note="云量 30%")],
        "confidence": "medium",
    }
    base.update(overrides)
    return DecisionCard(**base)


# ------ assert_card ------
def test_assert_card_passes_valid() -> None:
    """合法卡片：结论/置信度/evidence/机位数全部通过。"""
    problems = assert_card(_card(), {"min_evidence": 1, "max_locations": 5, "confidence_allowed": ["high", "medium", "low"]})
    assert problems == []


def test_assert_card_rejects_empty_evidence() -> None:
    """M2 底线：默认要求 evidence ≥1，空依据判违规。"""
    problems = assert_card(_card(evidence=[]), {})
    assert any("evidence" in problem for problem in problems)


def test_assert_card_rejects_too_many_spots_and_degrade() -> None:
    """机位数超限 / 意外降级标注 → 违规。"""
    card = _card(locations=[{"name": f"机位{i}", "reason": ""} for i in range(6)], degraded="配额降级")
    problems = assert_card(card, {"max_locations": 5})
    assert problems  # 机位数与降级均计入


def test_assert_card_keyword_and_time_window() -> None:
    """结论关键词缺省与 planning 时间窗口期望。"""
    card = _card(conclusion="值得出门")
    problems = assert_card(card, {"conclusion_contains": ["崇明"]})
    assert any("关键词" in problem for problem in problems)
    problems2 = assert_card(_card(), {"time_window_note": True})
    assert any("time_window" in problem for problem in problems2)


# ------ LocalRubric ------
def test_local_rubric_dims_in_range() -> None:
    """4 维打分均落在 1-5，且依据完整性随 evidence 数量上升。"""
    score = LocalRubric().score(_card(evidence=[Source(tool="a", field="f", confidence="low", note="n")]))
    assert set(score.keys()) == {"决策合理性", "依据完整性", "个性化程度", "不确定性坦白"}
    for entry in score.values():
        assert 1 <= entry["score"] <= 5
    rich = LocalRubric().score(
        _card(
            evidence=[
                Source(tool="a", field="f", confidence="low", note="n"),
                Source(tool="b", field="f", confidence="medium", note="n"),
                Source(tool="c", field="f", confidence="high", note="n"),
            ]
        )
    )
    assert rich["依据完整性"]["score"] >= score["依据完整性"]["score"]


# ------ ToolCrossCheck（工具即裁判）------
def test_cross_check_catches_500_rule_violation() -> None:
    """14mm 快门 40s 超过 500 法则（500/14≈35.7s）→ 抓出假阳性（模型建议违规）。"""
    card = _card(params=[ParamSuggestion(name="快门", value="40s", reason="长爆")])
    findings = ToolCrossCheck().cross_check(card, focal_mm=14.0)
    assert len(findings) == 1
    assert findings[0]["rule"] == "500法则"
    assert "35.7" in findings[0]["detail"]


def test_cross_check_passes_compliant_shutter() -> None:
    """14mm 快门 20s 合规 → 无违规；非快门参数不校验。"""
    cross = ToolCrossCheck()
    card = _card(params=[ParamSuggestion(name="快门", value="20s", reason=""), ParamSuggestion(name="ISO", value="3200", reason="")])
    assert cross.cross_check(card, focal_mm=14.0) == []
    assert cross.parse_shutter_seconds("1/125") == 1 / 125
    assert cross.parse_shutter_seconds("30s") == 30.0
    assert cross.parse_shutter_seconds("0.5") == 0.5
    assert cross.parse_shutter_seconds("f/2.8") is None


# ------ Cassette / LLM 判官 ------
def test_cassette_client_replays_by_mark() -> None:
    """cassette 客户端按 prompt 语义回放意图/综合两段。"""
    cassettes = {"g1": {"intent": {"content": intents("银河")}, "card": {"content": card("去拍")}}}
    client = CassetteChatClient(cassettes, group_for={})
    client.set_group("g1")
    intent_resp = client.chat([{"role": "user", "content": "请把用户的一句话请求解析为规范意图"}])
    assert json.loads(intent_resp["content"])["subject_type"] == "银河"
    card_resp = client.chat([{"role": "user", "content": "请基于以上数据输出一份决策卡片"}])
    assert json.loads(card_resp["content"])["conclusion"] == "去拍"


def test_llm_judge_parses_and_falls_back() -> None:
    """LLM 判官：正常 JSON 解析；异常输入降级本地打分。"""
    good = '{"决策合理性": {"score": 4, "reason": "ok"}, "依据完整性": {"score": 5, "reason": "ok"}, "个性化程度": {"score": 3, "reason": "ok"}, "不确定性坦白": {"score": 2, "reason": "ok"}}'
    judge = LLMJudge(lambda prompt: good)
    result = judge.score(_card())
    assert result["决策合理性"]["score"] == 4
    assert result["依据完整性"]["score"] == 5

    broken = LLMJudge(lambda prompt: "抱歉，无法打分")
    fallback = broken.score(_card())
    assert set(fallback.keys()) == {"决策合理性", "依据完整性", "个性化程度", "不确定性坦白"}
    assert all(1 <= entry["score"] <= 5 for entry in fallback.values())


# ------ runner 集成（离线）------
def test_runner_l2_offline_zero_llm() -> None:
    """L2 全量回放：全部通过、零 LLM 调用、可重复（两次结果一致）。"""
    report = run_l2()
    assert report["llm_calls"] == 0
    assert report["pipeline_pass"] == report["pipeline_total"]
    assert report["tool_pass"] == report["tool_total"]
    assert report["pipeline_total"] == 12 and report["tool_total"] == 10
    repeat = run_l2()
    assert repeat["pipeline_details"] == report["pipeline_details"]


def test_runner_l3_report_structure() -> None:
    """L3 报告：含 4 维均值、diff、交叉校验清单与逐条行。"""
    report = run_l3()
    assert {"决策合理性", "依据完整性", "个性化程度", "不确定性坦白"} <= set(report["means"].keys())
    assert "diff_vs_last" in report
    assert isinstance(report["cross_check_findings"], list)
    assert report["samples"] >= 10
