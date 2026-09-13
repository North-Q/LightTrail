"""配置模块：pydantic-settings 驱动的运行配置（B1-5 重写，D5 / D6 / D7）。

优先级：请求级覆盖（per-user，经 UserConfigProvider）> 进程环境变量 > 项目根 .env > 内置默认。

平台中立性（ADR-002）：
- 环境变量采用通用前缀 `LLM_`（LLM_API_KEY / LLM_BASE_URL / LLM_MODEL / LLM_MODEL_REASON /
  LLM_CONCURRENCY / LLM_TIMEOUT），不绑定特定供应商；兼容保留 ECNU_ 前缀别名，
  两者同时存在时 LLM_ 优先（AliasChoices 原生表达，不再手写解析）；
- 模型名默认值（ecnu-plus / ecnu-max）只是**可覆盖的部署默认值**：模型选择一律走
  ModelRouter 能力声明，业务代码不写品牌判断；
- 并发是**纯配置**（LLM_CONCURRENCY，默认 4，见 D5）：`LLM_SERIAL_LLM` 仅作只读兼容
  别名过渡一版（=1 时等价串行），B3 批次删除；不要在新代码里写「默认串行适配平台」的逻辑；
- 类型校验前置：非法布尔/数字/超时格式直接抛 ValidationError（不再静默取默认值）。

统一护栏（D5，全部可配置并在 trace 中可见）：
- LLM_CONCURRENCY：在途 LLM 请求上限（默认 4）
- REACT_MAX_ROUNDS：ReAct 单轮工具调用轮数上限（默认 12）
- PIPELINE_MAX_STEPS：单条管线步骤上限（默认 12）
- PLAN_MAX_STEPS：受控规划通道步数上限（默认 8）
- LLM_TIMEOUT：连接/读取超时（默认 30,120）
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from pydantic import AliasChoices, Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from lighttrail.contracts.llm import DEFAULT_CONCURRENCY
from lighttrail.contracts.plan import DEFAULT_PLAN_MAX_STEPS

# 项目根目录（src/lighttrail/config.py 向上三级）
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 学校开发者平台默认参数（默认值，均可被环境变量覆盖）
DEFAULT_BASE_URL = "https://chat.ecnu.edu.cn/open/api/v1"
DEFAULT_MODEL = "ecnu-plus"      # 工具调用链路主模型（支持 thinking + 工具调用）
DEFAULT_MODEL_REASON = "ecnu-max"  # 强推理模型（1M 上下文，支持 thinking）

# 数据目录（记忆/会话数据本地存放处，已 gitignore）
DEFAULT_DATA_DIR = "data"

# 护栏默认值（见模块 docstring；PLAN_MAX_STEPS 与契约层同源）
DEFAULT_REACT_MAX_ROUNDS = 12
DEFAULT_PIPELINE_MAX_STEPS = 12
DEFAULT_LLM_TIMEOUT: tuple[float, float] = (30.0, 120.0)

# 资源上限与配额（QuotaLedger 默认持有）
DEFAULT_QUOTA_WARN_THRESHOLD = 0.9


class Settings(BaseSettings):
    """运行时配置快照（pydantic-settings；字段名即代码 API，环境变量名见 alias）。

    Attributes:
        api_key: LLM API Key（LLM_API_KEY / ECNU_API_KEY）。
        base_url: OpenAI 兼容接口地址（LLM_BASE_URL / ECNU_BASE_URL）。
        model: 工具调用链路主模型（LLM_MODEL / ECNU_MODEL）。
        model_reason: 深推理模型（LLM_MODEL_REASON / ECNU_MODEL_REASON）。
        concurrency: 在途 LLM 请求上限（LLM_CONCURRENCY，默认 4）。
        legacy_serial_llm: 只读兼容别名（LLM_SERIAL_LLM；True → concurrency=1，B3 删除）。
        reason_thinking: 深推理通道是否携带 thinking 扩展参数（LLM_REASON_THINKING）。
        data_dir: 数据目录（LIGHTTRAIL_DATA_DIR）。
        quota_warn_threshold: 配额告警水位（LIGHTTRAIL_QUOTA_WARN_THRESHOLD，0-1）。
        react_max_rounds: ReAct 轮数上限（REACT_MAX_ROUNDS）。
        pipeline_max_steps: 单条管线步骤上限（PIPELINE_MAX_STEPS）。
        plan_max_steps: 受控规划步数上限（PLAN_MAX_STEPS）。
        llm_timeout: (连接超时, 读取超时) 秒（LLM_TIMEOUT，支持 "30,120" 写法）。
    """

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
        protected_namespaces=(),  # 允许 model / model_reason 字段名
        frozen=True,
    )

    # ------ LLM 配置 ------
    api_key: str = Field(default="", validation_alias=AliasChoices("LLM_API_KEY", "ECNU_API_KEY"))
    base_url: str = Field(
        default=DEFAULT_BASE_URL,
        validation_alias=AliasChoices("LLM_BASE_URL", "ECNU_BASE_URL"),
    )
    model: str = Field(
        default=DEFAULT_MODEL,
        validation_alias=AliasChoices("LLM_MODEL", "ECNU_MODEL"),
    )
    model_reason: str = Field(
        default=DEFAULT_MODEL_REASON,
        validation_alias=AliasChoices("LLM_MODEL_REASON", "ECNU_MODEL_REASON"),
    )
    # 声明顺序有意义：legacy_serial_llm 必须先于 concurrency（后者用它做兼容映射）
    legacy_serial_llm: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("LLM_SERIAL_LLM", "ECNU_SERIAL_LLM"),
    )
    concurrency: int = Field(default=DEFAULT_CONCURRENCY, ge=1, validation_alias="LLM_CONCURRENCY")
    reason_thinking: bool = Field(default=True, validation_alias="LLM_REASON_THINKING")

    # ------ 应用配置 ------
    data_dir: str = Field(default=DEFAULT_DATA_DIR, validation_alias="LIGHTTRAIL_DATA_DIR")
    quota_warn_threshold: float = Field(
        default=DEFAULT_QUOTA_WARN_THRESHOLD,
        ge=0.0,
        le=1.0,
        validation_alias="LIGHTTRAIL_QUOTA_WARN_THRESHOLD",
    )

    # ------ 统一护栏（D5）------
    react_max_rounds: int = Field(
        default=DEFAULT_REACT_MAX_ROUNDS, ge=1, validation_alias="REACT_MAX_ROUNDS"
    )
    pipeline_max_steps: int = Field(
        default=DEFAULT_PIPELINE_MAX_STEPS, ge=1, validation_alias="PIPELINE_MAX_STEPS"
    )
    plan_max_steps: int = Field(
        default=DEFAULT_PLAN_MAX_STEPS, ge=1, validation_alias="PLAN_MAX_STEPS"
    )
    llm_timeout: Annotated[tuple[float, float], NoDecode] = DEFAULT_LLM_TIMEOUT

    # ------ 派生属性 ------
    @property
    def has_api_key(self) -> bool:
        """是否配置了可用 API Key（占位值视为未配置）。"""
        return bool(self.api_key) and not self.api_key.startswith("sk-your-")

    @property
    def serial_llm(self) -> bool:
        """只读兼容属性（B3 批次删除）：concurrency == 1 等价旧「串行」语义。"""
        return self.concurrency == 1

    # ------ 校验 ------
    @field_validator("llm_timeout", mode="before")
    @classmethod
    def _parse_timeout(cls, value: Any) -> Any:
        """把 "30,120" / "[30, 120]" / "30 120" 统一解析为 (连接超时, 读取超时)。"""
        if isinstance(value, str):
            parts = [part for part in value.strip().strip("[]()").replace(" ", ",").split(",") if part]
            return tuple(float(part) for part in parts)
        return value

    @field_validator("concurrency", mode="after")
    @classmethod
    def _apply_serial_llm_alias(cls, value: int, info: ValidationInfo) -> int:
        """兼容映射：LLM_SERIAL_LLM=true → concurrency=1（B3 批次随旧开关一并删除）。"""
        if info.data.get("legacy_serial_llm"):
            return 1
        return value


def load_settings() -> Settings:
    """加载配置快照（.env + 环境变量 + 内置默认；非法值直接抛 ValidationError）。

    Returns:
        校验通过的配置快照。
    """
    return Settings()