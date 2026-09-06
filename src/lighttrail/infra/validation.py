"""LLM 结构化输出校验：pydantic 解析 + 错误回传自愈（≤N 次）+ 降级抛错。

设计要点（E5-2）：
- 全部管线 LLM 输出（Intent / DecisionCard / 诊断）先过 pydantic 校验；
- 校验失败把错误信息回传模型「重新输出合法 JSON」（≤2 次），仍失败抛
  SchemaError，由管线捕获降级到 ReAct 自由对话；
- 容错解析：剥掉 markdown 代码围栏、截取首个 JSON 对象，提高一次成功率。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

_MAX_RETRIES = 2


class SchemaError(Exception):
    """LLM 输出经重试仍无法解析为合法 schema。"""


def _extract_json(text: str) -> str:
    """从模型输出中抽取 JSON 对象文本（容忍 markdown 围栏与前后叙述）。

    Args:
        text: 模型原始输出。

    Returns:
        首个完整 JSON 对象文本（从首个 { 到末个 }）。

    Raises:
        SchemaError: 未找到可用的 JSON 对象。
    """
    content = text.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        lines = lines[1:] if lines else []
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        content = "\n".join(lines).strip()
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise SchemaError("模型输出中未找到 JSON 对象")
    return content[start : end + 1]


def parse_with_retry(
    schema: type[T],
    chat: Callable[[str], str],
    prompt: str,
    *,
    max_retries: int = _MAX_RETRIES,
) -> T:
    """让模型输出通过 schema 校验：失败回传错误重试，仍失败抛 SchemaError。

    Args:
        schema: pydantic 模型类（作出 JSON Schema 契约）。
        chat: 单轮纯文本 LLM 调用（接收 prompt，返回文本）。
        prompt: 首轮结构化输出指令（强约束 JSON）。
        max_retries: 自愈重试次数上限（默认 2）。

    Returns:
        校验通过的模型实例。

    Raises:
        SchemaError: 重试耗尽仍无法解析/校验通过。
    """
    current_prompt = prompt
    last_error = ""
    for attempt in range(max_retries + 1):
        raw = chat(current_prompt)
        try:
            return schema.model_validate_json(_extract_json(raw))
        except (SchemaError, ValidationError) as exc:
            last_error = str(exc)
            if attempt >= max_retries:
                break
            current_prompt = (
                f"{prompt}\n\n---\n模型上一次输出未通过结构化校验"
                f"（第 {attempt + 1} 次）：{last_error}\n"
                "请只输出一个符合上述 JSON 结构要求的完整对象，不要输出任何其他文字。"
            )
    raise SchemaError(f"{schema.__name__} 解析失败（重试 {max_retries} 次）：{last_error}")
