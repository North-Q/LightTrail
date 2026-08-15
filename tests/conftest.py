"""pytest 共享 fixtures 与全局初始化。

导入内置工具模块以触发注册（basic / exposure），确保所有测试用例
在运行时 registry 已包含完整工具集。pythonpath 已在 pyproject.toml
中配置为 src/，无需手动 sys.path 注入。
"""

# 导入即注册：basic 注册 get_current_time，exposure 注册 equivalent_exposure
from lighttrail.tools import basic, exposure  # noqa: F401
