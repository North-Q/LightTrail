"""ContextBuilder 五层分段组装的 pytest 用例（E1-2）。

验证：
- 默认提示已拆分为「角色与使命」「行为准则」两段静态层；
- 分层顺序 ①→⑤，动态层未就绪时整段省略；
- 静态前缀（版本头 + ①②③）在多轮调用、动态内容变化时字节不变；
- 工具层半静态缓存：注册表内容变化后自动刷新；
- 各层 token 预算截断与版本号报告。
"""

from __future__ import annotations

from typing import Any

from lighttrail.agent import Agent
from lighttrail.agent.context import (
    DEFAULT_CONDUCT_PROMPT,
    DEFAULT_ROLE_PROMPT,
    LAYER_CONDUCT,
    LAYER_PROFILE,
    LAYER_ROLE,
    LAYER_TOOLS,
    LAYER_TRACE,
    ContextBuilder,
)
from lighttrail.agent.tools import ToolRegistry

_HEADING_ROLE = "## 角色与使命"
_HEADING_CONDUCT = "## 行为准则"
_HEADING_TOOLS = "## 可用工具"
_HEADING_PROFILE = "## 用户档案与语义记忆"
_HEADING_TRACE = "## 会话轨迹摘要"


def _make_registry(*tools: tuple[str, str]) -> ToolRegistry:
    """构造独立注册表，避免污染全局 registry（tools: (name, description)）。"""

    reg = ToolRegistry()
    for name, description in tools:
        reg.register(lambda _=name: {"name": _}, name=name, description=description)
    return reg


def _now_builder(**kwargs: Any) -> ContextBuilder:
    """便捷构造：默认无动态注入器。"""
    return ContextBuilder(**kwargs)


# ------ 静态层拆分 ------
def test_role_conduct_split_real() -> None:
    """默认提示确已拆为角色与行为准则两段（职责在角色层、要求在准则层）。"""
    assert "职责" in DEFAULT_ROLE_PROMPT
    assert "行为要求" in DEFAULT_CONDUCT_PROMPT
    assert "行为要求" not in DEFAULT_ROLE_PROMPT


# ------ 分层顺序与空层省略 ------
def test_empty_dynamic_layers_omitted() -> None:
    """未配置动态注入器时，④⑤层整段省略，输出为 头+①②③。"""
    builder = _now_builder(registry=_make_registry(("sun_times", "查询日出日落时刻")))
    system = builder.build_system_prompt()
    assert _HEADING_ROLE in system
    assert _HEADING_CONDUCT in system
    assert _HEADING_TOOLS in system
    assert _HEADING_PROFILE not in system
    assert _HEADING_TRACE not in system


def test_five_layer_order_with_injectors() -> None:
    """配置档案/轨迹注入器后，五个分层按 ①→⑤ 顺序出现。"""
    builder = _now_builder(
        registry=_make_registry(("sun_times", "查询日出日落时刻")),
        profile_provider=lambda: "偏好风光/星空，常驻上海",
        trace_provider=lambda: "调用了 sun_times 工具",
    )
    system = builder.build_system_prompt()
    order = [
        system.index(_HEADING_ROLE),
        system.index(_HEADING_CONDUCT),
        system.index(_HEADING_TOOLS),
        system.index(_HEADING_PROFILE),
        system.index(_HEADING_TRACE),
    ]
    assert order == sorted(order)


# ------ 静态前缀字节稳定性 ------
def test_static_prefix_byte_stable_without_injectors() -> None:
    """无动态层时，多次 build 的系统提示字节完全一致（含注入空段路径）。"""
    builder = _now_builder()
    first = builder.build_system_prompt()
    second = builder.build_system_prompt()
    assert first == second


def test_static_prefix_stable_when_dynamic_changes() -> None:
    """动态层内容变化时，静态前缀（版本头 + ①②③）保持字节不变。"""
    profile_texts = iter(["档案 v1", "档案 v2（变更）"])
    builder = _now_builder(
        profile_provider=lambda: next(profile_texts),
        trace_provider=lambda: "轨迹不变",
    )
    first = builder.build_system_prompt()
    second = builder.build_system_prompt()
    assert first != second  # 动态部分确实变了
    boundary = first.index(_HEADING_PROFILE)
    assert first[:boundary] == second[:boundary]


