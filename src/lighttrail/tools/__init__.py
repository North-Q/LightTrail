"""内置工具包：导入即完成向全局注册表的注册。

新增工具模块时，在此处加入 import 语句即可自动挂载。
"""

from lighttrail.tools import basic  # noqa: F401  (注册 get_current_time)
from lighttrail.tools import exposure  # noqa: F401  (注册 equivalent_exposure)

__all__ = ["basic", "exposure"]
