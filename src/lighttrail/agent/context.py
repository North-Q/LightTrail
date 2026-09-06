"""上下文构建：把系统提示分层组装 + 消息历史拼接为 OpenAI messages。

设计要点（架构 v2.0 §2.7，E1-2 落地）：
- 系统提示按「变化频率升序」分为五层：① 角色与使命（静态）→ ② 行为准则（静态）
  → ③ 工具使用说明（半静态，注册表变更时刷新）→ ④ 用户档案+语义记忆（动态，注入器）
  → ⑤ 会话轨迹摘要（动态，注入器）；
- ①②③ 构成静态前缀：多轮对话间字节不变，服务平台 prompt 缓存命中（命中价 1/5）；
- 每层独立 token 预算与版本号：版本注释头只含静态层版本（保证静态前缀字节稳定），
  动态层在段尾带版本注释，供回归 diff；
- 动态层未就绪时注入空段（整段省略），行为退化为静态三层。

接口约定：
- `to_openai_messages(history)`：E1-2 起的主入口，组装分层 system + 历史；
- `build(system_prompt, history)`：旧接口保留为兼容薄封装（未经分层，透传原始提示）。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from lighttrail.agent.tools import ToolRegistry

# ------ 层定义与常量 ------
# 层名（每层独立版本号的前缀）
LAYER_ROLE = "role"
LAYER_CONDUCT = "conduct"
LAYER_TOOLS = "tools"
LAYER_PROFILE = "profile"
LAYER_TRACE = "trace"
_LAYER_ORDER: tuple[str, ...] = (LAYER_ROLE, LAYER_CONDUCT, LAYER_TOOLS, LAYER_PROFILE, LAYER_TRACE)

# 各层的中文标题（分层段落的 markdown 小标题，便于回归 diff）
_SECTION_HEADINGS: dict[str, str] = {
    LAYER_ROLE: "角色与使命",
    LAYER_CONDUCT: "行为准则",
    LAYER_TOOLS: "可用工具",
    LAYER_PROFILE: "用户档案与语义记忆",
    LAYER_TRACE: "会话轨迹摘要",
}

# 静态层默认内容（由原 DEFAULT_SYSTEM_PROMPT 拆分为两段，语义不变）
DEFAULT_ROLE_PROMPT = """你是 LightTrail（光迹）摄影助手，一位专业摄影智能体。

