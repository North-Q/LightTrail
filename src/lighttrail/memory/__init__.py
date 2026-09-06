"""记忆层：四层记忆（档案 / 事件 / 语义 / 短期）的统一门面与存储实现。

E3-1：档案常驻注入（profile.py + manager.py）。
E3-2：事件记忆（events.py，SQLite 按需检索）。
E3-3：语义记忆（semantic.py，精选注入 + double-confirm）。
"""

from lighttrail.memory.events import EventRecord, EventStore, to_prompt_section
from lighttrail.memory.manager import MemoryBlock, MemoryManager
from lighttrail.memory.profile import UserProfile

__all__ = [
    "EventRecord",
    "EventStore",
    "MemoryBlock",
    "MemoryManager",
    "UserProfile",
    "to_prompt_section",
]
