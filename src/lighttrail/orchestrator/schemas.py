"""结构化输出契约的迁移期 shim：真源已下沉到 `contracts/models.py`（B1-2）。

TODO(B5-4): 删除本 shim，全部调用方改 `from lighttrail.contracts.models import ...`。
本文件只做 re-export，**不得新增逻辑**（shim 带批次豁免，到期不删即批次不通过）。
"""

from __future__ import annotations

from lighttrail.contracts.models import (
    DecisionCard,
    Intent,
    LocationSuggestion,
    ParamSuggestion,
    PhotoAnalysisReport,
    PhotoReverseReport,
    Source,
)

__all__ = [
    "DecisionCard",
    "Intent",
    "LocationSuggestion",
    "ParamSuggestion",
    "PhotoAnalysisReport",
    "PhotoReverseReport",
    "Source",
]