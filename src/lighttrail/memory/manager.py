"""记忆统一门面：MemoryManager 按意图组装记忆注入块（M1）。

设计要点（架构 v2.0 §2.5，E3-1 起步）：
- 暴露给 ContextBuilder/编排层的是 `build_injections(intent) -> list[MemoryBlock]`，
  ContextBuilder 只关心「有哪些块、各块文本」，不感知具体记忆实现；
- 四层记忆对应四种注入策略：档案常驻（profile）、事件按需（events，E3-2）、
  语义择优（semantic，E3-3）、短期记忆在对话窗口内由 ContextBuilder 管理；
- 数据本地化：JSON 起步（profile / semantic），事件记忆 SQLite（E3-2）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lighttrail.memory.profile import UserProfile


@dataclass(frozen=True)
class MemoryBlock:
    """一个记忆注入块。

    Attributes:
        name: 块名（profile / events / semantic…），供 ContextBuilder 与测试识别。
        text: 注入文本（非空才有意义，空块由调用方丢弃）。
    """

    name: str
    text: str = ""


class MemoryManager:
    """记忆层门面：持有档案等记忆源，按意图产出注入块。"""

    def __init__(self, data_dir: str | Path, profile: UserProfile | None = None) -> None:
        """初始化记忆管理器。

        Args:
            data_dir: 数据目录（profile.json / events.db / semantic.json 所在）。
            profile: 可注入的档案实例；缺省从 data_dir 加载。
        """
        self._data_dir = Path(data_dir)
        self._profile = profile if profile is not None else UserProfile.load(self._data_dir)

    # ------ 对外接口 ------
    @property
    def profile(self) -> UserProfile:
        """当前用户档案。"""
        return self._profile

    def update_profile(self, fields: dict[str, Any]) -> UserProfile:
        """更新并持久化用户档案（E3-1 阶段唯一写路径）。

        Args:
            fields: 待更新字段（见 UserProfile.update）。

        Returns:
            更新后的档案实例。
        """
        self._profile.update(fields)
        self._profile.save(self._data_dir)
        return self._profile

    def build_injections(self, intent: str = "") -> list[MemoryBlock]:
        """按意图组装记忆注入块（E3-1 先只有档案常驻块）。

        Args:
            intent: 用户意图/查询文本（E3-2 起用于事件按需检索，本阶段忽略）。

        Returns:
            非空注入块列表；无可用记忆时为空列表。
        """
        blocks: list[MemoryBlock] = []
        profile_text = self._profile.to_prompt_section()
        if profile_text:
            blocks.append(MemoryBlock("profile", profile_text))
        return blocks
