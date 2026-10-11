"""本机服务的来源边界与凭据出口过滤；不提供公网身份认证。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit

from fastapi.responses import JSONResponse

from lighttrail.infra.redact import redact

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
LOCAL_ORIGINS = tuple(
    f"http://{host}:{port}"
    for host in ("localhost", "127.0.0.1", "[::1]")
    for port in (5173, 4173, 8000)
)


def _origin(value: str) -> str | None:
    """仅接受完整的本机 HTTP 来源，不接受路径、凭据或后缀伪装。"""
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in LOCAL_HOSTS
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            return None
        port = parsed.port or 80
        host = f"[{parsed.hostname}]" if parsed.hostname == "::1" else parsed.hostname
        return f"http://{host}:{port}"
    except ValueError:
        return None


class LocalBoundaryMiddleware:
    """校验 Host、实际客户端地址和写请求来源，并过滤文本响应中的服务端凭据。

    Args:
        app: 下游 ASGI 应用。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """在请求进入路由之前拒绝越界访问，SSE 按完整帧过滤而不缓存整条流。"""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        duplicate_boundary = any(
            sum(k.lower() == name for k, _ in scope["headers"]) > 1 for name in (b"host", b"origin")
        )
        host_origin = _origin(f"http://{headers.get('host', '')}")
        try:
            local_peer = ip_address(scope.get("client", ("", 0))[0]).is_loopback
        except ValueError:
            local_peer = False
        allowed = bool(host_origin and local_peer and not duplicate_boundary)
        if scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            origin = headers.get("origin")
            if origin is not None:
                allowed = allowed and _origin(origin) in {*LOCAL_ORIGINS, host_origin}
            else:
                allowed = allowed and headers.get("sec-fetch-site", "same-origin") in {
                    "same-origin",
                    "none",
                }
                if headers.get("referer"):
                    ref = urlsplit(headers["referer"])
                    allowed = allowed and _origin(f"{ref.scheme}://{ref.netloc}") == host_origin
        if not allowed:
            await JSONResponse(
                {"detail": "仅允许本机及明确允许的本地来源访问", "code": "local_origin_denied"},
                status_code=403,
            )(scope, receive, send)
            return

        text_response = False
        streaming = False
        buffer = b""

        async def filtered(message: Message) -> None:
            """JSON 整体过滤；SSE 按帧过滤，避免凭据跨传输块泄漏。"""
            nonlocal text_response, streaming, buffer
            if message["type"] == "http.response.start":
                message["headers"] = [
                    (k, redact(v.decode("latin-1")).encode("latin-1"))
                    for k, v in message["headers"]
                ]
                response_headers = dict(message["headers"])
                content_type = response_headers.get(b"content-type", b"")
                streaming = content_type.startswith(b"text/event-stream")
                text_response = (
                    streaming
                    or b"json" in content_type
                    or b"javascript" in content_type
                    or content_type.startswith(b"text/")
                )
                if text_response:
                    message["headers"] = [
                        (k, v) for k, v in message["headers"] if k.lower() != b"content-length"
                    ]
            elif message["type"] == "http.response.body" and text_response:
                buffer += message.get("body", b"")
                if streaming and message.get("more_body", False):
                    cutoff = buffer.rfind(b"\n\n") + 2
                    if cutoff < 2:
                        return
                    body, buffer = buffer[:cutoff], buffer[cutoff:]
                elif message.get("more_body", False):
                    return
                else:
                    body, buffer = buffer, b""
                message["body"] = redact(body.decode("utf-8")).encode("utf-8")
            await send(message)

        await self.app(scope, receive, filtered)
