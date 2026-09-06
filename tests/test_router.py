"""ModelRouter 能力矩阵的 pytest 用例：验证路由映射与非法声明。"""

from __future__ import annotations

import pytest

from lighttrail.llm.router import ModelRouter, RouterError


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


def test_invalid_deep_with_tools() -> None:
    """深推理与工具同时声明 → RouterError。"""
    with pytest.raises(RouterError):
        ModelRouter().resolve(needs_tools=True, needs_deep_reasoning=True)


def test_invalid_deep_with_vision() -> None:
    """深推理与视觉同时声明 → RouterError。"""
    with pytest.raises(RouterError):
        ModelRouter().resolve(needs_vision=True, needs_deep_reasoning=True)


def test_custom_matrix() -> None:
    """注入自定义模型名时按同构矩阵路由。"""
    router = ModelRouter(default_model="model-a", reason_model="model-b")
    assert router.resolve() == "model-a"
    assert router.resolve(needs_vision=True) == "model-a"
    assert router.resolve(needs_deep_reasoning=True) == "model-b"


def test_same_model_rejected() -> None:
    """默认模型与推理模型相同 → 构造失败。"""
    with pytest.raises(RouterError):
        ModelRouter(default_model="x", reason_model="x")