"""B6-1 相机规格知识表的离线回归。"""

from __future__ import annotations

from pathlib import Path

from lighttrail.contracts.knowledge import KnowledgeProvider
from lighttrail.domain.knowledge import CameraSpecsCatalog, FtsKnowledgeProvider

_TABLE = Path(__file__).resolve().parents[1] / "data" / "knowledge" / "camera_specs.yaml"
_SEED = Path(__file__).resolve().parents[1] / "data" / "knowledge" / "seed" / "atmospheric-knowledge.json"


def test_camera_specs_lookup_returns_pixel_pitch_and_source() -> None:
    """S5M2 精确查表返回约 6µm 像素间距与可溯源出处。"""
    catalog = CameraSpecsCatalog(_TABLE)
    result = catalog.lookup("S5M2", table="camera_specs")
    assert result is not None
    assert '"pixel_pitch_um": 5.93' in result.text
    assert "Panasonic" in result.source
    assert result.version == "2026-09"
    assert isinstance(catalog, KnowledgeProvider)


def test_camera_specs_unknown_key_and_wrong_table_are_honest() -> None:
    """未知机型或错误表名不编造默认规格。"""
    catalog = CameraSpecsCatalog(_TABLE)
    assert catalog.lookup("不存在机型") is None
    assert catalog.lookup("S5M2", table="other") is None


def test_fts_trigram_finds_chinese_substring(tmp_path: Path) -> None:
    """查询「散射」命中卷云冰晶知识，且结果带来源。"""
    provider = FtsKnowledgeProvider(tmp_path / "knowledge.db", _SEED)
    hits = provider.search("散射")
    assert len(hits) == 1
    assert hits[0].id == "cirrus-ice-crystals-longwave-scattering"
    assert "卷云冰晶" in hits[0].text
    assert hits[0].source
    assert provider.search("不存在") == []
