"""pytest 共享 fixtures 与全局初始化。

导入内置工具模块以触发注册（basic / exposure / astronomy / weather / site_match），
确保所有测试用例在运行时 registry 已包含完整工具集。pythonpath 已在
pyproject.toml 中配置为 src/，无需手动 sys.path 注入。
"""

# 导入即注册：各工具模块分别在 tools/__init__.py 中统一导入
from lighttrail.tools import (  # noqa: F401
    astronomy,
    basic,
    exposure,
    memory_tool,
    photo_analysis,
    site_match,
    weather,
)
