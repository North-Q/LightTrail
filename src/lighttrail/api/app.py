"""FastAPI 应用工厂（E7-3）：单例初始化 Agent/Orchestrator/SessionManager/Ledger。

设计要点（架构 v2.0 §2.8 / §2.9）：
- 薄服务层：唯一职责是组装依赖与挂路由，业务逻辑全部在 Agent/Orchestrator；
- 依赖注入：create_app(deps=...) 支持测试注入 Fake（FakeChatClient /
  Fake 数据源 / 临时 SessionManager），默认构造走真实 Settings；
- 默认单进程 uvicorn 即最优（默认串行配置下 LLM 是全局瓶颈）；CORS 面向本地
  开发放开，开源发布前应按部署域收紧；
- `uvicorn lighttrail.api.app:app` 直接启动。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from lighttrail.agent import registry as global_registry
from lighttrail.agent.tools import ToolRegistry
from lighttrail.api.routes import router
from lighttrail.api.session import SessionManager
from lighttrail.config import DEFAULT_MODEL, load_settings
from lighttrail.infra.quota import QuotaLedger
from lighttrail.llm.client import ChatClient
from lighttrail.memory import MemoryManager
from lighttrail.tools import (  # noqa: F401  触发全部工具注册
    astronomy,
    basic,
    exposure,
    memory_tool,
    photo_analysis,
    site_match,
    weather,
)

logger = logging.getLogger("lighttrail.api.app")


@dataclass
class ApiDeps:
    """Web 服务运行依赖（测试可整体替换为 Fake）。

    Attributes:
        client: LLM 客户端（默认真实 Settings + QuotaLedger）。
        sessions: 会话管理器。
        memory: 记忆管理器（档案/事件/语义）。
        registry: 工具注册表。
        model: 对话/工具主模型。
        reason_thinking: 深推理扩展参数开关（平台中立，见 ADR-003）。
        dispatch: 数据采集函数覆盖（测试 Fake 数据源用；默认注册表直调）。
        photo_analyze: 照片分析覆盖（测试 Fake 用；默认 evaluate 真实链路）。
    """

    client: ChatClient
    sessions: SessionManager
    memory: MemoryManager
    registry: ToolRegistry = global_registry
    model: str = DEFAULT_MODEL
    reason_thinking: bool = True
    dispatch: Callable[[str, str], str] | None = None
    photo_analyze: Callable[[str, str], dict[str, Any]] | None = None


def create_app(deps: ApiDeps | None = None) -> FastAPI:
    """构造 FastAPI 应用。

    Args:
        deps: 运行依赖；缺省按真实 Settings 构建（API Key 缺失时仅告警，
            服务可启动、/docs 可访问，LLM 调用会失败）。

    Returns:
        配置完成的 FastAPI 实例（挂在 app.state.deps）。
    """
    if deps is None:
        settings = load_settings()
        if not settings.has_api_key:
            logger.warning("未配置有效 API Key：服务可启动，但 LLM 调用会失败（请检查 .env）")
        client = ChatClient(
            settings.api_key,
            settings.base_url,
            serial_llm=settings.serial_llm,
            quota=QuotaLedger(warn_threshold=settings.quota_warn_threshold),
        )
        deps = ApiDeps(
            client=client,
            sessions=SessionManager(settings.data_dir),
            memory=MemoryManager(settings.data_dir),
            model=settings.model,
            reason_thinking=settings.reason_thinking,
        )
    app = FastAPI(
        title="LightTrail · 光迹",
        description="面向摄影场景的 AI 拍摄决策引擎（Web 服务层）",
        version="0.2.0",
    )
    app.state.deps = deps
    # 本地开发放开跨域（Vite dev server 端口）；开源发布前按部署域收紧
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


# uvicorn lighttrail.api.app:app
app = create_app()

__all__ = ["ApiDeps", "app", "create_app"]
