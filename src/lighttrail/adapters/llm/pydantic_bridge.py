"""PydanticAI 自定义 Model 桥（B2-5，ADR-002/003 语义不丢）。

设计要点：
- **框架管通用机制**（ReAct 循环 / DI / 输出校验重试 / TestModel），本桥只做「消息与工具
  的双向翻译 + 端口调用」——LLM 的实际调用一律经 `contracts.LLMProvider` 端口；
- **平台差异留在适配层**：`thinking` / `reasoning_effort` 从 `ModelSettings`（含
  `extra_body`）取出后透传给 provider；命名参数还是 extra_body 由 `llm/client.py` 的
  `_NATIVE_REASON_PARAMS` 探测决定（ADR-003 双路径原样保留）。其余 extra_body 键当前忽略
  （TODO(B3-4): 事件载荷白名单落地后按需扩展）；
- **消息映射**：pydantic-ai `ModelMessage` → OpenAI chat dict（system / user / assistant / tool）；
  `instructions` 与 `SystemPromptPart` 归并为**前置 system 消息**（静态前缀，利于平台 prompt 缓存命中）；
- **工具映射**：`ToolDefinition` → OpenAI function schema（参数 JSON Schema 原样透传）；
- **用量**：provider 的 usage 回调 → `RequestUsage(input_tokens, output_tokens)`，供框架侧成本观测；
- **流式**：本桥暂不实现 `request_stream`（PRD 附录 A2：token 流式延后；Web 端的「trace 即 UI」
  实时性由 trace→SSE 桥提供）。B3/B7 需要时再补。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncGenerator, Sequence
from typing import Any

from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import (
    BinaryImage,
    ImageUrl,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelResponsePart,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import (
    Model,
    ModelRequestParameters,
    ModelSettings,
    RequestUsage,
    StreamedResponse,
    ToolDefinition,
)
from pydantic_ai.profiles import ModelProfile, ModelProfileSpec

from lighttrail.contracts.llm import LLMProvider
from lighttrail.infra.trace import Recorder, null_trace

logger = logging.getLogger("lighttrail.adapters.llm.bridge")

# 桥的默认能力档案：支持工具调用与内联 system，不声明 JSON Schema/对象输出
# （结构化输出走 pydantic-ai 的 tool 或 prompted 模式，见 D4 双轨边界）
_DEFAULT_PROFILE: ModelProfile = ModelProfile(
    supports_tools=True,
    supports_json_schema_output=False,
    supports_json_object_output=False,
    supports_inline_system_prompts=True,
)

# 桥支持的平台扩展参数键（其余 extra_body 键忽略）
_REASON_KEYS = ("thinking", "reasoning_effort")


class LightTrailModel(Model):
    """把 PydanticAI 的模型调用接到 LightTrail 的 `LLMProvider` 端口。

    Args:
        provider: LLM 端口实现（如 `ChatClientProvider`）。
        model_name: 传给供应商的模型名。
        system: OTel `gen_ai.system` 语义值（供应商标识，默认 "lighttrail"）。
        settings: 模型级默认设置（temperature / extra_body 等）。
        profile: 能力档案覆盖（缺省见 `_DEFAULT_PROFILE`）。
        recorder: 可观测性记录器（记录每次 LLM 调用的耗时与 token；缺省关闭态）。
    """

    def __init__(
        self,
        provider: LLMProvider,
        *,
        model_name: str,
        system: str = "lighttrail",
        settings: ModelSettings | None = None,
        profile: ModelProfileSpec | None = None,
        recorder: Recorder | None = None,
    ) -> None:
        super().__init__(settings=settings, profile=profile or _DEFAULT_PROFILE)
        self._llm = provider
        self._recorder: Recorder = recorder or null_trace
        self._lt_model_name = model_name
        self._lt_system = system

    # ------ Model 端口 ------
    @property
    def model_name(self) -> str:
        """模型名（框架用于 RunResult/TraceReport 标识）。"""
        return self._lt_model_name

    @property
    def system(self) -> str:
        """供应商标识（OTel `gen_ai.system` 语义值）。"""
        return self._lt_system

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        """执行一次非流式请求：翻译消息与工具 → 端口调用 → 翻译回 ModelResponse。

        Args:
            messages: 框架维护的消息历史。
            model_settings: 本次请求的设置（temperature / extra_body …）。
            model_request_parameters: 本次请求的工具与输出模式声明。

        Returns:
            框架可消费的模型响应（parts + usage + finish_reason）。

        Raises:
            ModelAPIError: 供应商调用失败（经端口抛出的异常统一包装）。
        """
        settings, params = self.prepare_request(model_settings, model_request_parameters)
        settings = settings or {}
        payload = _map_messages(messages)
        tools = _map_tools(params.function_tools or [])
        reason = _reason_params(settings)
        temperature = settings.get("temperature")
        usage: dict[str, int] = {}

        def _on_usage(prompt_tokens: int, completion_tokens: int) -> None:
            usage["input"] = prompt_tokens
            usage["output"] = completion_tokens

        started = time.perf_counter()
        try:
            reply = await self._llm.complete(
                payload,
                model=self._lt_model_name,
                tools=tools or None,
                temperature=float(temperature) if temperature is not None else 0.2,
                thinking=reason.get("thinking"),
                reasoning_effort=reason.get("reasoning_effort"),
                usage_callback=_on_usage,
            )
        except Exception as exc:
            raise ModelAPIError(self._lt_model_name, f"{type(exc).__name__}: {exc}") from exc
        self._recorder.record_llm(
            self._lt_model_name,
            prompt_summary=f"消息数 {len(payload)}（pydantic-ai 桥）",
            duration_s=time.perf_counter() - started,
            tokens=(usage.get("input", 0) + usage.get("output", 0)) if usage else None,
            prompt_tokens=usage.get("input"),
            completion_tokens=usage.get("output"),
        )
        return _to_model_response(reply, model_name=self._lt_model_name, usage=usage, provider_name=self._lt_system)

    def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: Any = None,
    ) -> AsyncGenerator[StreamedResponse, None]:
        """流式通道暂未实现（PRD 附录 A2：token 流式延后）。

        Raises:
            NotImplementedError: 恒抛出；需要流式时在此按分片映射实现。
        """
        raise NotImplementedError("LightTrailModel 暂不支持流式（token 流式延后，见 PRD 附录 A2）")


def _reason_params(settings: ModelSettings) -> dict[str, Any]:
    """从 ModelSettings（含 extra_body）取平台扩展参数（thinking / reasoning_effort）。"""
    extra = dict(settings.get("extra_body") or {})
    reason = {key: extra.get(key) for key in _REASON_KEYS if extra.get(key) is not None}
    if settings.get("thinking") is not None:  # 新版 ModelSettings 的一等字段
        reason["thinking"] = settings.get("thinking")
    if extra:
        ignored = sorted(set(extra) - set(_REASON_KEYS))
        if ignored:
            logger.debug("桥忽略 extra_body 键：%s", ignored)
    return reason


def _map_tools(tools: Sequence[ToolDefinition]) -> list[dict[str, Any]]:
    """把 pydantic-ai 工具定义映射为 OpenAI function schema。"""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.parameters_json_schema or {"type": "object", "properties": {}},
            },
        }
        for tool in tools
    ]


def _stringify(content: Any) -> str:
    """把工具返回内容压成字符串（dict/list 走 JSON，其余 str）。"""
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str)


def _map_request_part(part: Any, out: list[dict[str, Any]], system_texts: list[str]) -> None:
    """映射一个 ModelRequest 分片到 OpenAI 消息（追加进 out / system_texts）。"""
    if isinstance(part, SystemPromptPart):
        if part.content and part.content not in system_texts:
            system_texts.append(part.content)
    elif isinstance(part, UserPromptPart):
        out.append({"role": "user", "content": _map_user_content(part.content)})
    elif isinstance(part, ToolReturnPart):
        out.append(
            {
                "role": "tool",
                "tool_call_id": part.tool_call_id,
                "content": _stringify(part.content),
            }
        )
    elif isinstance(part, RetryPromptPart):
        text = _stringify(part.content)
        if part.tool_name:
            out.append({"role": "tool", "tool_call_id": part.tool_call_id, "content": text})
        else:
            out.append({"role": "user", "content": text})
    else:
        logger.debug("桥跳过未知请求分片：%s", type(part).__name__)


def _map_user_content(content: Any) -> Any:
    """映射用户消息内容：纯文本直出；多模态列表转 OpenAI content 数组。"""
    if isinstance(content, str):
        return content
    items: list[dict[str, Any]] = []
    for item in content:
        if isinstance(item, str):
            items.append({"type": "text", "text": item})
        elif isinstance(item, ImageUrl):
            items.append({"type": "image_url", "image_url": {"url": item.url}})
        elif isinstance(item, BinaryImage):
            data = base64_data(item)
            items.append({"type": "image_url", "image_url": {"url": data}})
        else:
            items.append({"type": "text", "text": _stringify(item)})
    return items


def base64_data(image: BinaryImage) -> str:
    """把二进制图片转成 data URL（供多模态消息使用）。"""
    import base64

    encoded = base64.b64encode(image.data).decode("ascii")
    return f"data:{image.media_type};base64,{encoded}"


def _map_response(message: ModelResponse) -> dict[str, Any]:
    """映射一条 ModelResponse 到 assistant 消息（含 tool_calls）。"""
    content_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for part in message.parts:
        if isinstance(part, TextPart):
            content_parts.append(part.content)
        elif isinstance(part, ToolCallPart):
            tool_calls.append(
                {
                    "id": part.tool_call_id,
                    "type": "function",
                    "function": {"name": part.tool_name, "arguments": part.args_as_json_str()},
                }
            )
    payload: dict[str, Any] = {"role": "assistant", "content": "\n".join(content_parts)}
    if tool_calls:
        payload["tool_calls"] = tool_calls
    return payload


def _map_messages(messages: list[ModelMessage]) -> list[dict[str, Any]]:
    """把 pydantic-ai 消息历史映射为 OpenAI chat messages。

    `instructions` 与 `SystemPromptPart` 归并为前置 system 消息（静态前缀稳定 → 缓存命中）。

    Args:
        messages: 框架消息历史。

    Returns:
        OpenAI 格式消息列表。
    """
    body: list[dict[str, Any]] = []
    system_texts: list[str] = []
    for message in messages:
        if isinstance(message, ModelRequest):
            instructions = getattr(message, "instructions", None)
            if instructions and instructions not in system_texts:
                system_texts.append(instructions)
            for part in message.parts:
                _map_request_part(part, body, system_texts)
        elif isinstance(message, ModelResponse):
            body.append(_map_response(message))
    return [{"role": "system", "content": text} for text in system_texts] + body


def _to_model_response(
    reply: dict[str, Any],
    *,
    model_name: str,
    usage: dict[str, int],
    provider_name: str,
) -> ModelResponse:
    """把 provider 返回的消息字典翻译为框架的 ModelResponse。"""
    parts: list[ModelResponsePart] = []
    thinking = reply.get("thinking")
    if thinking:
        parts.append(ThinkingPart(str(thinking)))
    content = reply.get("content")
    tool_calls = reply.get("tool_calls") or []
    if content:
        parts.append(TextPart(str(content)))
    for call in tool_calls:
        function = call.get("function") or {}
        arguments = function.get("arguments") or "{}"
        parts.append(
            ToolCallPart(
                function.get("name", ""),
                arguments,
                tool_call_id=call.get("id") or "",
            )
        )
    return ModelResponse(
        parts=parts,
        usage=RequestUsage(input_tokens=usage.get("input", 0), output_tokens=usage.get("output", 0)),
        model_name=model_name,
        finish_reason="tool_call" if tool_calls else "stop",
        provider_name=provider_name,
    )


__all__ = ["LightTrailModel"]