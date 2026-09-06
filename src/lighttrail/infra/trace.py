"""可解释性 / 可观测性地基：TraceRecorder 被动记录 LLM / 工具 / 管线步骤事件。

设计要点（架构 v2.0 §2.6，E2-1 落地）：
- 三个写入点：每次 LLM 调用（record_llm）、每次工具调用（record_tool）、
  每个管线步骤（record_step），记录器只做被动记录，不改业务行为；
- 三种消费形态：运行时注入 prompt（to_prompt_section，E2-2 接入第⑤层）、
  实时事件流（subscribe，E7-4 挂 SSE 适配器）、事后结构化报告（to_report，
  M2「建议依据 / 来源与置信度」的数据来源）；
- 置信度不做模型自评：规则表收敛在 infra/confidence.py（确定性 → high、
  天气预报按时效 → high/medium、启发式组合 → medium、未知 → low），
  TraceRecorder 只负责把规则结果落进事件与报告；
- 每条工具来源带主字段标注（field），TraceReport.sources 输出
  [{tool, field, confidence}] 三元组，供 M2 依据展示与 E8 评估回归；
- 可关闭：NullTrace 关闭态零开销（不产生事件、不分配 payload），
  Agent/注册表默认使用它，行为与未接入时完全一致；
- to_report(since=cursor) 支持按调用边界切片报告（Agent.run_with_trace 的
  「本轮」报告依赖它），cursor 在锁内读取保证线程安全。

线程安全：事件列表与监听器在锁内追加/快照，订阅回调在锁外同步派发。
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from lighttrail.infra.confidence import confidence_for_tool

logger = logging.getLogger("lighttrail.trace")

# 事件类型
KIND_LLM = "llm"
KIND_TOOL = "tool"
KIND_STEP = "step"

# 参数 / 结果截断上限（字符）
_MAX_ARGS_CHARS = 200
_MAX_RESULT_CHARS = 300
# 结果单值摘要上限（字符）
_MAX_VALUE_CHARS = 60
# 数据来源字段候选键（工具返回 dict 中表示来源的键名）
_SOURCE_KEYS = ("数据来源", "来源", "data_source")

# 圆圈序号（1-20），用于 to_prompt_section 的「①②③」样式
_CIRCLED_NUMBERS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"

# 工具 → 报告主字段（TraceReport.sources 的 field 标注，供 M2「依据」展示）
_MAIN_FIELD: dict[str, str] = {
    "get_current_time": "时间",
    "equivalent_exposure": "等效方案",
    "star_shutter_rule": "最大快门",
    "nd_long_exposure": "曝光快门",
    "sun_times": "太阳时刻",
    "sun_position": "太阳方位",
    "moon_phase": "月相",
    "moon_events": "月升月落",
    "galaxy_visibility": "银心可见窗口",
    "weather_forecast": "每日预报",
    "sunset_glow_score": "评分",
    "match_sites": "匹配结果",
}
# 结果 dict 中不进入主字段的元信息键
_META_KEYS = frozenset({"数据来源", "来源", "data_source", "说明", "提示", "位置", "时区"})


def _truncate(text: str, limit: int) -> str:
    """把文本截断到 limit 字符，超限补省略号。"""
    if len(text) <= limit:
        return text
    cut = max(0, limit - 1)
    return text[:cut] + "…"


def _now_iso() -> str:
    """当前 UTC 时间的 ISO 8601 字符串（事件时间戳）。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class TraceEvent:
    """一次可观测事件的快照。

    Attributes:
        kind: 事件类型，llm / tool / step。
        name: 事件名（模型名 / 工具名 / 步骤名）。
        payload: 事件详情（参数、结果摘要、耗时等）。
        ts: 事件时间戳（UTC ISO 8601）。
    """

    kind: str
    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=_now_iso)


@dataclass(frozen=True)
class SourceRef:
    """M2 来源标注：某条建议依据哪个工具来源、对应字段、置信度如何。"""

    tool: str
    data_source: str = ""
    confidence: str = "low"
    field: str = ""


@dataclass(frozen=True)
class ToolCallRef:
    """一次工具调用的结构化摘要（供 TraceReport 与审计）。"""

    name: str
    arguments_summary: str = ""
    result_summary: str = ""
    data_source: str = ""
    confidence: str = "low"
    field: str = ""
    elapsed_ms: float = 0.0


@dataclass(frozen=True)
class StepRef:
    """一个管线步骤的结构化摘要（输入/输出快照）。"""

    name: str
    input_summary: str = ""
    output_summary: str = ""


