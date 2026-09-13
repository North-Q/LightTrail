"""请求上下文契约（用户体系预留，D13 / §7.1）。

设计要点：
- `RequestContext` 是一次请求的身份与配置上下文，贯穿 session / memory / quota /
  LLM 配置解析，作为 ToolContext、MemoryStore、QuotaLedger、SessionManager 的
  第一参数；
- 本期恒为本地单用户（`user_id="_local"`），但签名上无处不在——未来接用户体系时
  业务代码零改动，只换装配根的构造方式（由中间件从登录 token 解析构造）；
- `llm_overrides` 是 per-user Key 的落点（请求级覆盖整块 LLMConfig，见 §7.2）：
  本期恒 None，解析优先级 `请求级 > 部署级 .env > 内置默认` 由 UserConfigProvider
  单点实现；
- frozen：请求上下文在一次请求内不可变，避免跨请求串味。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lighttrail.contracts.llm import LLMConfig

# 本地单用户默认身份（本期所有请求都是它）
LOCAL_USER_ID = "_local"


@dataclass(frozen=True)
class RequestContext:
    """一次请求的身份与配置上下文。

    Attributes:
        user_id: 用户标识；本期恒为 "_local"（多用户时由认证中间件填入）。
        session_id: 会话标识（未落会话时为空串）。
        llm_overrides: 请求级 LLM 配置覆盖（per-user Key 的落点）；本期恒 None。
    """

    user_id: str = LOCAL_USER_ID
    session_id: str = ""
    llm_overrides: LLMConfig | None = None