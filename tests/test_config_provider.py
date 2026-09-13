"""部署级 LLM 配置与密钥实现的 pytest 用例（B1-3 / D7 / §7.2）。

验证：
- DeploymentConfigProvider.resolve：部署级配置字段映射 + 优先级链（请求级覆盖 > 部署级）；
- LLMConfig.__repr__ 掩码：不泄漏完整 Key（只露后 4 位）；
- EnvKeyVault：本地用户取部署级 Key、其他用户 None、空 Key 归一为 None、写入直接拒绝；
- 两个实现都满足契约层 Protocol（runtime_checkable 结构化校验）；
- 契约默认值：concurrency=4（D5 护栏）、reason_thinking=True。
"""

from __future__ import annotations

import dataclasses

import pytest

from lighttrail.adapters.llm.config_provider import (
    DeploymentConfigProvider,
    EnvKeyVault,
    KeyVaultError,
)
from lighttrail.contracts.context import RequestContext
from lighttrail.contracts.llm import KeyVault, LLMConfig, UserConfigProvider


@dataclasses.dataclass(frozen=True)
class _StubSettings:
    """部署级配置替身（字段与 config.Settings 的 LLM 子集一致）。"""

    api_key: str = "sk-deploy-abcdef1234"
    base_url: str = "https://llm.example.invalid/v1"
    model: str = "model-tools"
    model_reason: str = "model-reason"
    concurrency: int = 4
    reason_thinking: bool = True


@pytest.fixture()
def settings() -> _StubSettings:
    """默认部署级配置替身。"""
    return _StubSettings()


def test_resolve_returns_deployment_config(settings: _StubSettings) -> None:
    """无请求级覆盖时返回部署级配置（字段一一对应，concurrency 默认 4）。"""
    config = DeploymentConfigProvider(settings).resolve(RequestContext())
    assert config.api_key == settings.api_key
    assert config.base_url == settings.base_url
    assert config.model == settings.model
    assert config.model_reason == settings.model_reason
    assert config.concurrency == 4
    assert config.reason_thinking is True


def test_resolve_request_override_wins(settings: _StubSettings) -> None:
    """优先级链：请求级 llm_overrides 整块覆盖部署级（per-user Key 的落点）。"""
    override = LLMConfig(
        api_key="sk-user-9999",
        base_url="https://user.example.invalid/v1",
        model="user-model",
        model_reason="user-reason",
        concurrency=1,
        reason_thinking=False,
    )
    config = DeploymentConfigProvider(settings).resolve(RequestContext(llm_overrides=override))
    assert config is override
    assert config.api_key == "sk-user-9999"
    assert config.concurrency == 1


def test_llm_config_repr_masks_key() -> None:
    """repr 掩码：完整 Key 不出现，只露后 4 位（防日志/异常链路泄漏）。"""
    config = LLMConfig(
        api_key="sk-secret-abcdef1234",
        base_url="https://llm.example.invalid/v1",
        model="m",
        model_reason="r",
    )
    text = repr(config)
    assert "sk-secret-abcdef1234" not in text
    assert "1234" in text
    assert text.startswith("LLMConfig(api_key=")


def test_llm_config_repr_short_key_fully_masked() -> None:
    """短 Key（<=4 字符）整体掩码，不泄漏任何字符。"""
    config = LLMConfig(api_key="abc", base_url="u", model="m", model_reason="r")
    assert "abc" not in repr(config)


def test_env_key_vault_local_and_other_user(settings: _StubSettings) -> None:
    """EnvKeyVault：本地用户取部署级 Key；其他用户 None（回落部署级配置）。"""
    vault = EnvKeyVault(settings)
    assert vault.get_api_key("_local") == settings.api_key
    assert vault.get_api_key("") == settings.api_key
    assert vault.get_api_key("someone-else") is None


def test_env_key_vault_empty_key_is_none() -> None:
    """部署级 Key 为空时返回 None（不返回空串，避免调用方拿到假 Key）。"""
    vault = EnvKeyVault(_StubSettings(api_key=""))
    assert vault.get_api_key("_local") is None


def test_env_key_vault_write_rejected(settings: _StubSettings) -> None:
    """部署级 KeyVault 只读：写入直接抛 KeyVaultError（不静默失败）。"""
    with pytest.raises(KeyVaultError):
        EnvKeyVault(settings).set_api_key("_local", "sk-new-0000")


def test_implementations_satisfy_contracts(settings: _StubSettings) -> None:
    """两个实现都满足契约层 Protocol（结构化：方法齐全即成立）。"""
    assert isinstance(DeploymentConfigProvider(settings), UserConfigProvider)
    assert isinstance(EnvKeyVault(settings), KeyVault)