"""评估体系（E8）的 pytest 用例（全部离线，不触网、不依赖 API Key）。

覆盖：
- assert_card 结构化断言（M2：evidence 非空 / 机位数 / 置信度枚举 / 降级标注；
  B4-3 起含 confidence_detail 契约不变量与 verdict 允许集合）；
- LocalRubric 4 维打分、ToolCrossCheck 抓「违反 500 法则」假阳性；
- CassetteChatClient 回放确定性与 LLM 判官降级；
- runner L2 全量回放（零 LLM 成本、字节级可重复）与 L3 报告结构。
"""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

# evals 是项目根下的顶层包：把仓库根加入 sys.path 以导入
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.fake_data import CassetteChatClient, card, intents
from evals.judge import LLMJudge, LocalRubric, ToolCrossCheck
from evals.runner import (
    _load_cases,
    _load_cassettes,
    _run_pipeline_case,
    assert_card,
    run_l2,
    run_l3,
)
from evals.semantic import assert_semantics
from lighttrail.contracts.models import ConfidenceDetail, DecisionCard, ParamSuggestion, Source
from lighttrail.infra.confidence import confidence_detail
from lighttrail.tools import exposure  # noqa: F401  触发注册


def _card(**overrides) -> DecisionCard:
    """构造测试卡片（可覆盖字段）。"""
    base = {
        "conclusion": "建议 18:10 到达崇明东滩，值得出门。",
        "evidence": [
            Source(tool="weather_forecast", field="每日预报", confidence="medium", note="云量 30%")
        ],
        "confidence": "medium",
    }
    base.update(overrides)
    card = DecisionCard(**base)
    if card.confidence_detail is None and "confidence_detail" not in overrides:
        card.confidence_detail = confidence_detail(card.confidence, card.evidence)
    return card


# ------ assert_card ------
def test_assert_card_passes_valid() -> None:
    """合法卡片：结论/置信度/evidence/机位数全部通过。"""
    problems = assert_card(
        _card(),
        {"min_evidence": 1, "max_locations": 5, "confidence_allowed": ["high", "medium", "low"]},
    )
    assert problems == []


def test_assert_card_rejects_empty_evidence() -> None:
    """M2 底线：默认要求 evidence ≥1，空依据判违规。"""
    problems = assert_card(_card(evidence=[]), {})
    assert any("evidence" in problem for problem in problems)


def test_assert_card_rejects_too_many_spots_and_degrade() -> None:
    """机位数超限 / 意外降级标注 → 违规。"""
    card = _card(
        locations=[{"name": f"机位{i}", "reason": ""} for i in range(6)], degraded="配额降级"
    )
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
    score = LocalRubric().score(
        _card(evidence=[Source(tool="a", field="f", confidence="low", note="n")])
    )
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
    card = _card(
        params=[
            ParamSuggestion(name="快门", value="20s", reason=""),
            ParamSuggestion(name="ISO", value="3200", reason=""),
        ]
    )
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
def test_runner_l2_v2_passes_without_llm() -> None:
    """独立合成夹具全部通过语义验收，且离线回放逐条可重复。"""
    report = run_l2()
    assert report["llm_calls"] == 0
    assert report["pipeline_pass"] == report["pipeline_total"]
    assert report["tool_pass"] == report["tool_total"]
    assert report["pipeline_total"] == 12 and report["tool_total"] == 10
    repeat = run_l2()
    assert repeat["pipeline_details"] == report["pipeline_details"]


def _tampered_result(case_id: str, changes: dict[str, object]) -> list[str]:
    """篡改单条模型卡片后经生产管线和语义断言回放。"""
    case = next(row for row in _load_cases()["pipeline_cases"] if row["id"] == case_id)
    cassettes = deepcopy(_load_cassettes())
    payload = json.loads(cassettes[case_id]["card"]["content"])
    payload.update(changes)
    cassettes[case_id]["card"]["content"] = json.dumps(payload, ensure_ascii=False)
    _, _, problems = _run_pipeline_case(case, cassettes)
    return problems


def test_v2_rejects_tianhuangping_answering_shanghai() -> None:
    """问天荒坪却答上海必须失败，即使模型给了合法卡片结构。"""
    problems = _tampered_result(
        "L2-001",
        {
            "conclusion": "2026-10-03去上海拍银河，依据月相名称新月出发。",
            "locations": [{"name": "上海", "reason": "模型臆造"}],
        },
    )
    assert any("降级" in item or "错误地点" in item for item in problems)


def test_v2_rejects_tomorrow_chongming_answering_tonight() -> None:
    """问明天崇明却答今晚通用建议必须失败。"""
    problems = _tampered_result(
        "L2-005",
        {"conclusion": "今晚可以出去拍晚霞，依据日落18:05。", "time_window": "今晚 18:00-18:30"},
    )
    assert any("降级" in item or "今晚" in item or "日期" in item for item in problems)


