"""模型路由：按能力矩阵把调用意图映射到具体模型。

设计要点（架构 v2.0 §2.3）：
- 能力矩阵驱动：每个模型声明自己提供的能力（tools/vision/deep），
  路由按调用方声明的意图（needs_*）匹配。默认矩阵面向 ECNU 双模型
  （plus=工具+视觉，max=深推理），但矩阵可注入——接入其他 API 时
  无需改路由逻辑；
- 能力绑定收敛在矩阵内：路由逻辑只认能力名，不绑定任何品牌/型号。
  ECNU 双模型互补（max 无工具）是**该平台的特性**，不是架构前提——
  若某个模型同时具备 tools+deep（如部分全能模型），矩阵声明即可，
  不需要也不可能在代码里预设「深推理与工具互斥」；
- 非法声明（请求了未定义/未满足的能力）显式抛错，尽早暴露「调用方
  意图 × 模型能力」的不一致，而不是静默回退。

能力名（能力矩阵的合法键）：tools / vision / deep。
"""

from __future__ import annotations

from lighttrail.config import DEFAULT_MODEL, DEFAULT_MODEL_REASON

# 合法能力名（供声明校验）
_VALID_CAPABILITIES = frozenset({"tools", "vision", "deep"})

# 能力声明参数 → 能力名
_INTENT_TO_CAP = {
    "needs_tools": "tools",
    "needs_vision": "vision",
    "needs_deep_reasoning": "deep",
}


class RouterError(Exception):
    """模型路由参数非法（能力声明不合法或矩阵无法满足）。"""


class ModelRouter:
    """能力矩阵驱动的模型选择器。

    Args:
        default_model: 默认模型名（通常提供 tools + vision 能力）。
        reason_model: 深推理模型名（提供 deep 能力）。可与 default_model
            相同（单模型全能场景），此时能力矩阵两条目指向同一模型。
        capability_matrix: 可选自定义能力矩阵覆盖默认绑定。
            格式：{模型名: {能力名: bool}}，能力名 ∈ {tools, vision, deep}。
    """

    def __init__(
        self,
        *,
        default_model: str = DEFAULT_MODEL,
        reason_model: str = DEFAULT_MODEL_REASON,
        capability_matrix: dict[str, dict[str, bool]] | None = None,
    ) -> None:
        if capability_matrix is not None:
            self._validate_matrix(capability_matrix)
            self._matrix = dict(capability_matrix)
        elif default_model == reason_model:
            # 单模型全能：default 与 reason 指向同一模型，默认矩阵视为全能
            # （避免字面量 dict 同 key 覆盖导致工具能力丢失）
            self._matrix = {
                default_model: {"tools": True, "vision": True, "deep": True},
            }
        else:
            # 双模型互补：default_model 提供工具/视觉，reason_model 提供深推理
            self._matrix = {
                default_model: {"tools": True, "vision": True},
                reason_model: {"deep": True},
            }
        self._default_model = default_model

    @staticmethod
    def _validate_matrix(matrix: dict[str, dict[str, bool]]) -> None:
        """校验自定义能力矩阵：能力名必须合法，避免静默拼写错误。"""
        for model, caps in matrix.items():
            unknown = set(caps) - _VALID_CAPABILITIES
            if unknown:
                raise RouterError(
                    f"能力矩阵中存在非法能力名 {sorted(unknown)}（模型 {model}）；"
                    f"合法能力名：{sorted(_VALID_CAPABILITIES)}"
                )

    def resolve(
        self,
        *,
        needs_tools: bool = False,
        needs_vision: bool = False,
        needs_deep_reasoning: bool = False,
    ) -> str:
        """把能力需求解析为具体模型名。

        Args:
            needs_tools: 需要工具调用能力。
            needs_vision: 需要图像理解能力。
            needs_deep_reasoning: 需要长上下文深度推理。

        Returns:
            第一个满足全部声明的模型名；无任何需求时返回默认模型。
            多个模型同时满足时按矩阵插入顺序返回首个（确定性）。

        Raises:
            RouterError: 矩阵中没有任何模型满足全部声明。
        """
        wanted = {
            _INTENT_TO_CAP["needs_tools"]: needs_tools,
            _INTENT_TO_CAP["needs_vision"]: needs_vision,
            _INTENT_TO_CAP["needs_deep_reasoning"]: needs_deep_reasoning,
        }
        for name, caps in self._matrix.items():
            if all(not wanted[cap] or caps.get(cap, False) for cap in wanted):
                return name
        wanted_str = ", ".join(cap for cap, on in wanted.items() if on) or "（无）"
        raise RouterError(f"能力矩阵中没有任何模型满足声明：{wanted_str}")