"""全局知识门面：结构化相机规格与全文检索共用现有知识端口。"""

from __future__ import annotations

from lighttrail.contracts.knowledge import KnowledgeChunk
from lighttrail.domain.knowledge.catalog import CameraSpecsCatalog
from lighttrail.domain.knowledge.fts import FtsKnowledgeProvider


class GlobalKnowledgeProvider:
    """按访问方式委托只读知识，不合并或猜测未知规格。"""

    def __init__(self, catalog: CameraSpecsCatalog, search: FtsKnowledgeProvider) -> None:
        """保存装配根创建的规格表与检索器。"""
        self._catalog = catalog
        self._search = search

    def lookup(self, key: str, *, table: str = "") -> KnowledgeChunk | None:
        """按机型精确查规格，未命中返回 None。"""
        return self._catalog.lookup(key, table=table)

    def search(self, query: str, *, k: int = 3, scope: str = "") -> list[KnowledgeChunk]:
        """保留既有全文检索与来源。"""
        return self._search.search(query, k=k, scope=scope)

    def close(self) -> None:
        """释放本门面拥有的索引连接。"""
        self._search.close()
