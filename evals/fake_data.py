"""E8 评估用确定性数据源与 cassette 客户端（不触网、不改管线代码）。

- FakeDispatcher：按用例分组返回确定性工具数据（可注入「缺天气 Key / 极昼」等边界）；
- CassetteChatClient：按 prompt 语义回放 cassette（意图 / 综合两段），L2 默认零 LLM 成本；
- RecordingChatClient：包一层真实客户端，把意图/综合响应录制进 cassette（E8 开场最小样本）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from lighttrail.infra.trace import Recorder, null_trace

logger = logging.getLogger("lighttrail.evals")

# cassette 两组 prompt 语义判据
_INTENT_MARK = "把用户的一句话请求解析为规范意图"
_CARD_MARK = "决策卡片"


def _default_payload() -> dict[str, Any]:
    """常用确定性天文/天气数据（与 tests 同源，避免触网）。"""
    return {
        "moon_phase": {
            "月相名称": "新月",
            "照亮比例（%）": 2,
            "月光影响建议": "低",
            "数据来源": "天文计算（确定性）",
        },
        "galaxy_visibility": {
            "可见窗口": [{"开始": "20:10", "结束": "23:50"}],
            "最高高度角": 55,
            "提示": "窗口良好",
            "数据来源": "天文计算（确定性）",
        },
        "weather_forecast": {
            "每日预报": [
                {"日期": "2026-09-08", "平均云量（%）": 30},
                {"日期": "2026-09-09", "平均云量（%）": 18},
                {"日期": "2026-09-10", "平均云量（%）": 55},
            ],
            "数据来源": "Open-Meteo（确定性 Fake）",
        },
        "sun_times": {"日出": "05:42", "日落": "18:06", "数据来源": "天文计算（确定性）"},
        "sunset_glow_score": {
            "评分（0-100）": 62,
            "等级": "中等（可看趋势再定）",
            "数据来源": "启发式规则（确定性）",
        },
    }


class FakeDispatcher:
    """确定性工具数据源：分组可注入边界（缺天气 / 极昼）。

    Args:
        scenario: 场景名（normal / missing_weather / polar），影响天气与太阳数据。
    """

    def __init__(self, scenario: str = "normal") -> None:
        self._data = _default_payload()
        if scenario == "missing_weather":
            self._data["weather_forecast"] = {"error": "未配置天气 API Key（边界用例）"}
            self._data["sunset_glow_score"] = {"error": "缺少天气数据无法评分（边界用例）"}
        elif scenario == "polar":
            self._data["sun_times"] = {"提示": "极昼期间无日出日落", "数据来源": "天文计算（确定性）"}
            self._data["sunset_glow_score"] = {
                "评分（0-100）": 0,
                "等级": "极昼无黄昏",
                "提示": "极昼期间无火烧云窗口",
                "数据来源": "启发式规则（确定性）",
            }
        self.calls: list[tuple[str, str]] = []

    def __call__(self, name: str, args: str) -> str:
        """工具名 → 结果 JSON（与 registry.dispatch 同签名）。"""
        self.calls.append((name, args))
        if name in self._data:
            return json.dumps(self._data[name], ensure_ascii=False)
        return json.dumps({"error": f"FakeDispatcher 无数据：{name}"}, ensure_ascii=False)


def dispatch_with_trace(
    raw: FakeDispatcher, recorder: Recorder | None = None
) -> Any:
    """包一层 recorder 记录（与注册表直调一致），供 Orchestrator 注入。"""
    recorder = recorder or null_trace

    def dispatch(name: str, args: str) -> str:
        result = raw(name, args)
        recorder.record_tool(name, args, result)
        return result

    return dispatch


class CassetteChatClient:
    """按 cassette 回放的伪客户端：L2 零 LLM 成本。

    Args:
        cassettes: {group: {"intent": {"content": ...}, "card": {"content": ...}}}。
        group_for: 用例 → 分组映射函数（或 dict），用于选中 cassette。
    """

    def __init__(
        self,
        cassettes: dict[str, dict[str, dict[str, str]]],
        group_for: dict[str, str] | None = None,
    ) -> None:
        self._cassettes = cassettes
        self._group_for = group_for or {}
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages, **kwargs) -> dict[str, Any]:
        """回放：按最后一条用户消息语义判断意图/综合，选中当前分组 cassette。"""
        self.calls.append({"messages": messages, **kwargs})
        user_text = ""
        for message in reversed(messages):
            if isinstance(message, dict) and message.get("role") == "user":
                user_text = str(message.get("content", ""))
                break
        # 意图理解在管线最前，先按 mark 精确路由；综合/复盘 prompt 均含卡片指令
        if _INTENT_MARK in user_text:
            return {"role": "assistant", "content": self._pick("intent")}
        return {"role": "assistant", "content": self._pick("card")}

    def set_group(self, group: str) -> None:
        """切换当前用例分组（每次用例前由 runner 设置）。"""
        self._current = group

    def _pick(self, kind: str) -> str:
        """当前分组 cassette 的 content；缺分组回退任一可用 cassette。"""
        current = getattr(self, "_current", None)
        for group in (current, *self._group_for.values(), *self._cassettes.keys()):
            cassette = self._cassettes.get(group)
            if cassette and kind in cassette and cassette[kind].get("content"):
                return cassette[kind]["content"]
        raise KeyError(f"缺少 cassette：{kind}（分组 {current}）")


class RecordingChatClient:
    """真实客户端包装：录制意图/综合响应，供 --record 生成 cassette。

    Args:
        inner: 真实 ChatClient。
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.recorded: dict[str, str] = {}

    def chat(self, messages, **kwargs) -> dict[str, Any]:
        """调用真实客户端并记录 content（意图/综合两段，按 message 语义归档）。"""
        user_text = ""
        for message in reversed(messages):
            if isinstance(message, dict) and message.get("role") == "user":
                user_text = str(message.get("content", ""))
                break
        resp = self._inner.chat(messages, **kwargs)
        content = resp.get("content", "")
        kind = "intent" if _INTENT_MARK in user_text else "card"
        self.recorded[kind] = content
        return resp


def intents(subject: str, mode: str = "inspiration", location: str = "", time_hint: str = "这周末") -> str:
    """构造合法 Intent JSON（确定性）。"""
    return json.dumps(
        {"subject_type": subject, "location": location, "time_hint": time_hint, "mode": mode},
        ensure_ascii=False,
    )


def card(
    conclusion: str,
    *,
    confidence: str = "medium",
    time_window: str = "",
    locations: list[dict[str, str]] | None = None,
    params: list[dict[str, str]] | None = None,
    evidence: list[dict[str, str]] | None = None,
    degraded: str = "",
) -> str:
    """构造合法 DecisionCard JSON（确定性）。"""
    payload: dict[str, Any] = {
        "conclusion": conclusion,
        "evidence": evidence
        or [{"tool": "确定性规则", "field": "演示", "confidence": confidence, "note": "cassette 回放"}],
        "confidence": confidence,
        "time_window": time_window,
        "locations": locations or [{"name": "默认机位（上海）", "reason": "演示"}],
        "params": params or [],
        "alternatives": [],
        "degraded": degraded,
    }
    return json.dumps(payload, ensure_ascii=False)


__all__ = [
    "CassetteChatClient",
    "FakeDispatcher",
    "RecordingChatClient",
    "card",
    "dispatch_with_trace",
    "intents",
]
