"""LLM 适配层：把契约端口接到具体 SDK / 平台（B1 起配置解析，B3 起 LLMProvider 实现）。

设计要点：
- 目录名与顶层 `lighttrail.llm`（迁移期旧客户端）区分：本包属适配层（L4），
  只依赖 contracts；旧 `lighttrail.llm` 在 B3 被本包接替后删除；
- 品牌字面量与平台扩展参数（thinking/reasoning_effort 的 extra_body 适配）集中在本包。
"""

from __future__ import annotations