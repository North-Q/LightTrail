"""照片分析智能工具（E6-1）的 pytest 用例。

验证（全部离线，不触网、不用真实 Key）：
- 图片缩放编码：最长边 ≤1024px、base64 体积受控、data URL 前缀正确；
- EXIF 提取：无 EXIF 返回空 dict 不阻塞；JPEG 相机信息正确读出且不误中镜头字段；
- analyze_photo：多模态消息含 image_url 与 focus、model 走 VISION 路由（默认 plus）、tools=None；
- 输出契约自愈：首次非法 JSON → 重试携带纠错指令；连续失败抛 PhotoError；
- 器材背景：无显式 equipment 时读档案注入处方 prompt；显式优先；
- 深度=1 红线：模块不调用 registry.dispatch。
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import lighttrail.tools.photo_analysis as photo
from lighttrail.agent.tools import registry
from lighttrail.tools.photo_analysis import PhotoError, analyze_photo


@pytest.fixture()
def workdir() -> Path:
    """自建临时目录（pytest tmp_path 受沙箱 ACL 限制）。"""
    directory = Path(tempfile.mkdtemp(prefix="lt_photo_"))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


class FakeChatClient:
    """按脚本预置响应的伪多模态客户端（记录调用参数）。"""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def chat(self, messages, **kwargs) -> dict:
        self.calls.append({"messages": messages, "kwargs": kwargs})
        return {"role": "assistant", "content": self._responses.pop(0)}


def _make_png(path: Path, size: tuple[int, int] = (300, 200)) -> None:
    """生成无 EXIF 的纯色/渐变 PNG。"""
    from PIL import Image

    image = Image.new("RGB", size, (128, 64, 32))
    image.save(path, format="PNG")


def _make_jpeg_with_camera(path: Path) -> None:
    """生成带 Make/Model 相机信息的 JPEG。"""
    from PIL import Image

    exif = Image.Exif()
    exif[271] = "Panasonic"
    exif[272] = "DC-S5M2"
    image = Image.new("RGB", (160, 120), (200, 100, 50))
    image.save(path, format="JPEG", exif=exif)


# ------ 图片编码 ------
def test_encode_image_scales_and_is_compact(workdir: Path) -> None:
    """大图缩放后最长边 ≤1024，base64 体积受控且为 JPEG data URL。"""
    from PIL import Image

    big = workdir / "big.jpg"
    Image.new("RGB", (2400, 1600), (10, 20, 30)).save(big, format="JPEG")
    data_url = photo._encode_image(big)
    assert data_url.startswith("data:image/jpeg;base64,")
    size_kb = len(data_url) * 3 / 4 / 1024
    assert size_kb < 500  # 远小于上下文限制
    # 解码回看最长边
    import base64 as b64
    import io as _io

    from PIL import Image as _Img

    payload = data_url.split(",", 1)[1]
    with _Img.open(_io.BytesIO(b64.b64decode(payload))) as decoded:
        assert max(decoded.size) <= 1024


def test_encode_image_missing_raises(workdir: Path) -> None:
    """图片不存在抛 PhotoError。"""
    with pytest.raises(PhotoError):
        photo._encode_image(workdir / "no_such.jpg")


# ------ EXIF ------
def test_read_exif_empty_without_exif(workdir: Path) -> None:
    """无 EXIF（PNG）返回空 dict，不抛错。"""
    png = workdir / "a.png"
    _make_png(png)
    assert photo._read_exif(png) == {}


def test_read_exif_camera_model_not_mixed_with_lens(workdir: Path) -> None:
    """JPEG 相机信息读出，'Model' 不与镜头字段混淆。"""
    jpg = workdir / "cam.jpg"
    _make_jpeg_with_camera(jpg)
    exif = photo._read_exif(jpg)
    assert exif.get("相机品牌") == "Panasonic"
    assert exif.get("相机型号") == "DC-S5M2"
    assert "镜头" not in exif  # 无 LensModel 时不误中


# ------ analyze_photo ------
_OK_JSON = json.dumps(
    {
        "scene": "城市日落",
        "subject": "外滩天际线",
        "composition": "水平线居中，前景略空",
        "exposure": "整体曝光准确，暗部欠 1 档",
        "color": "暖调饱和",
        "assessment": "构图有潜力，曝光需注意暗部",
        "prescription": "下次 f/8、ISO 100、1/60s，用 24mm 焦段提前 20 分钟到机位",
        "suggestions": [{"name": "光圈", "value": "f/8", "reason": "景深更实"}],
        "confidence": "medium",
    },
    ensure_ascii=False,
)


def test_analyze_photo_sends_image_and_uses_vision_route(workdir: Path, monkeypatch) -> None:
    """多模态消息含 image_url 与 focus；模型走 VISION 能力路由（默认 plus）；tools=None。"""
    png = workdir / "shot.png"
    _make_png(png)
    fake = FakeChatClient([_OK_JSON])
    monkeypatch.setattr(photo, "_get_client", lambda: fake)
    result = analyze_photo(str(png), focus="看暗部细节")
    assert result["可执行处方"].startswith("下次 f/8")
    assert result["置信度"] == "medium"
    call = fake.calls[0]
    assert call["kwargs"]["model"] == "ecnu-plus"  # VISION 路由默认
    assert call["kwargs"]["tools"] is None
    content = call["messages"][-1]["content"]
    assert content[0]["type"] == "text"
    assert "看暗部细节" in content[0]["text"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_analyze_photo_retries_with_correction_then_succeeds(workdir: Path, monkeypatch) -> None:
    """首次非法 JSON → 重试（带纠错指令）→ 成功，共 2 次调用。"""
    png = workdir / "shot.png"
    _make_png(png)
    fake = FakeChatClient(["这不是 JSON", _OK_JSON])
    monkeypatch.setattr(photo, "_get_client", lambda: fake)
    result = analyze_photo(str(png))
    assert len(fake.calls) == 2
    second_text = fake.calls[1]["messages"][-1]["content"][0]["text"]
    assert "未通过结构化校验" in second_text
    assert result["总体评语"].startswith("构图有潜力")


def test_analyze_photo_fails_after_exhausted_retries(workdir: Path, monkeypatch) -> None:
    """连续非法输出 → 抛 PhotoError（重试 2 次 = 共 3 次调用）。"""
    png = workdir / "shot.png"
    _make_png(png)
    fake = FakeChatClient(["坏", "坏", "坏"])
    monkeypatch.setattr(photo, "_get_client", lambda: fake)
    with pytest.raises(PhotoError):
        analyze_photo(str(png))
    assert len(fake.calls) == 3


def test_analyze_photo_gear_from_profile(workdir: Path, monkeypatch) -> None:
    """无显式 equipment 时读取档案器材注入处方 prompt。"""
    (workdir / "profile.json").write_text(
        json.dumps({"camera_body": "松下 S5M2（全画幅）", "lenses": ["24-105mm F4", "契卡 14mm 定焦"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(photo, "load_settings", lambda: SimpleNamespace(data_dir=str(workdir)))
    png = workdir / "shot.png"
    _make_png(png)
    fake = FakeChatClient([_OK_JSON])
    monkeypatch.setattr(photo, "_get_client", lambda: fake)
    analyze_photo(str(png))
    prompt_text = fake.calls[0]["messages"][-1]["content"][0]["text"]
    assert "松下 S5M2" in prompt_text
    assert "24-105mm F4" in prompt_text


def test_analyze_photo_explicit_equipment_wins(workdir: Path, monkeypatch) -> None:
    """显式 equipment 覆盖档案器材。"""
    fake = FakeChatClient([_OK_JSON])
    monkeypatch.setattr(photo, "_get_client", lambda: fake)
    png = workdir / "shot.png"
    _make_png(png)
    analyze_photo(str(png), equipment="索尼 A7M4 + 16-35mm")
    text = fake.calls[0]["messages"][-1]["content"][0]["text"]
    assert "索尼 A7M4" in text
    assert "松下" not in text


# ------ 注册与深度=1 红线 ------
def test_analyze_photo_registered() -> None:
    """analyze_photo 已注册为工具。"""
    names = {s["function"]["name"] for s in registry.to_openai_schema()}
    assert "analyze_photo" in names


def test_no_registry_dispatch_inside_photo_tool() -> None:
    """深度=1 红线：photo_analysis 模块不得调用 registry.dispatch。"""
    source = Path(photo.__file__).read_text(encoding="utf-8")
    # 只禁止「调用形式」（带括号）；docstring 中提及红线说明不视为调用
    assert "registry.dispatch(" not in source


def test_e6_golden_cases_json_valid() -> None:
    """E6 黄金用例集文件：≥8 条、id 唯一、字段完整（供 E8-1 增量消费）。"""
    import json as _json
    from pathlib import Path as _Path

    golden_path = _Path(__file__).resolve().parents[1] / "evals" / "golden" / "E6-photo-cases.json"
    raw = _json.loads(golden_path.read_text(encoding="utf-8"))
    cases = raw["cases"]
    assert len(cases) >= 8
    ids = [case["id"] for case in cases]
    assert len(ids) == len(set(ids))
    for case in cases:
        assert case["type"] in {"photo_analyze", "reverse", "review"}
        assert case["request"] and case["expect"]

