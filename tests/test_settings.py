"""配置层（pydantic-settings）的 pytest 用例（B1-5 / D5 / D6 / D7）。

验证：
- 默认值：并发 4、护栏 12/12/8、超时 (30,120)、既有字段（数据目录/配额水位/模型名）；
- 别名解析：LLM_* 优先于 ECNU_*（AliasChoices）；
- LLM_CONCURRENCY 与护栏项可覆盖；LLM_SERIAL_LLM=true → concurrency=1 兼容映射；
- LLM_TIMEOUT 支持 "30,120" 与 "[45, 90]" 两种写法；
- 非法值直接抛 ValidationError（不再静默取默认）；frozen 快照不可变。

用例统一用 `_env_file=None` 关闭 .env 读取 + monkeypatch 清理环境变量，
保证断言与开发者本机 .env 无关。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lighttrail.config import Settings

_ENV_KEYS = (
    "LLM_API_KEY",
    "ECNU_API_KEY",
    "LLM_BASE_URL",
    "ECNU_BASE_URL",
    "LLM_MODEL",
    "ECNU_MODEL",
    "LLM_MODEL_REASON",
    "ECNU_MODEL_REASON",
    "LLM_CONCURRENCY",
    "LLM_SERIAL_LLM",
    "ECNU_SERIAL_LLM",
    "LLM_REASON_THINKING",
    "LLM_TIMEOUT",
    "REACT_MAX_ROUNDS",
    "PIPELINE_MAX_STEPS",
    "PLAN_MAX_STEPS",
    "LIGHTTRAIL_DATA_DIR",
    "LIGHTTRAIL_QUOTA_WARN_THRESHOLD",
)


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """清理相关环境变量，保证断言不受本机环境影响。"""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _load() -> Settings:
    """构造配置快照（关闭 .env 读取，只看进程环境变量）。"""
    return Settings(_env_file=None)


def test_defaults(clean_env: None) -> None:
    """默认值：并发 4、护栏 12/12/8、超时 (30,120)、空 Key 视为未配置。"""
    settings = _load()
    assert settings.concurrency == 4
    assert settings.serial_llm is False  # 默认并发（非串行），B3 删除该属性
    assert settings.react_max_rounds == 12
    assert settings.pipeline_max_steps == 12
    assert settings.plan_max_steps == 8
    assert settings.llm_timeout == (30.0, 120.0)
    assert settings.data_dir == "data"
    assert settings.quota_warn_threshold == pytest.approx(0.9)
    assert settings.reason_thinking is True
    assert settings.api_key == ""
    assert settings.has_api_key is False
    assert settings.base_url.startswith("https://")
    assert settings.model and settings.model_reason


def test_llm_prefix_preferred_over_ecnu_alias(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """别名解析：ECNU_* 可用；LLM_* 与 ECNU_* 同时存在时 LLM_* 优先。"""
    monkeypatch.setenv("ECNU_API_KEY", "sk-ecnu")
    monkeypatch.setenv("ECNU_MODEL", "ecnu-model-x")
    assert _load().api_key == "sk-ecnu"
    assert _load().model == "ecnu-model-x"

    monkeypatch.setenv("LLM_API_KEY", "sk-generic")
    monkeypatch.setenv("LLM_MODEL", "generic-model")
    assert _load().api_key == "sk-generic"
    assert _load().model == "generic-model"


def test_concurrency_override(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM_CONCURRENCY 可覆盖（纯配置，无平台语义）。"""
    monkeypatch.setenv("LLM_CONCURRENCY", "8")
    assert _load().concurrency == 8
    assert _load().serial_llm is False


def test_legacy_serial_llm_maps_to_concurrency_one(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """兼容映射：LLM_SERIAL_LLM=true → concurrency=1（B3 随旧开关一并删除）。"""
    monkeypatch.setenv("LLM_CONCURRENCY", "8")
    monkeypatch.setenv("LLM_SERIAL_LLM", "true")
    settings = _load()
    assert settings.concurrency == 1
    assert settings.serial_llm is True


def test_guardrail_defaults_and_overrides(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """护栏项（D5）默认 12/12/8，且全部可配置。"""
    monkeypatch.setenv("REACT_MAX_ROUNDS", "6")
    monkeypatch.setenv("PIPELINE_MAX_STEPS", "5")
    monkeypatch.setenv("PLAN_MAX_STEPS", "3")
    settings = _load()
    assert (settings.react_max_rounds, settings.pipeline_max_steps, settings.plan_max_steps) == (6, 5, 3)


def test_timeout_parsing(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """超时支持 "30,120" 与 "[45, 90]" 两种写法（连接/读取）。"""
    monkeypatch.setenv("LLM_TIMEOUT", "10,20")
    assert _load().llm_timeout == (10.0, 20.0)
    monkeypatch.setenv("LLM_TIMEOUT", "[45, 90]")
    assert _load().llm_timeout == (45.0, 90.0)


def test_invalid_values_raise(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """非法值直接报错（D6：不再静默取默认值）。"""
    monkeypatch.setenv("LLM_CONCURRENCY", "abc")
    with pytest.raises(ValidationError):
        _load()
    monkeypatch.delenv("LLM_CONCURRENCY")
    monkeypatch.setenv("LLM_SERIAL_LLM", "maybe")
    with pytest.raises(ValidationError):
        _load()
    monkeypatch.delenv("LLM_SERIAL_LLM")
    monkeypatch.setenv("LLM_CONCURRENCY", "0")
    with pytest.raises(ValidationError):
        _load()


def test_quota_threshold_bounds(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """配额水位限定 0-1，越界报错（护栏前置）。"""
    monkeypatch.setenv("LIGHTTRAIL_QUOTA_WARN_THRESHOLD", "1.5")
    with pytest.raises(ValidationError):
        _load()


def test_has_api_key_rejects_placeholder(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """占位 Key（sk-your-...）视为未配置，真实 Key 视为已配置。"""
    monkeypatch.setenv("LLM_API_KEY", "sk-your-token-here")
    assert _load().has_api_key is False
    monkeypatch.setenv("LLM_API_KEY", "sk-real-token")
    assert _load().has_api_key is True


def test_settings_snapshot_is_frozen(clean_env: None) -> None:
    """配置快照 frozen：运行期不可就地修改。"""
    settings = _load()
    with pytest.raises(ValidationError):
        settings.concurrency = 2  # type: ignore[misc]