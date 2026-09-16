"""相机规格表加载器（B6-1）。"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from lighttrail.contracts.knowledge import KnowledgeChunk

_DEFAULT_TABLE = "camera_specs"


class CameraSpecsCatalog:
    """从版本化 YAML 文件提供相机规格精确查表。

    Args:
        path: ``camera_specs.yaml`` 路径。
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        raw = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("cameras"), dict):
            raise TypeError("相机规格表格式非法：缺少 cameras 映射")
        self._version = str(raw.get("version", "unknown"))
        self._cameras: dict[str, dict[str, object]] = raw["cameras"]

    def lookup(self, key: str, *, table: str = "") -> KnowledgeChunk | None:
        """按机型名精确查规格。

        Args:
            key: 机型名，例如 ``S5M2``。
            table: 表名；只支持 ``camera_specs`` 或空串。

        Returns:
            带出处和版本的知识条目；未命中返回 None。
        """
        if table not in ("", _DEFAULT_TABLE):
            return None
        item = self._cameras.get(key)
        if item is None:
            return None
        source = str(item["source"])
        text = json.dumps(item, ensure_ascii=False, sort_keys=True)
        return KnowledgeChunk(id=key, text=text, source=source, version=self._version)

    def search(self, query: str, *, k: int = 3, scope: str = "") -> list[KnowledgeChunk]:
        """B6-1 不提供模糊检索；由 B6-2 的 FTS5 实现替换。"""
        del query, k, scope
        return []