def test_v2_rejects_wrong_topic_fact_and_personalization() -> None:
    """错误题材、关键事实和伪造个性化都不能靠关键词或合法 schema 通过。"""
    assert _tampered_result("L2-004", {"subject_type": "银河"})
    fact_problems = _tampered_result(
        "L2-007",
        {
            "conclusion": "2026-10-01去西岸拍日出，日出06:45，建议出发。",
            "evidence": [
                {
                    "tool": "sun_times",
                    "field": "日出",
                    "confidence": "high",
                    "note": "离线合成：日出 06:45",
                }
            ],
        },
    )
    assert any("工具事实" in item for item in fact_problems)
    profile_problems = _tampered_result(
        "L2-001",
        {
            "evidence": [
                {"tool": "moon_phase", "field": "月相名称", "confidence": "high", "note": "新月"}
            ]
        },
    )
    assert not any("用户器材依据" in item for item in profile_problems)
    # 真实档案补齐器材来源，模型删除自报依据不能删除已核验事实。


def test_v2_cassette_cannot_fall_back_to_another_case() -> None:
    """相同题材也不能跨 case 复用响应，缺失独占夹具直接报错。"""
    cases = _load_cases()["pipeline_cases"]
    assert len({case["id"] for case in cases}) == 12
    assert len(_load_cassettes()) == 12
    with pytest.raises(KeyError, match="独占 cassette"):
        _run_pipeline_case(cases[1], {cases[0]["id"]: _load_cassettes()[cases[0]["id"]]})
    client = CassetteChatClient({"first": {"card": {"content": "{}"}}})
    client.set_group("second")
    with pytest.raises(KeyError, match="当前用例"):
        client.chat([{"role": "user", "content": "输出决策卡片"}])


def test_v2_rejects_wrong_tools_and_stale_tool_date() -> None:
    """工具选错或天气结果没有目标日期时，结构化事实门禁失败。"""
    case = next(row for row in _load_cases()["pipeline_cases"] if row["id"] == "L2-005")
    cassette = deepcopy(_load_cassettes()["L2-005"])
    card = DecisionCard.model_validate_json(cassette["card"]["content"])
    problems = assert_semantics(card, case, cassette, [], [])
    assert any("工具选择" in item for item in problems)

    cassette["tool_results"]["weather_forecast"]["每日预报"][0]["日期"] = "2026-09-08"
    calls = [
        (
            name,
            json.dumps(
                {
                    "date": "2026-09-29",
                    "tz_offset": "+08:00",
                    "latitude": 31.52,
                    "longitude": 121.95,
                    "sites": [{"题材": "日落"}],
                }
            ),
        )
        for name in case["semantic"]["tools"]
    ]
    problems = assert_semantics(card, case, cassette, calls, [])
    assert any("天气结果不包含目标日期" in item for item in problems)


def test_runner_l3_report_structure() -> None:
    """L3 报告：含 4 维均值、diff、交叉校验清单与逐条行。"""
    report = run_l3()
    assert {"决策合理性", "依据完整性", "个性化程度", "不确定性坦白"} <= set(report["means"].keys())
    assert "diff_vs_last" in report
    assert isinstance(report["cross_check_findings"], list)
    assert report["samples"] >= 10
    assert report["semantic_pass"] == report["samples"]


def test_assert_card_requires_confidence_detail() -> None:
    """B4-3 契约不变量：缺 confidence_detail 判违规（前端置信度三层的唯一来源）。"""
    problems = assert_card(_card(confidence_detail=None), {})
    assert any("confidence_detail" in problem for problem in problems)


def test_assert_card_checks_detail_consistency() -> None:
    """明细区间非法 / 三档计数与依据条数不一致 → 违规。"""
    card = _card()
    card.confidence_detail = ConfidenceDetail(level="high", score=90, low=10, high=95, high_count=2)
    problems = assert_card(card, {"min_evidence": 1})
    assert any("计数" in problem for problem in problems)

    card.confidence_detail = ConfidenceDetail(level="high", score=10, low=90, high=95, high_count=1)
    assert any("区间非法" in problem for problem in assert_card(card, {"min_evidence": 1}))


def test_assert_card_verdict_allowed_set() -> None:
    """verdict 可在期望里收紧（verdict_allowed）；缺省允许 go/wait/risk/空串。"""
    assert assert_card(_card(verdict="wait"), {"verdict_allowed": ["wait", "risk"]}) == []
    problems = assert_card(_card(verdict="go"), {"verdict_allowed": ["wait", "risk"]})
    assert any("verdict" in problem for problem in problems)
    assert assert_card(_card(verdict=""), {}) == []
