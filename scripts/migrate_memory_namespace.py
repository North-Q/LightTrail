"""把旧版本地记忆迁入 ``data/users/_local/memory/``。"""

from __future__ import annotations

import argparse
from pathlib import Path

from lighttrail.memory.migration import migrate_memory_namespace


def main() -> int:
    """执行幂等迁移并打印结果。"""
    parser = argparse.ArgumentParser(description="迁移 LightTrail 记忆命名空间")
    parser.add_argument("--data-dir", default="data", help="旧 data 目录路径")
    parser.add_argument("--user-id", default="_local", help="目标用户 ID")
    args = parser.parse_args()
    result = migrate_memory_namespace(Path(args.data_dir), args.user_id)
    for path in result.moved:
        print(f"已迁移：{path}")
    for path in result.skipped:
        print(f"跳过（目标已存在）：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
