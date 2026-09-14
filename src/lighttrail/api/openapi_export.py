"""OpenAPI 契约导出入口（B4-1）：`python -m lighttrail.api.openapi_export [输出路径]`。

设计要点（架构 v4 §3 D8「契约单一真源」）：
- 前后端契约的唯一真源是 pydantic 模型（contracts/）：FastAPI 汇总为 OpenAPI JSON，
  `openapi-typescript` 再生成 `frontend/src/api/generated.ts`；
- 本入口只做「导出」，不做任何改写：同一份文档由 `npm run gen:api` 消费，
  `gen:api && git diff --exit-code` 即漂移门禁（漂移即红）；
- 输出排序稳定（sort_keys）：文档字段顺序变化不应造成生成物 diff。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from lighttrail.api.app import app


def render_openapi() -> str:
    """渲染 OpenAPI 文档为稳定的 JSON 文本。

    Returns:
        OpenAPI 文档 JSON 字符串（UTF-8、缩进 2、键排序、末尾换行）。
    """
    document = app.openapi()
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    """把 OpenAPI 文档写到文件（或标准输出）。

    Args:
        argv: 命令行参数；缺省取 sys.argv[1:]。首个参数为输出路径，缺省写标准输出。

    Returns:
        进程退出码（0 成功）。

    Raises:
        OSError: 写文件失败（权限/路径不存在）。
    """
    args = sys.argv[1:] if argv is None else argv
    text = render_openapi()
    if args:
        Path(args[0]).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())