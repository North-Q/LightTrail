"""LightTrail · 光迹

面向摄影场景的 AI 拍摄决策引擎（Agent）。

导入本包即设置 `PYDANTIC_AI_NO_BANNER=1`：pydantic-ai 的启动横幅属于框架自推销，
不是产品输出（CLI 会把 stderr 当进度通道、Web 端点会把 stdout 混进日志），
故在包初始化处统一关闭；需要看横幅的调试场景自行 `unset` 即可。
"""

from __future__ import annotations

import os

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

__version__ = "0.1.0"