职责：帮助摄影师完成拍摄前的规划与拍摄中的参数决策。
当前阶段提供以下能力（通过工具实现）：
- 获取当前时间，用于判断拍摄时机；
- 曝光参数推荐：等效曝光换算、星空 500/NPF 法则、ND 长曝光换算；
- 天文查询：日出日落/蓝调黄金/晨昏蒙影、太阳方位、月相月升月落、银心可见窗口；
- 天气查询：未来 1-7 天云量/能见度/降水/风力，火烧云概率评分；
- 机位匹配：多机位 × 天象条件对比排序。"""

DEFAULT_CONDUCT_PROMPT = """行为要求：
- 回答简洁、专业，使用中文；
- 需要真实数据或计算时，优先调用工具获取，不要凭空编造；
- 用户未指定时，默认采用 135 全画幅相机与常见档位给出建议；
- 涉及具体拍摄决策时，可以给出推荐值，但要说明依据与取舍。"""

# 兼容导出：拆分前的整段默认提示（供旧调用方使用），语义与拆分前一致
DEFAULT_SYSTEM_PROMPT = DEFAULT_ROLE_PROMPT + "\n\n" + DEFAULT_CONDUCT_PROMPT

# 各层 token 预算（估算：中英文混合保守按 0.5 token/字符，超限截断并标注）
_DEFAULT_BUDGETS: dict[str, int] = {
    LAYER_ROLE: 220,
    LAYER_CONDUCT: 220,
    LAYER_TOOLS: 1200,
    LAYER_PROFILE: 400,
    LAYER_TRACE: 300,
}
_TRUNCATE_MARKER = "…（超预算截断）"

# 静态层版本（工具层版本随注册表内容变化，动态层版本由注入方经 layer_versions 管理）
_STATIC_VERSIONS: dict[str, str] = {LAYER_ROLE: "1", LAYER_CONDUCT: "1"}
_NONE_VERSION = "0"

# 工具的可用说明单条宽度上限（字符），超出截断以控制注入体积
_MAX_TOOL_DESC_CHARS = 160


def _estimate_tokens(text: str) -> int:
    """估算 token 数：中英文混合场景保守按 2 字符 ≈ 1 token。"""
    return max(1, math.ceil(len(text) / 2))


def _build_tools_section(schemas: Sequence[dict[str, Any]]) -> str:
    """把工具 schema 列表精简为中文说明段落（半静态层③）。

    Args:
        schemas: ToolRegistry.to_openai_schema() 的返回（function calling 格式）。

    Returns:
        多行文本：每行一个工具名 + 用途描述。
    """
    if not schemas:
        return "当前无可用工具"
    lines = []
    for schema in schemas:
        fn = schema.get("function", {})
        name = fn.get("name", "")
        desc = (fn.get("description") or "").strip().replace("\n", " ")
        if len(desc) > _MAX_TOOL_DESC_CHARS:
            desc = desc[: _MAX_TOOL_DESC_CHARS - 1] + "…"
        lines.append(f"- {name}：{desc}")
    return "\n".join(lines)


def _section_block(layer: str, heading: str, text: str, version: str) -> str:
    """组装一个分层段落（标题 + 内容 + 段尾版本注释，格式 layer:<层名>@<版本>）。"""
    return f"## {heading}\n{text}\n<!-- layer:{layer}@{version} -->"


class ContextBuilder:
    """五层系统提示的组装器。

    Args:
        role_prompt: 第①层「角色与使命」静态文本。
        conduct_prompt: 第②层「行为准则」静态文本。
        registry: 工具注册表，用于生成第③层「可用工具」说明（半静态，变更自动刷新）。
        profile_provider: 第④层注入器，返回「用户档案+语义记忆」文本；未设置时省略该层。
        trace_provider: 第⑤层注入器，返回「会话轨迹摘要」文本；未设置时省略该层。
        budgets: 各层 token 预算覆盖（键为层名，默认见 _DEFAULT_BUDGETS）。
        layer_versions: 各层版本号覆盖（键为层名，如 {"profile": "3"} → layer:profile@3）。
    """

    def __init__(
        self,
        *,
        role_prompt: str = DEFAULT_ROLE_PROMPT,
        conduct_prompt: str = DEFAULT_CONDUCT_PROMPT,
        registry: ToolRegistry | None = None,
        profile_provider: Callable[[], str] | None = None,
        trace_provider: Callable[[], str] | None = None,
        budgets: Mapping[str, int] | None = None,
        layer_versions: Mapping[str, str | int] | None = None,
    ) -> None:
        self._sections: dict[str, str] = {
            LAYER_ROLE: role_prompt,
            LAYER_CONDUCT: conduct_prompt,
        }
        self._registry = registry
        self._providers: dict[str, Callable[[], str]] = {
            LAYER_PROFILE: profile_provider,
            LAYER_TRACE: trace_provider,
        }
        self._budgets: dict[str, int] = dict(_DEFAULT_BUDGETS)
        if budgets:
            self._budgets.update(budgets)
        self._versions: dict[str, str] = dict(_STATIC_VERSIONS)
        if layer_versions:
            self._versions.update({k: str(v) for k, v in layer_versions.items()})
        # 工具层缓存：(已缓存的工具名元组, 生成的说明文本)；注册表变更时自动失效
        self._tools_cache: tuple[tuple[str, ...], str] | None = None

    # ------ 对外接口 ------
    def to_openai_messages(self, history: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """组装带分层系统提示的完整消息列表。

        Args:
            history: 会话历史（user / assistant / tool 消息，不含 system）。

        Returns:
            可直接传给 ChatClient.chat 的消息列表（首条为 system）。
        """
        return [{"role": "system", "content": self.build_system_prompt()}, *history]

    def build(self, system_prompt: str, history: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """兼容薄封装：不经分层，把传入的 system_prompt 原样作为系统提示。

        Args:
            system_prompt: 系统提示全文（由调用方拼好）。
            history: 会话历史（不含 system）。

        Returns:
            可直接传给 ChatClient.chat 的消息列表（首条为 system）。
        """
        return [{"role": "system", "content": system_prompt}, *history]

    def build_system_prompt(self) -> str:
        """按 ①→⑤ 顺序组装分层系统提示（含版本注释头）。

        Returns:
            分层后的系统提示全文；静态前缀（头 + ①②③）在多轮调用间字节不变。
        """
        versions = self.layer_versions()
        header = (
            "<!-- LightTrail prompt v: "
            f"layer:{LAYER_ROLE}@{versions[LAYER_ROLE]}, layer:{LAYER_CONDUCT}@{versions[LAYER_CONDUCT]}, "
            f"layer:{LAYER_TOOLS}@{versions[LAYER_TOOLS]} -->"
        )
        blocks = [header]
        for layer in _LAYER_ORDER:
            text = self._layer_text(layer)
            if not text.strip():
                continue
            text = self._enforce_budget(layer, text)
            blocks.append(_section_block(layer, _SECTION_HEADINGS[layer], text, versions[layer]))
        return "\n\n".join(blocks)

    def layer_versions(self) -> dict[str, str]:
        """返回各层当前版本号（role/conduct 静态、tools 随注册表、动态层取配置值）。"""
        versions = dict(self._versions)
        versions[LAYER_TOOLS] = self._tools_version()
        versions.setdefault(LAYER_PROFILE, _NONE_VERSION)
        versions.setdefault(LAYER_TRACE, _NONE_VERSION)
        return {layer: versions.get(layer, _NONE_VERSION) for layer in _LAYER_ORDER}

    # ------ 内部实现 ------
    def _layer_text(self, layer: str) -> str:
        """取某层原始文本：静态层读配置，动态层调用注入器，工具层走缓存。"""
        if layer in self._sections:
            return self._sections[layer]
        if layer == LAYER_TOOLS:
            return self._tools_section()
        provider = self._providers.get(layer)
        if provider is None:
            return ""
        return provider()

    def _tools_section(self) -> str:
        """生成第③层工具说明（注册表名集合变化时自动重建）。"""
        if self._registry is None:
            return ""
        names = tuple(self._registry.names())
        if self._tools_cache is not None and self._tools_cache[0] == names:
            return self._tools_cache[1]
        text = _build_tools_section(self._registry.to_openai_schema())
        self._tools_cache = (names, text)
        return text

    def _tools_version(self) -> str:
        """工具层版本：无注册表时为 0，否则为当前工具数量（注册变更即变化）。"""
        if self._registry is None:
            return _NONE_VERSION
        return str(len(self._registry.names()))

    def _enforce_budget(self, layer: str, text: str) -> str:
        """按层预算截断文本（估算 token 超限时截断并标注，保证注入体积可控）。"""
        budget_chars = self._budgets.get(layer, 0) * 2
        if budget_chars <= 0 or _estimate_tokens(text) <= self._budgets.get(layer, 0):
            return text
        cut = max(0, budget_chars - len(_TRUNCATE_MARKER))
        return text[:cut] + _TRUNCATE_MARKER
