"""规则优先的摄影意图路由器。

已明确的摄影任务不应先消耗一次 LLM 做分类。此模块只处理可审计的
关键词规则，未命中时返回 ``None``，由调用方进入轻量 LLM 兜底通道。
"""

from __future__ import annotations

import re
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
    "下周末",
    "下周六",
    "下周日",
    "这周六",
    "这周日",
    "本周六",
    "本周日",
    "周六",
    "周日",
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
    IntentRule(
        "拍摄规划", "planning", ("拍摄计划", "规划拍摄", "行程规划", "赶场", "排期", "安排路线")
    ),
    IntentRule("临场决策", "live", ("值得冲", "该不该出门", "现在能拍", "临场", "火烧云", "晚霞")),
    IntentRule(
        "灵感方案",
        "inspiration",
        ("想去拍", "给我灵感", "银河", "星空", "日出", "日落", "朝霞", "蓝调", "夜景"),
    ),
)

_UNSUPPORTED_KEYWORDS: tuple[str, ...] = ("订机票", "订酒店", "叫车", "订餐", "下单购买")
_UNSUPPORTED_REPLY = "暂不支持代替你完成交易或出行预订；我可以帮你规划拍摄时间、机位和参数。"
_PLANNER_KEYWORDS: tuple[str, ...] = ("跨题材", "复合任务", "多步骤")


class RuleIntentRouter:
    """把明确摄影请求直接映射到既有四条确定性管线。"""

    def __init__(self, rules: tuple[IntentRule, ...] = DEFAULT_INTENT_RULES) -> None:
        """初始化路由器。

        Args:
            rules: 有序规则表；调用方可注入项目部署所需的本地规则。
        """
        self._rules = rules

    # ------ 对外接口 ------
    def unsupported_reply(self, user_request: str) -> str | None:
        """识别明确超出工具集的代办请求，并返回诚实替代建议。"""
        if any(keyword in user_request for keyword in _UNSUPPORTED_KEYWORDS):
            return _UNSUPPORTED_REPLY
        return None

    def needs_controlled_plan(self, user_request: str) -> bool:
        """判断是否为四条固定管线之外的明确复合规划任务。"""
        return any(keyword in user_request for keyword in _PLANNER_KEYWORDS)

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
                location = self._location_hint(request)
                subject_request = request.replace(location, "", 1) if location else request
                return Intent(
                    subject_type=self._subject_type(subject_request),
                    location=location,
                    time_hint=self._time_hint(request),
                    mode=rule.mode,
                )
        return None

    def ground(self, intent: Intent, user_request: str) -> Intent:
        """用原文中可直接核实的地点、日期和题材修正 LLM 意图。"""
        location = self._location_hint(user_request)
        subject_request = user_request.replace(location, "", 1) if location else user_request
        subject = self._subject_type(subject_request)
        time_hint = self._time_hint(user_request)
        return intent.model_copy(
            update={
                "subject_type": subject if subject != "其他" else intent.subject_type,
                "location": location
                or (intent.location if intent.location and intent.location in user_request else ""),
                "time_hint": time_hint or intent.time_hint,
            }
        )

    # ------ 内部实现 ------
    @staticmethod
    def _subject_type(user_request: str) -> str:
        """从固定题材词中取最长命中项，未命中时如实标为其他。"""
        matches = [keyword for keyword in _SUBJECT_KEYWORDS if keyword in user_request]
        return max(matches, key=len) if matches else "其他"

    @staticmethod
    def _time_hint(user_request: str) -> str:
        """提取首个明确时间词；不做不可靠的自然语言日期推断。"""
        iso_date = re.search(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", user_request)
        if iso_date is not None:
            return iso_date.group()
        for keyword in _TIME_KEYWORDS:
            if keyword in user_request:
                return keyword
        return ""

    @staticmethod
    def _location_hint(user_request: str) -> str:
        """只提取明确的「在/去/到某地拍」地点短语，未识别时留空。"""
        match = re.search(
            r"(?:在|去|到|前往)([^\s，。！？拍看]{2,30}?)(?:拍|摄影|看|观测)",
            user_request,
        )
        if match is None:
            return ""
        return match.group(1).removesuffix("机位")


__all__ = ["DEFAULT_INTENT_RULES", "IntentRule", "RuleIntentRouter"]
