"""trace 出口安全与 OTel 命名的 pytest 用例（B3-4）。

验证（v4 §3 D12 命名跟标准、D7 密钥安全四问之「防日志泄漏」）：
- `redact()` 掩码已知密钥形态（sk- 前缀 / Bearer / api_key=），且不误伤普通文本；
- TraceRecorder 载荷**白名单**：未在白名单内的键被丢弃（防整包 dumps 请求体）；
- 事件载荷经掩码后不含敏感形态（trace / SSE 出口都不泄漏 Key）；
- `to_otel_attributes()`：内部中文键 → OTel GenAI 语义键（gen_ai.*）+ 自定义命名空间。
"""

from __future__ import annotations

import json

from lighttrail.adapters.trace import to_otel_attributes
from lighttrail.contracts.events import KIND_LLM, KIND_STEP, KIND_TOOL, TraceEvent
from lighttrail.infra.redact import contains_secret, redact
from lighttrail.infra.trace import TraceRecorder


def test_redact_masks_known_secret_shapes() -> None:
    """已知密钥形态被掩码；普通文本不误伤。"""
    assert "sk-abcdef123456" not in redact("key=sk-abcdef123456")
    assert "***" in redact("key=sk-abcdef123456")
    assert "***" in redact("Authorization: Bearer abcdefghijklmnop")
    assert "***" in redact('{"api_key": "abcdefghijklmnop"}')
    text = "日出 05:12，云量 30%，快门 20s"
    assert redact(text) == text


def test_recorder_drops_non_whitelisted_payload_keys() -> None:
    """载荷白名单：未在白名单内的键被丢弃（防把请求体整包塞进事件）。"""
    recorder = TraceRecorder()
    captured: list[TraceEvent] = []
    recorder.subscribe(captured.append)
    recorder.emit(
        TraceEvent(
            kind=KIND_TOOL,
            name="sun_times",
            payload={
                "结果摘要": "日出 05:12",
                "请求体原文": {"api_key": "sk-abcdef123456"},  # 非白名单键 → 丢弃
                "未知键": "x",
            },
        )
    )
    assert recorder.to_report().tool_calls[0].result_summary == "日出 05:12"
    # 事件载荷只留白名单键（"请求体原文"/"未知键" 被丢弃）
    assert set(captured[0].payload) <= {
        "参数摘要", "结果摘要", "结果原文", "结果数据", "数据来源", "置信度", "来源字段", "耗时_ms"
    }


def test_recorder_payload_is_redacted() -> None:
    """事件载荷经掩码：trace 与 SSE 出口都不会泄漏 Key。"""
    recorder = TraceRecorder()
    captured: list[TraceEvent] = []
    recorder.subscribe(captured.append)
    recorder.record_tool(
        "photo_analysis",
        '{"api_key": "sk-abcdef1234567890"}',
        '{"结果": "ok", "token": "Bearer abcdefghijklmnop"}',
    )
    joined = " ".join(str(value) for value in captured[0].payload.values())
    assert not contains_secret(joined)
    assert "***" in joined


def test_otel_attributes_mapping() -> None:
    """内部中文键 → OTel GenAI 语义键（gen_ai.* + lighttrail.* 自定义命名空间）。"""
    llm = to_otel_attributes(
        TraceEvent(
            kind=KIND_LLM,
            name="ecnu-plus",
            payload={"模型": "ecnu-plus", "tokens": 150, "输入_tokens": 120, "输出_tokens": 30, "耗时_秒": 1.5},
        )
    )
    assert llm["gen_ai.operation.name"] == "chat"
    assert llm["gen_ai.request.model"] == "ecnu-plus"
    assert llm["gen_ai.usage.total_tokens"] == 150
    assert llm["gen_ai.usage.input_tokens"] == 120
    assert llm["lighttrail.llm.duration_s"] == 1.5

    tool = to_otel_attributes(
        TraceEvent(
            kind=KIND_TOOL,
            name="sun_times",
            payload={"数据来源": "astral", "置信度": "high", "来源字段": "太阳时刻", "耗时_ms": 3.2},
        )
    )
    assert tool["gen_ai.operation.name"] == "execute_tool"
    assert tool["gen_ai.tool.name"] == "sun_times"
    assert tool["lighttrail.tool.confidence"] == "high"
    assert tool["lighttrail.tool.duration_ms"] == 3.2

    step = to_otel_attributes(TraceEvent(kind=KIND_STEP, name="采集_sun_times"))
    assert step["lighttrail.pipeline.step"] == "采集_sun_times"


def test_recorder_keeps_structured_result_and_redacts_nested() -> None:
    """结构化结果进事件（B4-4 前端消费），且容器内文本同样过出口掩码。"""
    recorder = TraceRecorder()
    captured: list[TraceEvent] = []
    recorder.subscribe(captured.append)
    recorder.record_tool(
        "sun_times",
        "{}",
        json.dumps({"日出": "05:42", "备注": "Bearer abcdefghijklmnop"}, ensure_ascii=False),
    )
    payload = captured[0].payload
    assert payload["结果数据"]["日出"] == "05:42"
    assert "***" in payload["结果数据"]["备注"]
    assert not contains_secret(str(payload["结果数据"]))


def test_recorder_omits_structured_data_for_non_json_result() -> None:
    """非 JSON 结果不产生 结果数据 键（SSE 相应不带 data 字段）。"""
    recorder = TraceRecorder()
    captured: list[TraceEvent] = []
    recorder.subscribe(captured.append)
    recorder.record_tool("probe", "{}", "not-json")
    assert "结果数据" not in captured[0].payload
