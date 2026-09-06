"""上下文构建：把系统提示与消息历史组装为 OpenAI messages。

设计要点（架构 v2.0 §2.7）：
- E1-1 先实现「静态系统提示 + 历史」的基础拼接，接口固定为
  `build(system_prompt, history) -> messages`；
- E1-2 起升级为五层分段组装（角色/准则/工具说明/记忆/轨迹），本模块负责
  prompt 静态前缀缓存命中与版本追踪。
"""

from __future__ import annotations

from typing import Any


class ContextBuilder:
    """组装发送给 LLM 的完整消息列表。"""

    def build(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """把系统提示 + 历史拼接为 OpenAI 消息列表（E1-2 起变为分层组装）。

        Args:
            system_prompt: 系统提示（静态层，可由会话级注入覆盖）。
            history: 会话历史（user / assistant / tool 消息，不含 system）。

        Returns:
            可直接传给 ChatClient.chat 的完整消息列表。
        """
        return [{"role": "system", "content": system_prompt}, *history]