"""规则优先的摄影意图路由器。

已明确的摄影任务不应先消耗一次 LLM 做分类。此模块只处理可审计的
关键词规则，未命中时返回 ``None``，由调用方进入轻量 LLM 兜底通道。
"""

from __future__ import annotations

from dataclasses import dataclass

from lighttrail.contracts.models import Intent


@dataclass(frozen=True)
class IntentRule:
    """一条可配置的确定性意图分流规则。

    Args:
        name: 规则的稳定名称，供日志与测试识别。
        mode: 命中后进入的四管线模式。
        keywords: 任一关键词出现即命中。
    """

    name: str
    mode: str
    keywords: tuple[str, ...]


_SUBJECT_KEYWORDS: tuple[str, ...] = (
    "黄金时刻",
    "城市风光",
    "火烧云",
    "蓝调",
    "星空",
    "银河",
    "日出",
    "日落",
    "朝霞",
    "晚霞",
    "夜景",
)

_TIME_KEYWORDS: tuple[str, ...] = (
    "今天",
    "今晚",
    "明天",
    "后天",
    "这周末",
    "周末",
    "清晨",
    "傍晚",
    "日出前",
    "日落后",
)

# 顺序即优先级：同一句含“规划火烧云”时，规划意图应覆盖题材的临场缺省。
DEFAULT_INTENT_RULES: tuple[IntentRule, ...] = (
    IntentRule("照片复盘", "review", ("复盘", "点评照片", "分析照片", "修图建议")),
    IntentRule("拍摄规划", "planning", ("拍摄计划", "规划拍摄", "行程规划", "赶场", "排期", "安排路线")),
    IntentRule("临场决策", "live", ("值得冲", "该不该出门", "现在能拍", "临场", "火烧云", "晚霞")),
    IntentRule(
        "灵感方案",
        "inspiration",
        ("想去拍", "给我灵感", "银河", "星空", "日出", "日落", "朝霞", "蓝调", "夜景"),
    ),
)


class RuleIntentRouter:
    """把明确摄影请求直接映射到既有四条确定性管线。"""

    def __init__(self, rules: tuple[IntentRule, ...] = DEFAULT_INTENT_RULES) -> None:
        """初始化路由器。

        Args:
            rules: 有序规则表；调用方可注入项目部署所需的本地规则。
        """
        self._rules = rules

    # ------ 对外接口 ------
    def route(self, user_request: str) -> Intent | None:
        """尝试以规则解析请求，未命中时不猜测。

        Args:
            user_request: 原始用户请求。

        Returns:
            命中规则时的规范意图；无法确定时返回 None。
        """
        request = user_request.strip()
        if not request:
            return None
        for rule in self._rules:
            if any(keyword in request for keyword in rule.keywords):
                return Intent(
                    subject_type=self._subject_type(request),
                    time_hint=self._time_hint(request),
                    mode=rule.mode,
                )
        return None

    # ------ 内部实现 ------
    @staticmethod
    def _subject_type(user_request: str) -> str:
        """从固定题材词中取最长命中项，未命中时如实标为其他。"""
        matches = [keyword for keyword in _SUBJECT_KEYWORDS if keyword in user_request]
        return max(matches, key=len) if matches else "其他"

    @staticmethod
    def _time_hint(user_request: str) -> str:
        """提取首个明确时间词；不做不可靠的自然语言日期推断。"""
        for keyword in _TIME_KEYWORDS:
            if keyword in user_request:
                return keyword
        return ""


__all__ = ["DEFAULT_INTENT_RULES", "IntentRule", "RuleIntentRouter"]
