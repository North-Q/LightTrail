"""SQLite FTS5 中文知识检索（B6-2）。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from lighttrail.contracts.knowledge import KnowledgeChunk


class FtsKnowledgeProvider:
    """以 SQLite FTS5 trigram 检索全局只读领域知识。

    Args:
        db_path: FTS 索引数据库路径；可传 ``:memory:`` 供测试使用。
        seed_path: B5-2 迁出的全局领域知识种子。
    """

    def __init__(self, db_path: str | Path, seed_path: str | Path) -> None:
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._create_schema()
        self._load_seed(Path(seed_path))

    def _create_schema(self) -> None:
        """创建普通元数据表与中文 trigram FTS5 索引。"""
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS knowledge_meta (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                source TEXT NOT NULL,
                version TEXT NOT NULL,
                scope TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts
            USING fts5(id UNINDEXED, content, scope UNINDEXED, tokenize='trigram');
            """
        )

    def _load_seed(self, seed_path: Path) -> None:
        """幂等写入 JSON 种子，保持数据库仅作为可再生索引。"""
        raw = json.loads(seed_path.read_text(encoding="utf-8"))
        entries = raw.get("entries")
        if not isinstance(entries, list):
            raise TypeError("知识种子格式非法：缺少 entries 列表")
        version = str(raw.get("version", "unknown"))
        scope = str(raw.get("scope", "global-readonly"))
        self._conn.execute("DELETE FROM knowledge_fts")
        for item in entries:
            if not isinstance(item, dict):
                raise TypeError("知识种子格式非法：entry 必须是对象")
            entry_id = str(item["id"])
            content = str(item["content"])
            source = str(item["source"])
            self._conn.execute(
                "INSERT INTO knowledge_meta(id, content, source, version, scope) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET content=excluded.content, source=excluded.source, "
                "version=excluded.version, scope=excluded.scope",
                (entry_id, content, source, version, scope),
            )
            self._conn.execute(
                "INSERT INTO knowledge_fts(id, content, scope) VALUES (?, ?, ?)",
                (entry_id, content, scope),
            )
        self._conn.commit()

    def lookup(self, key: str, *, table: str = "") -> KnowledgeChunk | None:
        """B6-2 不提供结构化查表，调用方应组合 B6-1 Catalog。"""
        del key, table
        return None

    def search(self, query: str, *, k: int = 3, scope: str = "") -> list[KnowledgeChunk]:
        """按 BM25 排序检索中文子串。

        Args:
            query: FTS5 查询文本。
            k: 返回上限，必须为正数。
            scope: 全局只读知识范围；空串表示不筛选。

        Returns:
            按 BM25 排序的知识条目。
        """
        if not query.strip() or k <= 0:
            return []
        conditions = ["knowledge_fts MATCH ?"]
        params: list[object] = [query.strip()]
        if scope:
            conditions.append("knowledge_fts.scope = ?")
            params.append(scope)
        params.append(k)
        try:
            rows = self._conn.execute(
                "SELECT meta.id, meta.content, meta.source, meta.version "
                "FROM knowledge_fts JOIN knowledge_meta AS meta ON meta.id = knowledge_fts.id "
                f"WHERE {' AND '.join(conditions)} ORDER BY bm25(knowledge_fts) LIMIT ?",
                params,
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        # SQLite trigram 仅索引至少三个字符的片段；中文短词（如“散射”）不能 MATCH，
        # 以受限 LIKE 兜底，仍保持较长查询走 FTS/BM25 主路径。
        if not rows and len(query.strip()) < 3:
            where = "scope = ? AND content LIKE ?" if scope else "content LIKE ?"
            fallback_params: list[object] = [scope, f"%{query.strip()}%"] if scope else [f"%{query.strip()}%"]
            fallback_params.append(k)
            rows = self._conn.execute(
                f"SELECT id, content, source, version FROM knowledge_meta WHERE {where} LIMIT ?",
                fallback_params,
            ).fetchall()
        return [
            KnowledgeChunk(id=row["id"], text=row["content"], source=row["source"], version=row["version"])
            for row in rows
        ]
