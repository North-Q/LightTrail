"""U3 本机离线验收服务：生产 API 与组件，隔离目录、合成收藏和可计数模型。"""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI

from evals.fake_data import FakeDispatcher
from lighttrail.api.app import ApiDeps, create_app
from lighttrail.api.routes import get_request_context
from lighttrail.api.session import SessionManager
from lighttrail.composition import build_application
from lighttrail.config import Settings
from lighttrail.contracts.context import RequestContext

NOW = datetime(2026, 10, 6, 8, tzinfo=timezone.utc)


class OfflineModel:
    """可重复输出合成结果，并记录实际收到的上下文。"""

    def __init__(self) -> None:
        """初始化模型调用账本。"""
        self.messages: list[list[dict[str, Any]]] = []

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        """离线意图及综合；返回的乐观结论仍须经过 U1 资格检查。"""
        self.messages.append(messages)
        intent = "意图解析器" in str(messages[0].get("content", ""))
        output = (
            {
                "intent": {"subject_type": "火烧云", "mode": "live", "time_hint": "今晚"},
                "confidence": 0.9,
                "reason": "合成续问",
            }
            if intent
            else {
                "conclusion": "合成西岸观察火烧云，携带合成相机与三脚架。",
                "verdict": "go",
                "confidence": "high",
                "evidence": [],
            }
        )
        return {"role": "assistant", "content": json.dumps(output, ensure_ascii=False)}

    async def acall(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        """异步仍只调用本地替身。"""
        return self.chat(messages, **kwargs)

    def queue_position(self) -> int:
        """本地替身无供应商队列。"""
        return 0


def build_preview(root: Path) -> FastAPI:
    """构造仅用于人工及浏览器验收的离线应用，不读取真实配置或档案。

    Args:
        root: 项目 .pytest-run-u3 内的专用合成目录。

    Returns:
        复用生产路由且包含离线重建控制的应用。
    """
    boundary = (Path.cwd() / ".pytest-run-u3").resolve()
    root = root.resolve()
    if not root.is_relative_to(boundary):
        raise ValueError("离线验收目录必须位于项目 .pytest-run-u3 内")
    state: dict[str, Any] = {"failed": False, "save_failed": False, "tools": []}
    context = RequestContext(requested_at=NOW)
    initial_root = root

    def new_deps() -> ApiDeps:
        """重建真实装配根和会话管理器，替身不跨实例保留计数。"""
        model = OfflineModel()
        state["model"] = model
        state["tools"] = []
        fake = FakeDispatcher(now=datetime(2026, 10, 6, 7, tzinfo=timezone.utc))

        def dispatch(name: str, args: str) -> str:
            """所有模型数据工具均替换为合成结果。"""
            state["tools"].append(name)
            return (
                json.dumps({"error": "离线合成数据源失败"}, ensure_ascii=False)
                if state["failed"]
                else fake(name, args)
            )

        services = build_application(
            Settings(
                _env_file=None,
                LIGHTTRAIL_DATA_DIR=str(root),
                LLM_BASE_URL="http://127.0.0.1:1",
                LLM_API_KEY="u3-offline-model-placeholder",
                AMAP_WEB_SERVICE_KEY="",
                AMAP_JS_API_KEY="",
                AMAP_SECURITY_JS_CODE="",
                BAIDU_MAP_AK="",
                reason_thinking=False,
            ),
            client=model,
            dispatch=dispatch,
        )
        sessions = SessionManager(root)
        original_save = sessions.save

        def save(record: Any) -> None:
            """可控失败仅影响合成会话存储，不修改任何真实目录。"""
            if state["save_failed"]:
                raise OSError("离线模拟保存失败")
            original_save(record)

        sessions.save = save
        return ApiDeps(services, sessions)

    app = create_app(new_deps())
    app.dependency_overrides[get_request_context] = lambda: context

    @app.post("/api/_offline/new-run")
    async def new_run() -> dict[str, str]:
        """使用新的合成子目录，不删除上次证据或触碰真实数据。"""
        nonlocal root
        app.state.deps.services.close()
        root = initial_root / "runs" / uuid.uuid4().hex
        state["failed"] = state["save_failed"] = False
        app.state.deps = new_deps()
        return {"data_dir": str(root)}

    @app.get("/api/_offline/metrics")
    async def metrics() -> dict[str, Any]:
        """返回合成调用计数和实际模型上下文，供离线验收断言。"""
        return {
            "model_calls": len(state["model"].messages),
            "tool_calls": len(state["tools"]),
            "messages": state["model"].messages,
            "data_dir": str(root),
        }

    @app.post("/api/_offline/restart")
    async def restart() -> dict[str, bool]:
        """关闭旧应用服务，使用同一隔离目录重建装配与缓存。"""
        app.state.deps.services.close()
        app.state.deps = new_deps()
        return {"restarted": True}

    @app.post("/api/_offline/mode/{mode}")
    async def mode(mode: str) -> dict[str, str]:
        """切换合成工具/磁盘故障，不调用真实服务。"""
        if mode not in {"ready", "failed", "save-failed"}:
            raise ValueError("未知离线模式")
        state["failed"] = mode == "failed"
        state["save_failed"] = mode == "save-failed"
        return {"mode": mode}

    return app


def main() -> None:
    """启动绑定 loopback 的可重复离线验收服务。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path(".pytest-run-u3/manual"))
    parser.add_argument("--port", type=int, default=8783)
    args = parser.parse_args()
    uvicorn.run(build_preview(args.data_root), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
