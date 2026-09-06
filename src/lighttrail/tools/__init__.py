"""内置工具包：导入即完成向全局注册表的注册。

新增工具模块时，在此处加入 import 语句即可自动挂载。
"""

from lighttrail.tools import (
    astronomy,
    basic,
    exposure,
    memory_tool,
    site_match,
    weather,
)

__all__ = ["astronomy", "basic", "exposure", "memory_tool", "site_match", "weather"]
