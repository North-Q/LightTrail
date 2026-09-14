"""前后端契约单一真源（B4-1/B4-3）的 pytest 用例。

验证：
- `contracts.events` 的 SSE 负载模型 dump 出的字段与线上帧一致；
- `SSEEventPayload` 是 type 判别联合（契约层可直接校验事件形态）；
- OpenAPI 文档覆盖 SSE 事件联合 + DecisionCard/ConfidenceDetail（前端生成物真源）；
- SSE 端点 200 响应标注 text/event-stream 且 schema 为 oneOf（无兜底 "type": "string"）；
- 档案端点响应模型进 schema（前端不再手抄档案类型）。

全部离线：只读应用与契约，不触网、不需要 API Key。
"""

from __future__ import annotations

import json

from pydantic import TypeAdapter

from lighttrail.api.events import to_payload
from lighttrail.api.openapi_export import render_openapi
from lighttrail.contracts.events import (
    CardEvent,
    DoneEvent,
    QueuedEvent,
    SSEEventPayload,
    StepEvent,
    ToolResultEvent,
)
from lighttrail.contracts.models import DecisionCard

# 三个 SSE 端点共用同一份事件契约
_SSE_PATHS = ("/api/chat", "/api/decide", "/api/photos/review")
# 生成物必须覆盖的契约模型（前端 generated.ts 的类型来源）
_CONTRACT_SCHEMAS = (
    "QueuedEvent",
    "StepEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    "TokenEvent",
    "CardEvent",
    "ErrorEvent",
    "DoneEvent",
    "DecisionCard",
    "ConfidenceDetail",
    "Source",
    "ProfilePayload",
)


def test_sse_payload_models_match_wire_fields() -> None:
    """事件模型 dump 出的键与线上帧一致（exclude_none：缺省字段不出现）。"""
    assert to_payload(QueuedEvent(position=2, session_id="s1")) == {
        "type": "queued",
        "position": 2,
        "session_id": "s1",
    }
    step = to_payload(StepEvent(name="采集_sun_times", session_id="s1", ts="t"))
    assert set(step) == {"type", "name", "input_summary", "output_summary", "session_id", "ts"}
    result = to_payload(ToolResultEvent(name="sun_times", result="日出: 05:42", session_id="s1", ts="t"))
    assert result["type"] == "tool_result"
    assert result["confidence"] == "low"
    assert "data" not in result  # 无结构化结果 → 不出现 data 键
    assert to_payload(DoneEvent(text="好", session_id="s1")) == {
        "type": "done",
        "text": "好",
        "session_id": "s1",
    }


def test_card_event_serialises_decision_card() -> None:
    """card 事件把 DecisionCard 序列化为 JSON 安全字典（前端直接消费）。"""
    card = DecisionCard(conclusion="去", evidence=[], confidence="low", verdict="go")
    payload = to_payload(CardEvent(card=card, text="去", session_id="s1"))
    assert payload["card"]["verdict"] == "go"
    assert payload["card"]["conclusion"] == "去"
    assert json.loads(json.dumps(payload, ensure_ascii=False))["type"] == "card"


def test_sse_event_payload_is_discriminated_union() -> None:
    """SSEEventPayload 按 type 判别（事件类型增删即契约变更）。"""
    adapter: TypeAdapter[SSEEventPayload] = TypeAdapter(SSEEventPayload)
    event = adapter.validate_python({"type": "done", "text": "好", "session_id": "s"})
    assert isinstance(event, DoneEvent)
    assert event.text == "好"


def test_openapi_exposes_sse_union_and_card_contract() -> None:
    """OpenAPI 覆盖事件联合与决策卡契约（generated.ts 的真源）。"""
    document = json.loads(render_openapi())
    schemas = document["components"]["schemas"]
    missing = [name for name in _CONTRACT_SCHEMAS if name not in schemas]
    assert missing == []

    for path in _SSE_PATHS:
        response = document["paths"][path]["post"]["responses"]["200"]
        assert list(response["content"]) == ["text/event-stream"]
        schema = response["content"]["text/event-stream"]["schema"]
        assert "oneOf" in schema
        assert "type" not in schema  # 兜底 {"type": "string"} 已清理（B4-1）
        assert schema["discriminator"]["propertyName"] == "type"


def test_openapi_profile_uses_contract_model() -> None:
    """档案端点用契约模型（前端类型由生成物提供，不再手抄）。"""
    document = json.loads(render_openapi())
    for method in ("get", "put"):
        schema = document["paths"]["/api/profile"][method]["responses"]["200"]["content"]
        assert schema["application/json"]["schema"]["$ref"].endswith("/ProfilePayload")