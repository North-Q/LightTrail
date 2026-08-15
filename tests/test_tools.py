"""工具注册表与分发的 pytest 用例。

迁移自 src/lighttrail/smoke.py 的 test_registry_and_dispatch，
拆分为四个独立测试函数，覆盖正常调用与异常路径。
"""

from __future__ import annotations

import json

from lighttrail.agent import registry


def test_get_current_time() -> None:
    """get_current_time 应返回含 iso 字段的时间字典。"""
    result = json.loads(registry.dispatch("get_current_time", "{}"))
    assert "iso" in result
    assert "weekday" in result


def test_equivalent_exposure() -> None:
    """equivalent_exposure 在 adjust_stops=0 时应返回曝光不变的等效方案。"""
    result = json.loads(
        registry.dispatch("equivalent_exposure", '{"f_stop": 2.8, "shutter_speed": 30, "iso": 400}')
    )
    plans = result["等效方案"]

    # adjust_stops=0 → factor=1，三组参数均不变
    assert plans["ISO 优先"]["iso"] == 400
    assert plans["快门优先"]["shutter_speed"] == 30
    assert abs(plans["光圈优先"]["f_stop"] - 2.8) < 0.01

    # schema 完整性
    schemas = registry.to_openai_schema()
    names = {s["function"]["name"] for s in schemas}
    assert "get_current_time" in names
    assert "equivalent_exposure" in names
    assert all("parameters" in s["function"] for s in schemas)


def test_unknown_tool_error() -> None:
    """调用未注册的工具名应返回包含 error 字段的结果。"""
    err = json.loads(registry.dispatch("no_such_tool", "{}"))
    assert "error" in err


def test_invalid_params_error() -> None:
    """传入非法参数（负值）应返回包含 error 字段的结果。"""
    err2 = json.loads(
        registry.dispatch("equivalent_exposure", '{"f_stop": -1, "shutter_speed": 1, "iso": 100}')
    )
    assert "error" in err2
