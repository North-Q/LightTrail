"""本地冒烟测试：验证工具注册、schema 生成、分发与 Agent 循环逻辑。

不依赖网络与 API Key（LLM 部分使用本地伪客户端），可直接运行：
    python -m lighttrail.smoke
"""

from __future__ import annotations

import sys

from lighttrail.agent import Agent, registry
from lighttrail.tools import (  # noqa: F401  触发全部工具注册
    astronomy,
    basic,
    exposure,
    site_match,
    weather,
)

PASSED = 0


def check(name: str, cond: bool) -> None:
    global PASSED
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {name}")
    if cond:
        PASSED += 1
    else:
        raise SystemExit(f"冒烟测试失败：{name}")


class FakeChatClient:
    """按脚本预置响应序列的伪客户端，用于离线验证 Agent 循环。"""

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.calls = []

    def chat(self, messages, *, model=None, tools=None, temperature=0.2) -> dict:
        self.calls.append({"messages": messages, "tools": tools})
        return self._responses.pop(0)


def test_registry_and_dispatch() -> None:
    print("1) 工具注册与分发")
    schemas = registry.to_openai_schema()
    check(f"schema 共 {len(schemas)} 个工具", len(schemas) >= 2)
    names = {s["function"]["name"] for s in schemas}
    check("包含 get_current_time", "get_current_time" in names)
    check("包含 equivalent_exposure", "equivalent_exposure" in names)
    check("包含星空工具（star_shutter_rule）", "star_shutter_rule" in names)
    check("包含天气工具（weather_forecast）", "weather_forecast" in names)
    check("包含机位匹配工具（match_sites）", "match_sites" in names)
    check("包含照片分析工具（analyze_photo）", "analyze_photo" in names)
    check("包含照片反推工具（reverse_engineer_photo）", "reverse_engineer_photo" in names)
    check("schema 含 parameters", all("parameters" in s["function"] for s in schemas))

    import json

    now = json.loads(registry.dispatch("get_current_time", "{}"))
    check("get_current_time 返回 iso 字段", "iso" in now)

    result = json.loads(
        registry.dispatch("equivalent_exposure", '{"f_stop": 2.8, "shutter_speed": 30, "iso": 400}')
    )
    plans = result["等效方案"]
    check("曝光不变：ISO 优先方案 ISO 仍为 400", plans["ISO 优先"]["iso"] == 400)
    check("曝光不变：快门优先方案快门仍为 30s", plans["快门优先"]["shutter_speed"] == 30)
    check("曝光不变：光圈优先方案光圈仍为 f/2.8", abs(plans["光圈优先"]["f_stop"] - 2.8) < 0.01)

    err = json.loads(registry.dispatch("no_such_tool", "{}"))
    check("未知工具返回 error", "error" in err)

    err2 = json.loads(registry.dispatch("equivalent_exposure", '{"f_stop": -1, "shutter_speed": 1, "iso": 100}'))
    check("非法参数返回 error", "error" in err2)


def test_agent_loop() -> None:
    print("2) Agent 循环（伪客户端）")
    tool_call_msg = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_current_time", "arguments": "{}"},
            }
        ],
    }
    final_msg = {"role": "assistant", "content": "现在是北京时间 2026-08-12 23:40。"}
    fake = FakeChatClient([tool_call_msg, final_msg])
    agent = Agent(fake, registry, model="ecnu-plus")
    reply = agent.run("现在几点？")

    check("最终回复来自模型", reply.startswith("现在是"))
    check("伪客户端被调用 2 次", len(fake.calls) == 2)
    # 第一轮请求应携带 tools schema
    check("请求携带工具 schema", fake.calls[0]["tools"] is not None)
    # 工具结果已回传：第二轮消息含 role=tool
    roles = [m["role"] for m in fake.calls[1]["messages"]]
    check("工具结果已回传模型", "tool" in roles)
    # 历史保留用户消息
    check("历史保留用户消息", any(m.get("content") == "现在几点？" for m in agent.history))

    agent.reset()
    check("reset 后历史为空", agent.history == [])


def main() -> int:
    print("LightTrail 冒烟测试（离线，无需 API Key）\n")
    test_registry_and_dispatch()
    test_agent_loop()
    print(f"\n全部通过：{PASSED} 项检查 OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
