"""B7-1 规则优先意图路由回归。"""

from __future__ import annotations

import pytest

from lighttrail.application.router import IntentRule, RuleIntentRouter


@pytest.mark.parametrize(
    ("user_request", "mode", "subject"),
    [
        ("帮我复盘这张银河照片", "review", "银河"),
        ("规划拍摄火烧云的赶场路线", "planning", "火烧云"),
        ("明天傍晚火烧云值得冲吗", "live", "火烧云"),
        ("这周末想去拍银河", "inspiration", "银河"),
    ],
)
def test_rules_route_each_pipeline(user_request: str, mode: str, subject: str) -> None:
    """四类明确请求直接命中对应管线，不依赖模型判定。"""
    intent = RuleIntentRouter().route(user_request)
    assert intent is not None
    assert intent.mode == mode
    assert intent.subject_type == subject


def test_rule_router_returns_none_for_ambiguous_request() -> None:
    """不明确的开放请求不由规则猜测，交给后续 LLM 兜底。"""
    assert RuleIntentRouter().route("最近有什么适合练习的摄影方向") is None


def test_rule_table_can_be_replaced() -> None:
    """部署可注入本地规则表，而不改动路由器实现。"""
    router = RuleIntentRouter((IntentRule("本地夜拍", "planning", ("夜游",)),))
    intent = router.route("帮我安排夜游拍摄")
    assert intent is not None
    assert intent.mode == "planning"