@dataclass(frozen=True)
class TraceReport:
    """事后结构化报告：工具调用链 + 管线步骤 + LLM 调用概览。

    sources 由工具调用聚合派生（每条工具调用一个来源标注），
    是 M2「建议依据 / 来源与置信度」的数据来源。
    """

    llm_calls: tuple[dict[str, Any], ...] = ()
    tool_calls: tuple[ToolCallRef, ...] = ()
    steps: tuple[StepRef, ...] = ()

    @property
    def sources(self) -> tuple[SourceRef, ...]:
        """按调用顺序返回来源标注列表（含 data_source 与置信度）。"""
        return tuple(
            SourceRef(tool=ref.name, data_source=ref.data_source, confidence=ref.confidence, field=ref.field)
            for ref in self.tool_calls
        )


def _main_field_for(name: str, result_data: dict[str, Any]) -> str:
    """确定工具来源的主字段：先查映射表，缺省取结果的首个业务键。"""
    if name in _MAIN_FIELD:
        return _MAIN_FIELD[name]
    for key in result_data:
        if key not in _META_KEYS and not key.startswith("_"):
            return key
    return ""


def _extract_source(result_data: dict[str, Any]) -> str:
    """从工具结果 dict 中提取数据来源字段（首个命中的键值）。"""
    for key in _SOURCE_KEYS:
        value = result_data.get(key)
        if value is not None:
            return str(value)
    return ""


def _summarize_json(raw: str, limit: int) -> str:
    """把工具结果 JSON 压缩成一行摘要（顶层键: 值 平铺，超长截断）。"""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return _truncate(raw, limit)
    if not isinstance(data, dict):
        return _truncate(str(data), limit)
    parts: list[str] = []
    for key, value in data.items():
        if key.startswith("_"):  # 跳过内部字段
            continue
        if isinstance(value, (dict, list)):
            compact = _truncate(json.dumps(value, ensure_ascii=False), _MAX_VALUE_CHARS)
            parts.append(f"{key}: {compact}")
        else:
            parts.append(f"{key}: {value}")
    return _truncate("，".join(parts), limit)


def _numbered(index: int) -> str:
    """把序号格式化为 ① ② … ⑳ / 21. 22. … 样式。"""
    if 1 <= index <= len(_CIRCLED_NUMBERS):
        return _CIRCLED_NUMBERS[index - 1]
    return f"{index}."


