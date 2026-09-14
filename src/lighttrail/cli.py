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

from lighttrail.composition import (
    build_client,
    build_memory,
    build_orchestrator,
    build_provider,
    build_recorder,
    build_registry,
    build_runtime,
    load_settings,
)
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
            print("[综合] 深推理中（思考模式，可能需要 30~120 秒），请稍候…", file=sys.stderr, flush=True)
        elif name == "管线降级":
            print("[管线] 异常，降级到自由对话…", file=sys.stderr, flush=True)
        elif name == "reason_thinking":
            print("[推理] 思考摘要已记录（M2-04）", file=sys.stderr, flush=True)
    elif event.kind == "tool":
        print(f"[工具] {name} 完成", file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(prog="lighttrail", description="LightTrail · 光迹（拍摄决策引擎）")
    parser.add_argument(
        "--pipeline",
        nargs="*",
        metavar="REQUEST",
        help="一句话出方案：走决策管线（灵感/规划/临场），如 --pipeline 这周末想去拍银河",
    )
    args = parser.parse_args(argv)

    _configure_stdio()
    settings = load_settings()
    if not settings.has_api_key:
        print("未检测到有效 API Key。请复制 .env.example 为 .env，填入 LLM_API_KEY 后重试（ECNU_API_KEY 兼容）。")
        return 1

    # 装配根是唯一的 new 点（B2-4）：CLI 不再取模块级全局单例
    recorder = build_recorder()
    recorder.subscribe(_print_progress)
    client = build_client(settings)
    registry = build_registry(recorder=recorder)
    memory = build_memory(settings)
    # B2-7：自由对话走 PydanticAI runtime（--pipeline 的编排器仍用旧 Agent，切换见 B2-7 剩余项）
    runtime = build_runtime(
        build_provider(settings), registry, settings, recorder=recorder, memory=memory
    )

    if args.pipeline:
        request = " ".join(args.pipeline)
        orchestrator = build_orchestrator(client, registry, runtime, memory=memory, recorder=recorder)
        print(orchestrator.plan(request))
        return 0

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
            runtime.reset()
            print("（已清空对话历史）")
            continue
        if user_input == "/help":
            print(_HELP)
            continue

        try:
            reply = runtime.run(user_input)
        except Exception as exc:  # noqa: BLE001 - CLI 层兜底，避免直接崩溃
            print(f"[错误] {exc}")
            continue
        print(f"光迹 > {reply}")


if __name__ == "__main__":
    sys.exit(main())
