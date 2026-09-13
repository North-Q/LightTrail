"""运行时层（runtime）：引擎骨架（工具注册表 / Agent runtime / 上下文组装）。

设计要点（v4 §2.1）：
- 依赖方向：`runtime → contracts`（不得 import domain / application / adapters / interface）；
  领域工具由装配根（composition.py）注入，运行时只认 ToolSpec 与 Tool 端口。
"""

from __future__ import annotations