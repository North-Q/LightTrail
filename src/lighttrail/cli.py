"""LightTrail 命令行交互入口。

用法：
    python -m lighttrail.cli

需先在项目根目录配置 .env（参考 .env.example），填入 LLM_API_KEY
（ECNU_API_KEY 兼容别名亦可）。
内置命令：/exit 退出；/reset 清空对话历史；/help 帮助。
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

from lighttrail.composition import (
    ApplicationServices,
    build_application,
    load_settings,
)
from lighttrail.contracts.context import RequestContext
from lighttrail.infra.trace import TraceEvent

BANNER = "LightTrail · 光迹（拍摄决策引擎）—— 输入 /help 查看命令"


def _configure_stdio() -> None:
    """让标准输出/错误对不可编码字符容错（Windows GBK 控制台。

    模型输出与卡片渲染会带 ⚠ / → 等非 GBK 字符，默认编码器会直接抛 UnicodeEncodeError
    把整条 CLI 崩掉（实测：cp936 控制台下 `--pipeline` rc=1、零输出，白烧一次 LLM 调用）。
    这里只把编码错误降级为替代字符，不改编码本身——控制台是 GBK 就仍按 GBK 输出中文。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(errors="replace")


_HELP = """内置命令：
  /exit  退出
  /reset 清空对话历史
  /help  显示本帮助

示例提问：
  · 现在几点？
  · 14mm f/2.8 拍银河，最大快门多少不拖线？
  · 1/125s 加 ND64 之后快门是多少？
  · 明天上海日落几点？蓝调和黄金时刻窗口是多少？
  · 周五傍晚崇明东滩火烧云概率怎么样？
  · 帮我对比这两个机位今晚拍日落哪个更好（给出经纬度与朝向）
"""


def _print_progress(event: TraceEvent) -> None:
    """把 trace 事件打印为 CLI 进度（stderr，不污染 stdout 结果）。"""
    name = event.name
    if event.kind == "step":
        if name.startswith("采集_"):
            print(f"[采集] {name[3:]} …", file=sys.stderr, flush=True)
        elif name == "意图理解":
            print("[意图] 正在解析请求…", file=sys.stderr, flush=True)
        elif name == "综合_reason":
            print(
                "[综合] 深推理中（思考模式，可能需要 30~120 秒），请稍候…",
                file=sys.stderr,
                flush=True,
            )
        elif name == "管线降级":
            print("[管线] 异常，降级到自由对话…", file=sys.stderr, flush=True)
        elif name == "reason_thinking":
            print("[推理] 思考摘要已记录（M2-04）", file=sys.stderr, flush=True)
    elif event.kind == "tool":
        print(f"[工具] {name} 完成", file=sys.stderr, flush=True)


def main(
    argv: list[str] | None = None,
    *,
    services: ApplicationServices | None = None,
    request_context: RequestContext | None = None,
) -> int:
    """解析 CLI 参数并通过统一装配执行请求。

    Args:
        argv: 命令行参数；空值时读取进程参数。
        services: 应用服务覆盖，供离线集成与嵌入式调用使用。
        request_context: 调用方确定的身份；默认本地用户。

    Returns:
        进程退出码。
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(
        prog="lighttrail", description="LightTrail · 光迹（拍摄决策引擎）"
    )
    parser.add_argument(
        "--pipeline",
        nargs="*",
        metavar="REQUEST",
        help="一句话出方案：走决策管线（灵感/规划/临场），如 --pipeline 这周末想去拍银河",
    )
    args = parser.parse_args(argv)

    _configure_stdio()
    settings = services.settings if services is not None else load_settings()
    if services is None and not settings.has_api_key:
        print(
            "未检测到有效 API Key。请复制 .env.example 为 .env，填入 LLM_API_KEY 后重试（ECNU_API_KEY 兼容）。"
        )
        return 1

    active_services = services if services is not None else build_application(settings)
    context = request_context if request_context is not None else RequestContext()
    try:
        if args.pipeline:
            scope = active_services.create_scope(context)
            scope.recorder.subscribe(_print_progress)
            print(scope.orchestrator.plan(" ".join(args.pipeline)))
            return 0
        return _chat_loop(active_services, context)
    finally:
        if services is None:
            active_services.close()


def _chat_loop(services: ApplicationServices, context: RequestContext) -> int:
    """逐轮创建作用域，只有对话历史在 CLI 会话中显式传递。"""
    history: list[Any] = []

    print(BANNER)
    while True:
        try:
            user_input = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            return 0

        if not user_input:
            continue
        if user_input == "/exit":
            print("再见！")
            return 0
        if user_input == "/reset":
            history = []
            print("（已清空对话历史）")
            continue
        if user_input == "/help":
            print(_HELP)
            continue

        try:
            scope = services.create_scope(context)
            scope.recorder.subscribe(_print_progress)
            scope.runtime.load_messages(history)
            reply = scope.runtime.run(user_input)
            history = scope.runtime.messages
        except Exception as exc:  # noqa: BLE001 - CLI 层兜底，避免直接崩溃
            print(f"[错误] {exc}")
            continue
        print(f"光迹 > {reply}")


if __name__ == "__main__":
    sys.exit(main())
