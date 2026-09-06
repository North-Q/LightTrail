"""LLM 结构化输出校验（E5-2）的 pytest 用例。

验证：
- 合法 JSON 一次通过（含 markdown 围栏剥离）；
- 校验失败把错误回传模型自愈重试（≤N 次）；
- 重试耗尽抛 SchemaError（管线据此降级）。
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from lighttrail.infra.validation import SchemaError, parse_with_retry
from lighttrail.orchestrator.schemas import DecisionCard, Intent

_INTENT = {"subject_type": "星空", "location": "崇明", "time_hint": "今晚", "mode": "inspiration"}


class _FakeLLM:
    """按脚本返回文本的伪 LLM（记录调用次数）。"""

    def __init__(self, outputs: list[str]) -> None:
        self._outputs = list(outputs)
        self.calls = 0

    def __call__(self, prompt: str) -> str:
        self.calls += 1
        return self._outputs.pop(0)


def test_parse_valid_json_once() -> None:
    """合法 JSON 一次通过。"""
    fake = _FakeLLM([json.dumps(_INTENT, ensure_ascii=False)])
    intent = parse_with_retry(Intent, fake, "解析请求")
    assert intent.subject_type == "星空"
    assert intent.time_hint == "今晚"
    assert fake.calls == 1


def test_parse_strips_markdown_fence() -> None:
    """markdown 代码围栏与前后叙述被剥离。"""
    text = f"好的，结果如下：\n```json\n{json.dumps(_INTENT, ensure_ascii=False)}\n```\n供参考。"
    fake = _FakeLLM([text])
    intent = parse_with_retry(Intent, fake, "解析")
    assert intent.mode == "inspiration"


def test_retry_after_invalid_then_valid() -> None:
    """先非法再合法：错误回传后自愈通过，调用 2 次。"""
    fake = _FakeLLM(["这不是 JSON", json.dumps(_INTENT, ensure_ascii=False)])
    intent = parse_with_retry(Intent, fake, "解析")
    assert intent.subject_type == "星空"
    assert fake.calls == 2
    assert fake._outputs == []  # 两次输出均被消费


def test_retry_honors_custom_max() -> None:
    """max_retries=1：2 次调用后仍失败即抛。"""
    fake = _FakeLLM(["坏", "也坏"])
    with pytest.raises(SchemaError):
        parse_with_retry(Intent, fake, "解析", max_retries=1)
    assert fake.calls == 2


def test_exhausted_retries_raise_schema_error() -> None:
    """默认 max_retries=2：3 次调用后抛 SchemaError。"""
    fake = _FakeLLM(["坏1", "坏2", "坏3"])
    with pytest.raises(SchemaError):
        parse_with_retry(Intent, fake, "解析")
    assert fake.calls == 3


def test_decision_card_requires_evidence_and_confidence() -> None:
    """DecisionCard 缺 evidence/confidence → 校验失败（结构性保证 M2 不落空）。"""
    with pytest.raises(ValidationError):
        DecisionCard.model_validate_json('{"conclusion": "去"}')
