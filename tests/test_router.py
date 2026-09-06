"""ModelRouter 能力矩阵的 pytest 用例：验证路由映射、自定义矩阵与非法能力。"""

from __future__ import annotations

import pytest

from lighttrail.llm.router import ModelRouter, RouteIntent, RouterError


def test_default_no_need() -> None:
    """无任何能力声明时返回默认模型（工具模型 ecnu-plus）。"""
    assert ModelRouter().resolve() == "ecnu-plus"


def test_tools_route_to_plus() -> None:
    """需要工具 → 路由到 ecnu-plus。"""
    assert ModelRouter().resolve(needs_tools=True) == "ecnu-plus"


def test_vision_route_to_plus() -> None:
    """需要视觉 → 路由到 ecnu-plus。"""
    assert ModelRouter().resolve(needs_vision=True) == "ecnu-plus"


def test_deep_reasoning_route_to_max() -> None:
    """需要深推理 → 路由到 ecnu-max。"""
    assert ModelRouter().resolve(needs_deep_reasoning=True) == "ecnu-max"


def test_default_is_reason_model_when_same() -> None:
    """单模型全能场景：default_model 与 reason_model 相同，深推理路由到同一模型。"""
    router = ModelRouter(default_model="unified", reason_model="unified")
    assert router.resolve(needs_deep_reasoning=True) == "unified"
    assert router.resolve(needs_tools=True) == "unified"
    assert router.resolve() == "unified"


def test_custom_matrix_deep_tools_same_model() -> None:
    """注入自定义矩阵：单一模型同时具备 tools+deep，深推理与工具都路由到它。"""
    router = ModelRouter(
        default_model="model-a",
        reason_model="model-b",
        capability_matrix={
            "model-a": {"tools": True, "vision": True, "deep": True},
            "model-b": {"deep": True},
        },
    )
    assert router.resolve(needs_deep_reasoning=True) == "model-a"
    assert router.resolve(needs_tools=True) == "model-a"
    assert router.resolve(needs_vision=True) == "model-a"


def test_custom_matrix_dual_complementary() -> None:
    """注入自定义矩阵：双模型互补（tools/vision 与 deep 分离），按声明各自路由。"""
    router = ModelRouter(
        default_model="perceiver",
        reason_model="reasoner",
        capability_matrix={
            "perceiver": {"tools": True, "vision": True},
            "reasoner": {"deep": True},
        },
    )
    assert router.resolve() == "perceiver"
    assert router.resolve(needs_deep_reasoning=True) == "reasoner"
    with pytest.raises(RouterError):
        # 矩阵中没有模型同时满足 tools+deep
        router.resolve(needs_tools=True, needs_deep_reasoning=True)


def test_unknown_capability_rejected() -> None:
    """自定义矩阵中出现非法能力名 → RouterError（防拼写错误）。"""
    with pytest.raises(RouterError):
        ModelRouter(
            default_model="a",
            reason_model="b",
            capability_matrix={"a": {"tools": True, "magic": True}},
        )


def test_no_model_satisfies_raises() -> None:
    """矩阵中没有任何模型满足声明 → RouterError（而非静默回退）。"""
    router = ModelRouter(
        default_model="a",
        reason_model="b",
        capability_matrix={"a": {"tools": True}, "b": {"deep": True}},
    )
    with pytest.raises(RouterError):
        router.resolve(needs_vision=True)


def test_route_intent_resolves_default_matrix() -> None:
    """RouteIntent 在默认矩阵上解析：工具/视觉 → plus，深推理 → max，默认 → plus。"""
    router = ModelRouter()
    assert RouteIntent.TOOLS.resolve(router) == "ecnu-plus"
    assert RouteIntent.VISION.resolve(router) == "ecnu-plus"
    assert RouteIntent.DEEP_REASONING.resolve(router) == "ecnu-max"
    assert RouteIntent.DEFAULT.resolve(router) == "ecnu-plus"


def test_route_intent_resolves_custom_matrix() -> None:
    """RouteIntent 在自定义矩阵上解析（不绑定品牌，单模型全能由矩阵表达）。"""
    router = ModelRouter(default_model="alpha", reason_model="omega")
    assert RouteIntent.TOOLS.resolve(router) == "alpha"
    assert RouteIntent.DEEP_REASONING.resolve(router) == "omega"
    assert RouteIntent.DEFAULT.resolve(router) == "alpha"
