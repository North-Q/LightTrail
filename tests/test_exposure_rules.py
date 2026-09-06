"""拍摄参数推荐工具（星空 500/NPF、ND 长曝光）的 pytest 用例。

与 tests/ 下既有风格一致：通过 registry.dispatch 走完整分发链路，
验证计算正确性与边界条件。
"""

from __future__ import annotations

import json

from lighttrail.agent import registry
from lighttrail.tools import exposure  # noqa: F401


def _call(name: str, arguments: dict) -> dict:
    """通过注册表分发工具并解析结果。"""
    return json.loads(registry.dispatch(name, json.dumps(arguments, ensure_ascii=False)))


def test_star_shutter_rule_500() -> None:
    """500 法则：500 / 等效焦距，无像素间距时退回 500 法则。"""
    result = _call("star_shutter_rule", {"focal_length": 14, "aperture": 2.8, "crop_factor": 1.0})
    assert result["500 法则最大快门（秒）"] == 35.7  # 500 / 14
    assert result["NPF 法则最大快门（秒）"] is None
    assert "无像素间距数据，按 500 法则" in result["采用建议"]


def test_star_shutter_npf_tightens() -> None:
    """NPF 法则应比 500 法则更保守，且银心赤纬修正会放宽快门。"""
    result = _call("star_shutter_rule", {"focal_length": 14, "aperture": 2.8, "pixel_pitch": 6.0})
    npf = result["NPF 法则最大快门（秒）"]
    assert 19.5 < npf < 20.5  # (35*2.8+30*6)/14 ≈ 19.9
    assert npf < result["500 法则最大快门（秒）"]
    result_dec = _call(
        "star_shutter_rule",
        {"focal_length": 14, "aperture": 2.8, "pixel_pitch": 6.0, "declination": -29.0},
    )
    assert result_dec["NPF 法则最大快门（秒）"] > npf


def test_star_shutter_aps_c_crop() -> None:
    """APS-C 裁切系数应放大等效焦距并收紧快门。"""
    full_frame = _call("star_shutter_rule", {"focal_length": 24, "aperture": 2.0})
    apsc = _call("star_shutter_rule", {"focal_length": 24, "aperture": 2.0, "crop_factor": 1.5})
    assert apsc["500 法则最大快门（秒）"] < full_frame["500 法则最大快门（秒）"]


def test_star_shutter_invalid() -> None:
    """非法参数应返回 error 字段。"""
    assert "error" in _call("star_shutter_rule", {"focal_length": -1, "aperture": 2.8})
    assert "error" in _call("star_shutter_rule", {"focal_length": 14, "aperture": 0})


def test_nd_forward_nd64() -> None:
    """正向换算：ND64 = 6 档，1/125s -> 约 1/2s。"""
    result = _call("nd_long_exposure", {"base_shutter": 0.008, "nd_filter": "ND64"})
    assert result["模式"] == "正向换算"
    assert result["ND 减光档数"] == 6.0
    assert result["长曝光快门"] == "1/2s"


def test_nd_forward_nd1000() -> None:
    """正向换算：ND1000 = 10 档，1/125s -> 约 8s。"""
    result = _call("nd_long_exposure", {"base_shutter": 0.008, "nd_stops": 10})
    assert result["长曝光快门"] == "8.2s"


def test_nd_reverse_recommend_filter() -> None:
    """反向推荐：1/125s 拍成 2s 需要约 8 档，推荐 ND256。"""
    result = _call("nd_long_exposure", {"base_shutter": 0.008, "target_shutter": 2.0})
    assert result["模式"] == "反推所需 ND"
    assert result["推荐滤镜"] == "ND256"
    assert abs(result["所需减光档数"] - 7.97) < 0.1


def test_nd_invalid_inputs() -> None:
    """非法参数应返回 error 字段。"""
    assert "error" in _call("nd_long_exposure", {"base_shutter": -1, "nd_stops": 3})
    assert "error" in _call("nd_long_exposure", {"base_shutter": 1})  # 缺 ND
    assert "error" in _call("nd_long_exposure", {"base_shutter": 1, "nd_filter": "ND999"})
