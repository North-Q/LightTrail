"""配置模块：从环境变量与 .env 文件加载运行配置。

优先级：进程环境变量 > 项目根目录 .env 文件 > 内置默认值。

平台中立性：
- 环境变量采用通用前缀 `LLM_`（LLM_API_KEY / LLM_BASE_URL / LLM_MODEL /
  LLM_MODEL_REASON / LLM_SERIAL_LLM），不绑定特定供应商；
  兼容保留 ECNU_ 前缀别名（ECNU_API_KEY 等），两者同时存在时 LLM_ 优先；
- LLM 调用并发策略由 `LLM_SERIAL_LLM` 开关控制，默认开启以适配 ECNU
  平台「避免并行请求」的建议；接入支持并发的 API 时关闭即可，
  无需改动任何业务代码（缓存/重试/超时等策略与并发解耦）；
- 模型路由通过能力声明（needs_*）驱动，模型名全部可配置、能力矩阵
  可注入（见 llm/router.py），不绑定特定品牌。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# 项目根目录（src/lighttrail/config.py 向上三级）
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 学校开发者平台默认参数（默认值，均可被环境变量覆盖）
DEFAULT_BASE_URL = "https://chat.ecnu.edu.cn/open/api/v1"
DEFAULT_MODEL = "ecnu-plus"      # 工具调用链路主模型（支持 thinking + 工具调用）
DEFAULT_MODEL_REASON = "ecnu-max"  # 强推理模型（1M 上下文，支持 thinking）

# 并发策略默认值：默认串行以适配 ECNU「避免并行请求」建议；
# 接入支持并发的 API 时设 LLM_SERIAL_LLM=false 即可关闭。
DEFAULT_SERIAL_LLM = True

# 数据目录（记忆/会话数据本地存放处，已 gitignore）
DEFAULT_DATA_DIR = "data"


def _get_env(*keys: str, default: str = "") -> str:
    """按顺序取第一个非空环境变量（通用前缀优先，兼容别名兜底）。"""
    for key in keys:
        value = os.getenv(key)
        if value is not None and value != "":
            return value
    return default


def _load_dotenv(path: Path) -> None:
    """极简 .env 解析：仅支持 `KEY=VALUE` 行与 `#` 注释，不做变量展开。

    仅在对应环境变量未设置时写入 os.environ，避免覆盖进程环境变量。
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        if key not in os.environ:
            os.environ[key] = value


def _parse_bool(value: str, default: bool) -> bool:
    """宽松解析布尔环境变量。"""
    if value is None or value == "":
        return default
    return value.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """运行时配置快照。"""

    api_key: str
    base_url: str
    model: str
    model_reason: str
    serial_llm: bool
    data_dir: str
    quota_warn_threshold: float

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key) and not self.api_key.startswith("sk-your-")


def load_settings() -> Settings:
    """加载配置。API Key 缺失时返回占位值，由上层决定是否提示。

    环境变量命名：通用前缀 LLM_ 优先，兼容 ECNU_ 别名。
    """
    _load_dotenv(PROJECT_ROOT / ".env")
    return Settings(
        api_key=_get_env("LLM_API_KEY", "ECNU_API_KEY"),
        base_url=_get_env("LLM_BASE_URL", "ECNU_BASE_URL", default=DEFAULT_BASE_URL),
        model=_get_env("LLM_MODEL", "ECNU_MODEL", default=DEFAULT_MODEL),
        model_reason=_get_env("LLM_MODEL_REASON", "ECNU_MODEL_REASON", default=DEFAULT_MODEL_REASON),
        serial_llm=_parse_bool(
            os.getenv("LLM_SERIAL_LLM", os.getenv("ECNU_SERIAL_LLM", "")),
            DEFAULT_SERIAL_LLM,
        ),
        data_dir=os.getenv("LIGHTTRAIL_DATA_DIR", DEFAULT_DATA_DIR),
        quota_warn_threshold=float(os.getenv("LIGHTTRAIL_QUOTA_WARN_THRESHOLD", "0.9")),
    )
