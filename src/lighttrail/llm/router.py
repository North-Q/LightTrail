"""模型路由：按能力矩阵把调用意图映射到具体模型。

设计要点（架构 v2.0 §2.3）：
- 能力矩阵硬编码：ecnu-plus 负责工具调用与多模态感知，ecnu-max 负责
  长上下文深推理；矩阵互斥，路由即穷举，无需 LLM 参与；
- 非法声明（如同时要求工具 + 深推理）直接抛错，把「模型能力」与「调用方
  意图」的不一致尽早暴露，而不是静默降级。
"""

from __future__ import annotations

from lighttrail.config import DEFAULT_MODEL, DEFAULT_MODEL_REASON


class RouterError(Exception):
    """模型路由参数非法（能力声明相互矛盾或超出矩阵）。"""


class ModelRouter:
    """能力矩阵驱动的模型选择器。"""

    def __init__(
        self,
        *,
        default_model: str = DEFAULT_MODEL,
        reason_model: str = DEFAULT_MODEL_REASON,
    ) -> None:
        if default_model == reason_model:
            raise RouterError("default_model 与 reason_model 不能为同一模型")
        self._default_model = default_model
        # 能力矩阵：key 为模型名，value 为能力声明（路由时按插入顺序优先匹配）
        self._matrix: dict[str, dict[str, bool]] = {
            default_model: {"tools": True, "vision": True},
            reason_model: {"deep": True},
        }

    def resolve(
        self,
        *,
        needs_tools: bool = False,
        needs_vision: bool = False,
        needs_deep_reasoning: bool = False,
    ) -> str:
        """把能力需求解析为具体模型名。

        Args:
            needs_tools: 需要工具调用能力（ecnu-plus）。
            needs_vision: 需要图像理解能力（ecnu-plus）。
            needs_deep_reasoning: 需要长上下文深度推理（ecnu-max）。

        Returns:
            满足全部声明的模型名；无任何需求时返回默认模型。

        Raises:
            RouterError: 声明自相矛盾（深推理同时要求工具/视觉）。
        """
        if needs_deep_reasoning and (needs_tools or needs_vision):
            raise RouterError(
                "深推理模型不提供工具/视觉能力，needs_deep_reasoning 不能与 "
                "needs_tools/needs_vision 同时声明"
            )
        for name, caps in self._matrix.items():
            if needs_tools and not caps.get("tools", False):
                continue
            if needs_vision and not caps.get("vision", False):
                continue
            if needs_deep_reasoning and not caps.get("deep", False):
                continue
            return name
        return self._default_model