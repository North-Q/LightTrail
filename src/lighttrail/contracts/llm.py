"""LLM 端口与配置契约（B1-3，D6 / D7 / §2.3 / §7）。

设计要点：
- `LLMConfig` 是 LLM 调用的**唯一配置形态**：模型名、并发、超时之外不含任何平台语义；
  调用方只拿 resolve 结果，永不直读环境变量（per-user Key 未来零侵入的前提，ADR-002 延伸）；
- `UserConfigProvider` 是优先级链的唯一入口（请求级 > 部署级 > 内置默认，§7.2）：
  本期只有 DeploymentConfigProvider 一个实现，未来新增实现即支持 per-user Key；
- `KeyVault` 是密钥保管抽象（本期 EnvKeyVault；未来 EncryptedStoreKeyVault）；
- 品牌字面量红线（ADR-002）：本模块**不含**任何供应商 URL/模型名，默认值只允许出现在
  adapters/llm 与 settings 默认值处；
- 密钥安全：`LLMConfig.__repr__` 对 api_key 掩码（只露后 4 位），日志/异常打印不泄漏 Key。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from lighttrail.contracts.context import RequestContext

# 并发默认值（D5 护栏：LLM_CONCURRENCY 默认 4；settings 侧同名常量与之对齐）
DEFAULT_CONCURRENCY = 4


@dataclass(frozen=True, repr=False)
class LLMConfig:
    """一次 LLM 调用的完整配置（部署级解析结果或请求级覆盖）。

    Attributes:
        api_key: 调用密钥（repr 掩码，只露后 4 位）。
        base_url: OpenAI 兼容接口地址。
        model: 工具调用链路主模型。
        model_reason: 深推理模型。
        concurrency: 在途 LLM 请求上限（纯配置，无平台语义，见 D5）。
        reason_thinking: 深推理通道是否携带 thinking 扩展参数。
    """

    api_key: str
    base_url: str
    model: str
    model_reason: str
    concurrency: int = DEFAULT_CONCURRENCY
    reason_thinking: bool = True

    def __repr__(self) -> str:
        """掩码 repr：api_key 只露后 4 位（防日志/异常链路泄漏密钥）。"""
        masked = f"***{self.api_key[-4:]}" if len(self.api_key) > 4 else "***"
        return (
            f"LLMConfig(api_key={masked!r}, base_url={self.base_url!r}, model={self.model!r}, "
            f"model_reason={self.model_reason!r}, concurrency={self.concurrency}, "
            f"reason_thinking={self.reason_thinking})"
        )


@runtime_checkable
class LLMProvider(Protocol):
    """LLM 端口：唯一被业务层依赖的 LLM 能力面（实现见 adapters/llm，B3 起 async-first）。"""

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        thinking: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        """发起一次对话补全。

        Args:
            messages: OpenAI 格式消息列表。
            model: 模型名覆盖；None 时由实现按默认模型解析。
            tools: OpenAI 格式工具定义；None 表示不带工具。
            temperature: 采样温度。
            thinking: 思考模式扩展参数（None 不携带）。
            reasoning_effort: 推理强度（None 不携带）。

        Returns:
            助手消息字典（role/content，可能含 tool_calls / thinking 摘要）。
        """
        ...

    def queue_position(self) -> int:
        """返回当前在途 + 排队的 LLM 请求数（0 = 空闲；供 SSE queued 事件）。"""
        ...


@runtime_checkable
class UserConfigProvider(Protocol):
    """LLM 配置优先级链的唯一解析入口（§7.2）。

    实现约定优先级：`ctx.llm_overrides`（请求级）> 部署级（.env / 默认值）。
    """

    def resolve(self, ctx: RequestContext) -> LLMConfig:
        """解析本次请求应使用的 LLM 配置。

        Args:
            ctx: 请求上下文（含 user_id 与请求级覆盖）。

        Returns:
            本次请求的 LLM 配置。
        """
        ...


@runtime_checkable
class KeyVault(Protocol):
    """密钥保管抽象（本期部署级 EnvKeyVault；未来 per-user 加密落盘）。"""

    def get_api_key(self, user_id: str) -> str | None:
        """取某用户的密钥（无则 None，调用方回落到部署级配置）。"""
        ...

    def set_api_key(self, user_id: str, key: str) -> None:
        """写入某用户的密钥（部署级实现不支持写入，直接拒绝）。"""
        ...


__all__ = [
    "DEFAULT_CONCURRENCY",
    "KeyVault",
    "LLMConfig",
    "LLMProvider",
    "UserConfigProvider",
]