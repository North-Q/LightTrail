"""LightTrail 命令行交互入口。

用法：
    python -m lighttrail.cli

需先在项目根目录配置 .env（参考 .env.example），填入 ECNU_API_KEY。
内置命令：/exit 退出；/reset 清空对话历史；/help 帮助。
"""

from __future__ import annotations

import logging
import sys

from lighttrail.agent import Agent, registry
from lighttrail.config import load_settings
from lighttrail.llm import ChatClient
from lighttrail.tools import basic, exposure  # noqa: F401  触发工具注册

BANNER = "LightTrail · 光迹（Agent 骨架 v0.1）—— 输入 /help 查看命令"

_HELP = """内置命令：
  /exit  退出
  /reset 清空对话历史
  /help  显示本帮助

示例提问：
  · 现在几点？
  · 我现在是 f/4、1/125s、ISO 100，想在不改变曝光的前提下把快门降到 1/30s 该怎么调？
"""


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    settings = load_settings()
    if not settings.has_api_key:
        print("未检测到有效 API Key。请复制 .env.example 为 .env，填入 ECNU_API_KEY 后重试。")
        return 1

    client = ChatClient(settings.api_key, settings.base_url)
    agent = Agent(client, registry, model=settings.model)

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
            agent.reset()
            print("（已清空对话历史）")
            continue
        if user_input == "/help":
            print(_HELP)
            continue

        try:
            reply = agent.run(user_input)
        except Exception as exc:  # noqa: BLE001 - CLI 层兜底，避免直接崩溃
            print(f"[错误] {exc}")
            continue
        print(f"光迹 > {reply}")


if __name__ == "__main__":
    sys.exit(main())
