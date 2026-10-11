"""同步门面的进程级协程执行器，避免连接池所属循环随单次调用销毁。

共享循环由进程持有，Runtime 与 SDK 同步入口共用。各应用仍负责关闭自己的资源；
关闭某个应用不能终止其他应用正在使用的共享循环。异步宿主继续使用自身循环。
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

_T = TypeVar("_T")
_SYNC_LOOP: asyncio.AbstractEventLoop | None = None
_SYNC_LOOP_LOCK = threading.Lock()


def shared_loop() -> asyncio.AbstractEventLoop:
    """返回同步入口共用的存活循环，首次创建时等待服务线程就绪。

    Returns:
        进程级后台事件循环；连接池由创建它的应用关闭。
    """
    global _SYNC_LOOP
    with _SYNC_LOOP_LOCK:
        if _SYNC_LOOP is None:
            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def serve() -> None:
                """安装线程所属循环，并在循环开始处理回调后报告就绪。"""
                asyncio.set_event_loop(loop)
                loop.call_soon(ready.set)
                loop.run_forever()

            thread = threading.Thread(target=serve, name="lighttrail-sync", daemon=True)
            thread.start()
            ready.wait()
            _SYNC_LOOP = loop
        if not _SYNC_LOOP.is_running():
            raise RuntimeError("同步执行器的所属事件循环已停止，不能替换循环关闭既有资源")
        return _SYNC_LOOP


def run_sync(coro: Coroutine[Any, Any, _T]) -> _T:
    """在持久共享循环执行协程，同步等待结果或原始异常。

    Args:
        coro: 待执行协程。

    Returns:
        协程结果。

    Raises:
        RuntimeError: 调用方已有运行中的循环，或共享执行器已停止。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        coro.close()
        raise RuntimeError("同步门面不能在事件循环内调用，请使用异步入口")
    try:
        loop = shared_loop()
    except RuntimeError:
        coro.close()
        raise
    return asyncio.run_coroutine_threadsafe(coro, loop).result()
