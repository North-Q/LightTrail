"""全局只读知识库的结构化查表实现。"""

from __future__ import annotations

from lighttrail.domain.knowledge.catalog import CameraSpecsCatalog
from lighttrail.domain.knowledge.fts import FtsKnowledgeProvider
from lighttrail.domain.knowledge.provider import GlobalKnowledgeProvider

__all__ = ["CameraSpecsCatalog", "FtsKnowledgeProvider", "GlobalKnowledgeProvider"]
