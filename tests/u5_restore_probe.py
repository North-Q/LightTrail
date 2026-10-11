"""U5独立进程只读恢复探针；导入前阻断网络，不运行旧真实轮次。"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import sys
from typing import Any

from httpx import ASGITransport, AsyncClient

external_attempts = 0
model_calls = 0


def forbidden_network(*args: Any, **kwargs: Any) -> Any:
    """任何网络尝试都计为恢复失败。"""
    global external_attempts
    external_attempts += 1
    raise AssertionError("恢复禁止网络")


_CONNECT = socket.socket.connect
_CONNECT_EX = socket.socket.connect_ex
_DNS = socket.getaddrinfo


def _check_address(address: Any) -> None:
    """允许Windows事件循环自唤醒回环连接，拒绝任何外部地址。"""
    if isinstance(address, tuple):
        try:
            if ipaddress.ip_address(address[0]).is_loopback:
                return
        except ValueError:
            pass
    forbidden_network()


def _connect(sock: socket.socket, address: Any) -> None:
    """在真实连接前检查边界。"""
    _check_address(address)
    _CONNECT(sock, address)


def _connect_ex(sock: socket.socket, address: Any) -> int:
    """同样覆盖非阻塞连接。"""
    _check_address(address)
    return _CONNECT_EX(sock, address)


def _dns(host: Any, *args: Any, **kwargs: Any) -> Any:
    """拒绝非回环DNS，避免阻断事件循环内部socketpair。"""
    if host not in (None, "localhost", b"localhost"):
        _check_address((host.decode() if isinstance(host, bytes) else host, 0))
    return _DNS(host, *args, **kwargs)


socket.socket.connect = _connect
socket.socket.connect_ex = _connect_ex
socket.getaddrinfo = _dns

from lighttrail.api.app import ApiDeps, create_app
from lighttrail.api.routes import get_request_context
from lighttrail.api.session import SessionManager
from lighttrail.composition import build_application
from lighttrail.config import Settings
from lighttrail.contracts.context import RequestContext


class RejectModel:
    """模型替身只计数并拒绝。"""

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        """模型调用即失败。"""
        global model_calls
        model_calls += 1
        raise AssertionError("恢复禁止模型")

    async def acall(self, *args: Any, **kwargs: Any) -> Any:
        """异步模型调用也拒绝。"""
        return self.chat(*args, **kwargs)


async def main() -> None:
    """新建服务与缓存，只GET原卡和反馈，输出核验状态。"""
    settings = Settings(_env_file=None)
    services = build_application(settings, client=RejectModel())
    app = create_app(ApiDeps(services, SessionManager(settings.data_dir)))
    app.dependency_overrides[get_request_context] = lambda: RequestContext(user_id="owner")
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://localhost") as client:
            source = await client.get(sys.argv[1].removesuffix("/feedback"))
            feedback = await client.get(sys.argv[1])
            assert source.status_code == feedback.status_code == 200
            print(
                json.dumps(
                    {
                        "feedback": feedback.json(),
                        "model_calls": model_calls,
                        "external_attempts": external_attempts,
                    },
                    ensure_ascii=False,
                )
            )
    finally:
        await services.aclose()


if __name__ == "__main__":
    asyncio.run(main())
