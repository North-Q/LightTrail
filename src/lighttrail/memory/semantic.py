"""语义记忆（M1.3）：结论型经验的精选注入，写入需 double-confirm。

设计要点（架构 v2.0 §2.5，E3-3 落地）：
- 语义记忆是「结论」不是「数据」：如「偏好低云量+高云为主的晚霞，火烧云成功率约 7 成」，
  token 效率最高，但写入必须人工/规则双重确认，防污染；
- MVP：手动维护 data/semantic.json + 关键词规则命中注入（E6-3 再做事件自动提炼）；
- 写入模型：staged_add 入「候选池」→ confirm/reject 显式裁决，未确认不计入命中；
- 注入策略：命中规则时最多注入 1-2 条到档案段旁（第④层），不常驻全量。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_SEMANTIC_FILE = "semantic.json"
# 单次注入最多条数
_MAX_INJECT = 2


@dataclass(frozen=True)
class SemanticEntry:
    """一条已确认的语义记忆。

    Attributes:
        content: 结论型经验文本（≤1 句，可直接注入）。
        keywords: 命中关键词（任一出现在意图中即触发注入）。
    """

    content: str
    keywords: tuple[str, ...] = ()


class SemanticStore:
    """语义记忆存储（JSON 起步）：关键词规则命中注入 + double-confirm 写入。"""

    def __init__(self, path: str | Path | None = None) -> None:
        """初始化存储。

        Args:
            path: semantic.json 路径；None 表示内存态（不落盘）。
        """
        self._path = Path(path) if path is not None else None
        self._entries: list[SemanticEntry] = []
        self._pending: list[SemanticEntry] = []
        if self._path is not None and self._path.exists():
            self._load()

    # ------ 对外接口：读取与命中 ------
    def match(self, intent: str) -> list[str]:
        """按关键词规则匹配意图，返回命中内容（最多 _MAX_INJECT 条）。

        Args:
            intent: 用户意图/查询文本。

        Returns:
            命中内容列表；无命中或意图为空返回空列表。
        """
        if not intent:
            return []
        hits = [entry.content for entry in self._entries if any(kw in intent for kw in entry.keywords)]
        return hits[:_MAX_INJECT]

    def entries(self) -> list[SemanticEntry]:
        """已确认条目（只读视图）。"""
        return list(self._entries)

    # ------ 对外接口：写入（double-confirm）------
    def staged_add(self, content: str, keywords: list[str] | tuple[str, ...] | None = None) -> int:
        """把候选语义记忆加入待确认池（未确认不参与命中）。

        Args:
            content: 结论文本。
            keywords: 命中关键词。

        Returns:
            候选 ID（供 confirm/reject 使用；-1 表示内容为空被拒绝）。
        """
        text = content.strip()
        if not text:
            return -1
        self._pending.append(SemanticEntry(text, tuple(keywords or ())))
        return len(self._pending) - 1

    def confirm(self, candidate_id: int) -> bool:
        """人工/规则确认通过：候选转正为正式条目并落盘。

        Args:
            candidate_id: staged_add 返回的候选 ID。

        Returns:
            是否确认成功（越界或已确认返回 False）。
        """
        if not 0 <= candidate_id < len(self._pending) or self._pending[candidate_id] is None:
            return False
        entry = self._pending[candidate_id]
        self._pending[candidate_id] = None  # 占位防重复确认
        if any(e.content == entry.content for e in self._entries):
            return False
        self._entries.append(entry)
        if self._path is not None:
            self._save()
        return True

    def reject(self, candidate_id: int) -> bool:
        """人工/规则否决：丢弃候选。

        Args:
            candidate_id: 候选 ID。

        Returns:
            是否丢弃成功。
        """
        if not 0 <= candidate_id < len(self._pending) or self._pending[candidate_id] is None:
            return False
        self._pending[candidate_id] = None
        return True

    # ------ 内部实现 ------
    def _load(self) -> None:
        """从 JSON 读取正式条目（耐脏：非法文件回退空库）。"""
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            entries = raw.get("entries", []) if isinstance(raw, dict) else []
        except (ValueError, OSError):
            entries = []
        self._entries = [
            SemanticEntry(str(item.get("content", "")).strip(), tuple(item.get("keywords", []) or []))
            for item in entries
            if isinstance(item, dict) and str(item.get("content", "")).strip()
        ]

    def _save(self) -> None:
        """把正式条目写回 JSON（自动建目录）。"""
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "说明": "语义记忆（结论型经验）。写入须经由 staged_add → confirm 双确认，未确认条目不落盘",
            "entries": [
                {"content": entry.content, "keywords": list(entry.keywords)}
                for entry in self._entries
            ],
        }
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
