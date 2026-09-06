"""LightTrail 命令行交互入口。

用法：
    python -m lighttrail.cli

需先在项目根目录配置 .env（参考 .env.example），填入 LLM_API_KEY
（ECNU_API_KEY 兼容别名亦可）。
内置命令：/exit 退出；/reset 清空对话历史；/help 帮助。
"""

from __future__ import annotations

import logging
import sys

from lighttrail.agent import Agent, registry
from lighttrail.config import load_settings
from lighttrail.llm import ChatClient
from lighttrail.memory import MemoryManager
from lighttrail.tools import (  # noqa: F401  触发全部工具注册
    astronomy,
    basic,
    exposure,
    site_match,
    weather,
)

BANNER = "LightTrail · 光迹（拍摄决策引擎）—— 输入 /help 查看命令"

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


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    settings = load_settings()
    if not settings.has_api_key:
        print("未检测到有效 API Key。请复制 .env.example 为 .env，填入 LLM_API_KEY 后重试（ECNU_API_KEY 兼容）。")
        return 1

    client = ChatClient(settings.api_key, settings.base_url, serial_llm=settings.serial_llm)
    agent = Agent(
        client,
        registry,
        model=settings.model,
        memory=MemoryManager(settings.data_dir),
    )

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
