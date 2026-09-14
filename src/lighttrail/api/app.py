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
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from lighttrail.adapters.llm.client import ChatClient
from lighttrail.api.routes import router
from lighttrail.api.session import SessionManager
from lighttrail.composition import (
    build_client,
    build_memory,
    build_registry,
    load_settings,
)
from lighttrail.config import DEFAULT_MODEL
from lighttrail.memory import MemoryManager
from lighttrail.runtime.registry import ToolRegistry
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
    registry: ToolRegistry = field(default_factory=build_registry)
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
        # 装配根是唯一的 new 点（B2-4）
        deps = ApiDeps(
            client=build_client(settings),
            sessions=SessionManager(settings.data_dir),
            memory=build_memory(settings),
            registry=build_registry(),
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
    _install_contract_openapi(app)
    return app


# ------ 契约化 OpenAPI（B4-1：gen:api 流水线的输入真源）------
def _install_contract_openapi(app: FastAPI) -> None:
    """安装契约化 OpenAPI 生成（就地替换 app.openapi）。

    背景：FastAPI 对非 JSONResponse 的 response_class 会先写 `{"type": "string"}` 兜底，
    再把 `responses={200: {"model": SSEEventPayload}}` 的模型 schema 深合并进去，结果是
    `oneOf`/`$ref` 与 `type` 并存（自相矛盾且污染生成物）。SSE 端点的真实契约是事件
    判别联合，故统一移除该兜底键。

    Args:
        app: FastAPI 实例。
    """

    def _openapi() -> dict[str, Any]:
        """生成（并缓存）OpenAPI 文档。"""
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        _strip_stream_fallback(schema)
        app.openapi_schema = schema
        return schema

    app.openapi = _openapi  # type: ignore[method-assign]


def _strip_stream_fallback(schema: dict[str, Any]) -> None:
    """删除 SSE 响应里的通用 `{"type": "string"}` 兜底 schema（原地修改）。"""
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            responses = operation.get("responses")
            if not isinstance(responses, dict):
                continue
            for response in responses.values():
                content = response.get("content") if isinstance(response, dict) else None
                if not isinstance(content, dict):
                    continue
                for media_type, body in content.items():
                    if not media_type.startswith("text/event-stream") or not isinstance(body, dict):
                        continue
                    media = body.get("schema")
                    if isinstance(media, dict) and ("oneOf" in media or "$ref" in media):
                        media.pop("type", None)


# uvicorn lighttrail.api.app:app
app = create_app()

__all__ = ["ApiDeps", "app", "create_app"]
