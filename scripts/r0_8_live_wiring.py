r"""R0-8 最小真实模型联调：本地 API + 合成工具数据，核对模型路由与注入来源。

用法：项目 dev extras 安装后，从仓库根目录执行
``.\.venv\Scripts\python.exe -m scripts.r0_8_live_wiring``。
脚本仅向已配置的 LLM 服务发送两条合成请求，不调用天气/地图服务，也不落地用户数据。
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from httpx import ASGITransport, AsyncClient

from evals.fake_data import FakeDispatcher
from lighttrail.api.app import ApiDeps, create_app
from lighttrail.api.routes import get_request_context
from lighttrail.api.session import SessionManager
from lighttrail.composition import build_application, build_client
from lighttrail.config import Settings, load_settings
from lighttrail.contracts.context import RequestContext
from lighttrail.runtime.registry import ToolRegistry

ROOT = Path(__file__).resolve().parents[1]
CASE_FILE = ROOT / "evals" / "cassettes" / "v2" / "L2-001.json"
GOLDEN_FILE = ROOT / "evals" / "golden" / "L2-cases.json"
KNOWLEDGE_QUERY = "卷云冰晶对长波散射有什么影响？"
KNOWLEDGE_FACT = "卷云冰晶对长波散射敏感"
CAMERA = "松下 S5M2"


class WiringCheckError(RuntimeError):
    """生产联调断言失败。"""


class _ObservedClient:
    """记录真实客户端收到的模型名和提示词；不记录 Key 或模型响应。"""

    def __init__(self, settings: Settings) -> None:
        """使用应用装配根构造真实 LLM 客户端。

        Args:
            settings: 已解析的部署配置。
        """
        self._inner = build_client(settings)
        self.quota = self._inner.quota
        self.calls: list[dict[str, Any]] = []

    async def acall(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        """转发一次异步模型请求，并只在内存中留存调用信息。"""
        self.calls.append({"messages": messages, "model": kwargs.get("model")})
        return await self._inner.acall(messages, **kwargs)

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        """兼容客户端同步端口。"""
        self.calls.append({"messages": messages, "model": kwargs.get("model")})
        return self._inner.chat(messages, **kwargs)

    def queue_position(self) -> int:
        """透传真实客户端的排队信息。"""
        return self._inner.queue_position()


def _events(body: str) -> list[dict[str, Any]]:
    """解析本地 ASGI 请求返回的 SSE 事件。"""
    return [json.loads(line[5:].strip()) for line in body.splitlines() if line.startswith("data:")]


def _prompt_text(call: dict[str, Any]) -> str:
    """拼接一次调用的提示词，以核对记忆与知识来源。"""
    return "\n".join(str(message.get("content", "")) for message in call["messages"])


async def _run() -> None:
    """顺序执行知识问答和拍摄决策，核对同一请求身份的应用装配。"""
    settings = load_settings()
    if not settings.has_api_key:
        raise WiringCheckError("缺少有效 LLM API Key，无法执行真实模型联调")
    case = next(
        row
        for row in json.loads(GOLDEN_FILE.read_text(encoding="utf-8"))["pipeline_cases"]
        if row["id"] == "L2-001"
    )
    cassette = json.loads(CASE_FILE.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="lighttrail_r08_live_") as directory:
        active = settings.model_copy(update={"data_dir": directory})
        observed = _ObservedClient(active)
        services = build_application(
            active,
            client=observed,
            registry=ToolRegistry(()),
            dispatch=FakeDispatcher(payloads=cassette["tool_results"]),
        )
        values = case["request_context"]
        context = RequestContext(
            user_id=values["user_id"],
            requested_at=datetime.fromisoformat(values["requested_at"]),
            tz_offset=values["tz_offset"],
        )
        services.memory.update_profile(context, case["profile"])
        app = create_app(ApiDeps(services, SessionManager(directory)))
        app.dependency_overrides[get_request_context] = lambda: context
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as http:
                chat = await http.post("/api/chat", json={"message": KNOWLEDGE_QUERY})
                if chat.status_code != 200 or any(
                    event.get("type") == "error" for event in _events(chat.text)
                ):
                    raise WiringCheckError("本地 /api/chat 未成功完成")
                chat_calls = list(observed.calls)
                if not chat_calls or chat_calls[0]["model"] != active.model:
                    raise WiringCheckError("知识问答未使用工具模型路由")
                if not any(
                    KNOWLEDGE_FACT in _prompt_text(call) and CAMERA in _prompt_text(call)
                    for call in chat_calls
                ):
                    raise WiringCheckError("知识或当前用户器材未注入问答提示词")

                start = len(observed.calls)
                decide = await http.post("/api/decide", json={"request": case["request"]})
                decision_events = _events(decide.text)
                if decide.status_code != 200 or not any(
                    event.get("type") == "card" for event in decision_events
                ):
                    raise WiringCheckError("本地 /api/decide 未产出决策卡")
                if not any(
                    event.get("type") == "step" and event.get("name") == "意图路由_规则"
                    for event in decision_events
                ):
                    raise WiringCheckError("决策请求未走可审计规则路由")
                decision_calls = observed.calls[start:]
                if not decision_calls or decision_calls[0]["model"] != active.model_reason:
                    raise WiringCheckError("决策综合未使用 reason model")
                if CAMERA not in _prompt_text(decision_calls[0]):
                    raise WiringCheckError("当前用户器材未注入决策提示词")
        finally:
            services.close()
    print("R0-8 真实最小联调通过：本地 API、模型路由、reason model、用户记忆、领域知识")


def main() -> None:
    """脚本入口。"""
    asyncio.run(_run())


if __name__ == "__main__":
    main()