class TraceRecorder:
    """被动事件记录器：记录 + 订阅 + 注入文本 + 结构化报告。"""

    def __init__(self) -> None:
        self._events: list[TraceEvent] = []
        self._listeners: list[Callable[[TraceEvent], None]] = []
        self._lock = threading.Lock()

    # ------ 对外接口：记录 ------
    def record_llm(
        self,
        model: str,
        *,
        prompt_summary: str = "",
        duration_s: float = 0.0,
        tokens: int | None = None,
    ) -> None:
        """记录一次 LLM 调用。

        Args:
            model: 模型名。
            prompt_summary: prompt 摘要（如消息数、首条用户消息片段）。
            duration_s: 调用耗时（秒）。
            tokens: 本次调用 token 用量（未知时 None，E4-2 配额账本接入后补齐）。
        """
        self._emit(
            KIND_LLM,
            model,
            {"模型": model, "prompt_summary": prompt_summary, "耗时_秒": duration_s, "tokens": tokens},
        )

    def record_tool(
        self,
        name: str,
        arguments: str,
        result: str,
        *,
        elapsed_ms: float = 0.0,
    ) -> None:
        """记录一次工具调用（参数与结果自动截断、提取数据来源）。

        Args:
            name: 工具名。
            arguments: 工具参数（JSON 字符串）。
            result: 工具返回（JSON 字符串）。
            elapsed_ms: 调用耗时（毫秒）。
        """
        args_text = _truncate(arguments, _MAX_ARGS_CHARS)
        result_text = _truncate(result, _MAX_RESULT_CHARS)
        result_data: dict[str, Any] = {}
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict):
                result_data = parsed
        except (ValueError, TypeError):
            result_data = {}
        data_source = _extract_source(result_data)
        confidence = confidence_for_tool(name, result_data)
        field = _main_field_for(name, result_data)
        result_summary = _summarize_json(result, _MAX_RESULT_CHARS)
        self._emit(
            KIND_TOOL,
            name,
            {
                "参数摘要": args_text,
                "结果摘要": result_summary,
                "结果原文": result_text,
                "数据来源": data_source,
                "置信度": confidence,
                "来源字段": field,
                "耗时_ms": elapsed_ms,
            },
        )

    def record_step(self, name: str, *, input_summary: str = "", output_summary: str = "") -> None:
        """记录一个管线步骤（输入/输出快照）。

        Args:
            name: 步骤名。
            input_summary: 输入摘要。
            output_summary: 输出摘要。
        """
        self._emit(KIND_STEP, name, {"输入摘要": input_summary, "输出摘要": output_summary})

    # ------ 对外接口：订阅 / 消费 ------
    def subscribe(self, callback: Callable[[TraceEvent], None]) -> Callable[[], None]:
        """订阅事件流，返回取消订阅函数。

        Args:
            callback: 事件回调，收到 TraceEvent（同步派发，异常被记录不打断）。

        Returns:
            取消订阅的闭包。
        """
        with self._lock:
            self._listeners.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._listeners:
                    self._listeners.remove(callback)

        return unsubscribe

    def to_prompt_section(self, limit: int = 12) -> str:
        """生成精简轨迹注入文本（最近 limit 条工具/步骤事件）。

        Args:
            limit: 最多纳入的轨迹条数。

        Returns:
            形如「① 调用 sun_times：日出 05:12」的多行文本；无事件时返回空串。
        """
        rows: list[str] = []
        row_index = 0  # 只对工具/步骤事件计数，LLM 事件不占序号
        with self._lock:
            events = list(self._events)
        for event in events:
            if event.kind == KIND_TOOL:
                row_index += 1
                payload = event.payload
                summary = payload.get("结果摘要") or payload.get("结果原文") or ""
                rows.append(f"{_numbered(row_index)} 调用 {event.name}：{summary}")
            elif event.kind == KIND_STEP:
                row_index += 1
                output = event.payload.get("输出摘要") or ""
                rows.append(f"{_numbered(row_index)} {event.name}：{output}")
        return "\n".join(rows[-limit:])

    def to_report(self, since: int = 0) -> TraceReport:
        """生成结构化报告（LLM 调用概览 + 工具调用链 + 管线步骤 + 来源标注）。

        Args:
            since: 事件游标，只汇总该游标之后的事件（配合 cursor() 实现
                「本轮调用」切片；缺省 0 为全量）。

        Returns:
            结构化报告快照。
        """
        with self._lock:
            events = list(self._events[since:])
        llm_calls: list[dict[str, Any]] = []
        tool_calls: list[ToolCallRef] = []
        steps: list[StepRef] = []
        for event in events:
            if event.kind == KIND_LLM:
                llm_calls.append(event.payload)
            elif event.kind == KIND_TOOL:
                payload = event.payload
                tool_calls.append(
                    ToolCallRef(
                        name=event.name,
                        arguments_summary=payload.get("参数摘要", ""),
                        result_summary=payload.get("结果摘要", ""),
                        data_source=payload.get("数据来源", ""),
                        confidence=payload.get("置信度", "low"),
                        field=payload.get("来源字段", ""),
                        elapsed_ms=payload.get("耗时_ms", 0.0),
                    )
                )
            elif event.kind == KIND_STEP:
                payload = event.payload
                steps.append(
                    StepRef(
                        name=event.name,
                        input_summary=payload.get("输入摘要", ""),
                        output_summary=payload.get("输出摘要", ""),
                    )
                )
        return TraceReport(
            llm_calls=tuple(llm_calls),
            tool_calls=tuple(tool_calls),
            steps=tuple(steps),
        )

    def cursor(self) -> int:
        """返回当前事件游标（用于 to_report(since=...) 切片）。"""
        with self._lock:
            return len(self._events)

    def clear(self) -> None:
        """清空已记录事件（新会话开始时使用）。"""
        with self._lock:
            self._events.clear()

    # ------ 内部实现 ------
    def _emit(self, kind: str, name: str, payload: dict[str, Any]) -> None:
        """记录事件并向订阅者同步派发（列表与监听器快照在锁内完成）。"""
        event = TraceEvent(kind=kind, name=name, payload=payload)
        with self._lock:
            self._events.append(event)
            listeners = list(self._listeners)
        for callback in listeners:
            try:
                callback(event)
            except Exception:
                logger.exception("trace 订阅者处理事件失败：%s", event.kind)


class NullTrace:
    """关闭态记录器：全部方法零实现，行为与「未接入 trace」完全一致。"""

    def record_llm(self, model: str, **kwargs: Any) -> None:
        """关闭态空实现。"""

    def record_tool(self, name: str, arguments: str, result: str, **kwargs: Any) -> None:
        """关闭态空实现。"""

    def record_step(self, name: str, **kwargs: Any) -> None:
        """关闭态空实现。"""

    def subscribe(self, callback: Callable[[TraceEvent], None]) -> Callable[[], None]:
        """关闭态空实现（返回空取消函数）。"""

        def _noop() -> None:
            return None

        return _noop

    def to_prompt_section(self, limit: int = 12) -> str:
        """关闭态空实现（返回空串）。"""
        return ""

    def to_report(self, since: int = 0) -> TraceReport:
        """关闭态空实现（返回空报告）。"""
        return TraceReport()

    def cursor(self) -> int:
        """关闭态空实现（返回 0）。"""
        return 0

    def clear(self) -> None:
        """关闭态空实现。"""


# 全局关闭态单例：Agent/注册表默认使用
null_trace = NullTrace()

# 记录器类型别名（供依赖方注解）
Recorder = TraceRecorder | NullTrace