def test_build_messages_static_across_history() -> None:
    """to_openai_messages 的 system 段不随 history 变化。"""
    builder = _now_builder()
    m1 = builder.to_openai_messages([{"role": "user", "content": "a"}])
    m2 = builder.to_openai_messages([{"role": "user", "content": "b"}, {"role": "assistant", "content": "c"}])
    assert m1[0]["content"] == m2[0]["content"]
    assert m1[1:] == [{"role": "user", "content": "a"}]
    assert m2[1:] == [
        {"role": "user", "content": "b"},
        {"role": "assistant", "content": "c"},
    ]


# ------ 工具层半静态缓存 ------
def test_tools_section_refreshes_on_registry_change() -> None:
    """注册表内容变化后，工具层说明自动重建（缓存按工具名集合失效）。"""
    reg = _make_registry(("sun_times", "查询日出日落时刻"))
    builder = _now_builder(registry=reg)
    first = builder.build_system_prompt()
    assert "sun_times" in first

    reg.register(lambda: {"ok": 1}, name="weather_forecast", description="查询天气预报")
    second = builder.build_system_prompt()
    assert "weather_forecast" in second

    third = builder.build_system_prompt()
    assert second == third  # 无变更时字节稳定（缓存生效）


def test_tools_section_empty_without_registry() -> None:
    """未注入注册表时工具层省略，不报错。"""
    builder = _now_builder()
    system = builder.build_system_prompt()
    assert _HEADING_TOOLS not in system


def test_tools_section_truncates_long_description() -> None:
    """单条工具说明超宽时截断并加省略号。"""
    desc = "很长的工具描述" * 40  # 320 字，超过单条宽度上限
    reg = _make_registry(("long_tool", desc))
    builder = _now_builder(registry=reg)
    text = builder._tools_section()
    assert "…" in text
    assert text.startswith("- long_tool：很长的工具描述")


# ------ token 预算与版本号 ------
def test_profile_budget_truncation() -> None:
    """档案层超预算时截断并标注，小文本不截断。"""
    builder = _now_builder(
        profile_provider=lambda: "偏" * 500,
        budgets={LAYER_PROFILE: 50},  # 50 token ≈ 100 字符
    )
    system = builder.build_system_prompt()
    assert "超预算截断" in system


def test_layer_versions_report() -> None:
    """版本号报告：静态层固定、工具层随数量、动态层可覆盖。"""
    reg = _make_registry(("a", "工具A"), ("b", "工具B"))
    builder = _now_builder(registry=reg, layer_versions={LAYER_PROFILE: "3"})
    versions = builder.layer_versions()
    assert versions[LAYER_ROLE] == "1"
    assert versions[LAYER_CONDUCT] == "1"
    assert versions[LAYER_TOOLS] == "2"
    assert versions[LAYER_PROFILE] == "3"
    assert versions[LAYER_TRACE] == "0"


def test_version_annotations_present() -> None:
    """输出文本带版本注释（头 + 段尾，格式 layer:<层名>@<版本>）。"""
    builder = _now_builder(
        profile_provider=lambda: "档案",
        trace_provider=lambda: "轨迹",
        layer_versions={LAYER_PROFILE: "3"},
    )
    system = builder.build_system_prompt()
    assert "<!-- LightTrail prompt v: layer:role@1" in system
    assert "layer:tools@0 -->" in system
    assert "<!-- layer:profile@3 -->" in system
    assert "<!-- layer:trace@0 -->" in system


# ------ 兼容接口 ------
def test_build_legacy_passthrough() -> None:
    """旧 build() 保持透传：system_prompt 原样输出，不做分层。"""
    builder = _now_builder()
    messages = builder.build("原始提示", [{"role": "user", "content": "hi"}])
    assert messages == [{"role": "system", "content": "原始提示"}, {"role": "user", "content": "hi"}]


# ------ Agent 集成 ------
class _FakeChatClient:
    """记录调用参数的伪客户端。"""

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def chat(self, messages, *, model=None, tools=None, temperature=0.2) -> dict:
        self.calls.append({"messages": messages, "model": model, "tools": tools})
        return self._responses.pop(0)


def test_agent_system_prompt_is_layered() -> None:
    """Agent.run 经 ReActLoop 默认走分层组装：首轮 messages[0] 为分层 system。"""
    tech = {"role": "assistant", "content": "已查询完毕。"}
    fake = _FakeChatClient([tech])
    agent = Agent(fake, _make_registry(("x", "工具X")), model="ecnu-plus")
    agent.run("现在几点？")
    system = fake.calls[0]["messages"][0]
    assert system["role"] == "system"
    assert _HEADING_ROLE in system["content"]
    assert _HEADING_CONDUCT in system["content"]
    assert _HEADING_TOOLS in system["content"]
