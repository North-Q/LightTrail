"""知识库端口契约（B1-4，D10 / D14 / §2.3）。

设计要点：
- 知识库 = 「我知道世界」：**全局只读**，检索**不接 user_id**（D10/D14 判定规则）——
  与 per-user 可写的记忆（memory.py）是两条独立链路；
- 两种形态共用本端口：① 结构化判据表（键值/规则，精确查表，`lookup`）；
  ② 文本检索（SQLite FTS5 中文 trigram/jieba，`search`）；向量检索为后续升级项；
- `KnowledgeChunk` 带 `source`（出处）与 `version`：知识必须可溯源、可版本化
  （B6 的相机规格表即按此落地）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class KnowledgeChunk:
    """一条知识条目（可溯源、带版本）。

    Attributes:
        id: 条目标识（结构化表内为键名，文本检索为文档 ID）。
        text: 知识正文（判据说明 / 规格文本）。
        source: 出处（标准 / 论文 / 实测经验），供溯源展示。
        version: 版本（如 "2026-09"），供漂移排查与更新。
    """

    id: str
    text: str
    source: str
    version: str


@runtime_checkable
class KnowledgeProvider(Protocol):
    """知识库端口：全局只读，检索不接 user_id（D10 判定规则）。"""

    def lookup(self, key: str, *, table: str = "") -> KnowledgeChunk | None:
        """按键精确查结构化判据表。

        Args:
            key: 键名（如机型名 / 判据名）。
            table: 表名（空串表示默认表）。

        Returns:
            命中条目；未命中返回 None（调用方不得编造缺省知识）。
        """
        ...

    def search(self, query: str, *, k: int = 3, scope: str = "") -> list[KnowledgeChunk]:
        """文本检索知识库（FTS5 中文检索，B6 落地）。

        Args:
            query: 查询文本。
            k: 返回条数上限。
            scope: 检索范围（如机型 / 判据域；空串表示全库）。

        Returns:
            命中条目列表（按相关度排序，可能为空）。
        """
        ...


__all__ = ["KnowledgeChunk", "KnowledgeProvider"]