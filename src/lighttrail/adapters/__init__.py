"""适配层：契约端口的具体实现（LLM / 数据源 / 存储 / 观测）。

设计要点：
- 只依赖 contracts（实现 Protocol），不得被 domain / application / runtime 反向依赖；
- 品牌字面量与平台扩展参数适配集中在本层（ADR-002 红线）：业务层不写平台判断。
"""

from __future__ import annotations