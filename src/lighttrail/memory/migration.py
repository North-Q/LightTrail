"""旧版单用户记忆目录迁移到 per-user 命名空间。"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from lighttrail.contracts.context import LOCAL_USER_ID

_MEMORY_FILES = ("profile.json", "events.db", "semantic.json")
_USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class MigrationResult:
    """一次迁移的结果。"""

    moved: tuple[Path, ...]
    skipped: tuple[Path, ...]


def migrate_memory_namespace(data_dir: str | Path, user_id: str = LOCAL_USER_ID) -> MigrationResult:
    """迁移旧 ``data/`` 根目录的记忆文件，重复运行安全。

    已存在同名目标文件时保留两端文件并记录 skipped，避免迁移脚本覆盖用户的新数据。
    """
    if not _USER_ID_PATTERN.fullmatch(user_id):
        raise ValueError("user_id 只能包含字母、数字、下划线和连字符")
    root = Path(data_dir)
    destination = root / "users" / user_id / "memory"
    moved: list[Path] = []
    skipped: list[Path] = []
    for name in _MEMORY_FILES:
        source = root / name
        target = destination / name
        if not source.exists():
            continue
        if target.exists():
            skipped.append(source)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        moved.append(target)
    return MigrationResult(tuple(moved), tuple(skipped))
