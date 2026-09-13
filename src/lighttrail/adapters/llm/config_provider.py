"""部署级 LLM 配置与密钥实现（B1-3：UserConfigProvider / KeyVault 的本期实现）。

设计要点（D6 / D7 / §7.2）：
- `DeploymentConfigProvider` 是配置解析的**单点**：把部署级 Settings 解析成 LLMConfig，
  并实现优先级链 `请求级覆盖 > 部署级配置`。业务层（管线/Agent/智能工具）只拿
  `resolve()` 结果，不读环境变量——per-user Key 未来零侵入的前提；
- `EnvKeyVault` 是部署级密钥保管：只读（部署级不支持按用户写 Key），写入直接抛
  `KeyVaultError`，避免出现「看起来能存但其实没存」的静默失败；
- 平台中立（ADR-002）：本模块不解析环境变量、不含品牌字面量，Settings 由装配根注入；
  Key 掩码由 LLMConfig.__repr__ 保证。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lighttrail.contracts.context import LOCAL_USER_ID
from lighttrail.contracts.llm import LLMConfig

if TYPE_CHECKING:
    from lighttrail.config import Settings
    from lighttrail.contracts.context import RequestContext


class KeyVaultError(Exception):
    """密钥保管操作失败（如部署级 KeyVault 不支持写入）。"""


class DeploymentConfigProvider:
    """部署级配置解析器：唯一把 Settings + 请求级覆盖解析成 LLMConfig 的地方。

    Args:
        settings: 部署级配置快照（由装配根构造，来自 .env / 环境变量 / 内置默认）。
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def resolve(self, ctx: RequestContext) -> LLMConfig:
        """解析本次请求的 LLM 配置（优先级链：请求级 > 部署级 > 内置默认）。

        Args:
            ctx: 请求上下文；`llm_overrides` 非空时整块覆盖部署级配置（per-user Key 落点）。

        Returns:
            本次请求生效的 LLM 配置。
        """
        deployment = LLMConfig(
            api_key=self._settings.api_key,
            base_url=self._settings.base_url,
            model=self._settings.model,
            model_reason=self._settings.model_reason,
            concurrency=self._settings.concurrency,
            reason_thinking=self._settings.reason_thinking,
        )
        if ctx.llm_overrides is not None:
            return ctx.llm_overrides
        return deployment


class EnvKeyVault:
    """部署级密钥保管（只读）：从部署级配置取 Key，不支持按用户写入。

    Args:
        settings: 部署级配置快照（提供平台/部署级 Key）。
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def get_api_key(self, user_id: str) -> str | None:
        """取密钥：本地部署用户返回部署级 Key，其他用户返回 None（回落部署级）。

        Args:
            user_id: 用户标识。

        Returns:
            部署级 API Key；非本地用户返回 None（本期不保存 per-user Key）。
        """
        if user_id in ("", LOCAL_USER_ID):
            return self._settings.api_key or None
        return None

    def set_api_key(self, user_id: str, key: str) -> None:
        """写入密钥：部署级实现不支持（per-user Key 属未来 EncryptedStoreKeyVault）。

        Args:
            user_id: 用户标识。
            key: 待写入的密钥。

        Raises:
            KeyVaultError: 恒定抛出（不静默失败）。
        """
        raise KeyVaultError("部署级 KeyVault 只读：per-user Key 需 EncryptedStoreKeyVault（未实现）")


__all__ = ["DeploymentConfigProvider", "EnvKeyVault", "KeyVaultError"]