"""机位 × 天象匹配工具的 pytest 用例。

验证朝向偏差评分、银河题材评分、排序稳定性与参数校验。
"""

from __future__ import annotations

import json

from lighttrail.agent import registry
from lighttrail.tools import site_match  # noqa: F401


def _call(name: str, arguments: dict) -> dict:
    return json.loads(registry.dispatch(name, json.dumps(arguments, ensure_ascii=False)))


def test_match_sites_sunset_orientation() -> None:
    """日落题材：朝向西南的机位应优于朝向东北的机位。"""
    sites = [
        {"名称": "陆家嘴滨江", "纬度": 31.24, "经度": 121.50, "题材": "日落", "朝向": "西"},
        {"名称": "外滩东望", "纬度": 31.24, "经度": 121.49, "题材": "日落", "朝向": "东北"},
    ]
    result = _call("match_sites", {"sites": sites, "date": "2026-08-16"})
    assert "error" not in result
    ranked = result["排序结果"]
    assert ranked[0]["名称"] == "陆家嘴滨江"
    assert ranked[0]["匹配度"] > ranked[1]["匹配度"]
    assert ranked[0]["依据"] and any("偏差" in reason for reason in ranked[0]["依据"])


def test_match_sites_galaxy() -> None:
    """银河题材：无朝向，按银心可见时长与月光干扰评分。"""
    sites = [{"名称": "崇明东滩", "纬度": 31.6, "经度": 121.8, "题材": "银河"}]
    result = _call("match_sites", {"sites": sites, "date": "2026-08-16"})
    assert "error" not in result
    entry = result["排序结果"][0]
    assert 4 <= entry["匹配度"] <= 5  # 8 月中旬银心窗长 + 月光小
    assert "银心窗口" in entry["关键天象"]
    assert any("月光" in reason for reason in entry["依据"])


def test_match_sites_facing_free() -> None:
    """未提供朝向时不扣分，并明确提示。"""
    sites = [{"名称": "任意朝向点", "纬度": 31.23, "经度": 121.47, "题材": "日出"}]
    result = _call("match_sites", {"sites": sites, "date": "2026-08-16"})
    entry = result["排序结果"][0]
    assert entry["匹配度"] == 5
    assert any("未提供朝向" in reason for reason in entry["依据"])


def test_match_sites_invalid_inputs() -> None:
    """空列表、非法题材、缺失字段应返回 error。"""
    assert "error" in _call("match_sites", {"sites": [], "date": "2026-08-16"})
    bad_theme = [{"名称": "x", "纬度": 31.0, "经度": 121.0, "题材": "赶鸭子"}]
    assert "error" in _call("match_sites", {"sites": bad_theme, "date": "2026-08-16"})
    missing = [{"名称": "x", "纬度": 31.0}]
    assert "error" in _call("match_sites", {"sites": missing, "date": "2026-08-16"})
    assert "error" in _call("match_sites", {"sites": bad_theme, "date": "2026-13-99"})
