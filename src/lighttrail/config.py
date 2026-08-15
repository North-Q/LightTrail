"""配置模块：从环境变量与 .env 文件加载运行配置。

优先级：进程环境变量 > 项目根目录 .env 文件 > 内置默认值。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# 项目根目录（src/lighttrail/config.py 向上三级）
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 学校开发者平台默认参数
DEFAULT_BASE_URL = "https://chat.ecnu.edu.cn/open/api/v1"
DEFAULT_MODEL = "ecnu-plus"      # 工具调用链路主模型（支持 thinking + 工具调用）
DEFAULT_MODEL_REASON = "ecnu-max"  # 强推理模型（1M 上下文，支持 thinking）


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
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    """运行时配置快照。"""

    api_key: str
    base_url: str
    model: str
    model_reason: str

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key) and not self.api_key.startswith("sk-your-")


def load_settings() -> Settings:
    """加载配置。API Key 缺失时返回占位值，由上层决定是否提示。"""
    _load_dotenv(PROJECT_ROOT / ".env")
    return Settings(
        api_key=os.getenv("ECNU_API_KEY", ""),
        base_url=os.getenv("ECNU_BASE_URL", DEFAULT_BASE_URL),
        model=os.getenv("ECNU_MODEL", DEFAULT_MODEL),
        model_reason=os.getenv("ECNU_MODEL_REASON", DEFAULT_MODEL_REASON),
    )
