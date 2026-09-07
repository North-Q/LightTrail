"""照片分析智能工具（E6-1，PRD D4-01~04）。

设计要点（架构 v2.0 §2.4 智能工具约束）：
- **深度=1 红线**：本工具内部调用一次多模态 LLM（不携带工具），绝不调用
  `registry.dispatch`，防止智能工具递归；
- **走同一个并发边界与配额账本**：模块级 ChatClient 由 settings 构造
  （serial_llm + QuotaLedger 同 CLI 链路），测试可经 set_client 注入 Fake；
- **多模态模型按能力路由**：`RouteIntent.VISION` → 能力矩阵解析（默认 plus）；
- **输出走 pydantic schema**（PhotoAnalysisReport，E5-2 契约）——校验失败把错误
  回传模型自愈（≤2 次），仍失败抛 SchemaError 由工具层返回可读错误；
- **图片控 token**：最长边 ≤1024 缩放 + JPEG 压缩 + base64；EXIF 经 exifread 提取；
- **处方结合器材**：优先显式 equipment 参数，缺省从 data/profile.json 档案读取，
  让处方落在具体参数（D4-04）而非泛泛点评。
"""

from __future__ import annotations

import base64
import io
import json
import logging
from pathlib import Path

from PIL import Image, ImageOps

from lighttrail.agent.tools import registry
from lighttrail.config import load_settings
from lighttrail.infra.quota import QuotaLedger
from lighttrail.infra.validation import _extract_json
from lighttrail.llm.client import ChatClient
from lighttrail.llm.router import ModelRouter, RouteIntent
from lighttrail.orchestrator.schemas import PhotoAnalysisReport

logger = logging.getLogger("lighttrail.tools.photo")

# 图片编码上限：最长边像素 / JPEG 质量 / base64 体积告警阈值（KB）
_MAX_EDGE = 1024
_JPEG_QUALITY = 88
_BASE64_WARN_KB = 800

# 输出解析自愈重试次数（首轮之外最多再试 2 次）
_MAX_RETRIES = 2

# EXIF 常用 tag 名（exifread 输出形如 'Image Model'/'EXIF LensModel'）→ 中文键。
# 匹配用「尾部完整 tag 名」，避免 'Model' 误中 'LensModel' 等子串冲突。
_EXIF_FIELDS: dict[str, str] = {
    "Image Make": "相机品牌",
    "Image Model": "相机型号",
    "LensModel": "镜头",
    "FNumber": "光圈",
    "ExposureTime": "快门",
    "ISOSpeedRatings": "ISO",
    "PhotographicSensitivity": "ISO",
    "FocalLength": "焦距",
    "DateTimeOriginal": "拍摄时间",
    "WhiteBalance": "白平衡",
}

_ANALYZE_SYSTEM = (
    "你是 LightTrail（光迹）的摄影复盘专家：从 EXIF 与画面分析构图/曝光/色彩，"
    "并给「下次同样的场景怎么拍」的可执行处方。"
    "处方必须结合用户器材落在具体参数（光圈/快门/ISO/焦段/机位/时机），不要泛泛点评。"
)


def set_client(client: ChatClient | None) -> None:
    """注入/重置默认客户端（测试用 Fake 客户端；None 恢复 settings 懒加载）。

    Args:
        client: ChatClient 实例。
    """
    global _DEFAULT_CLIENT
    _DEFAULT_CLIENT = client


_DEFAULT_CLIENT: ChatClient | None = None


def _get_client() -> ChatClient:
    """返回默认客户端（settings 懒加载，串行 + 配额账本与 CLI 链路一致）。"""
    global _DEFAULT_CLIENT
    if _DEFAULT_CLIENT is None:
        settings = load_settings()
        _DEFAULT_CLIENT = ChatClient(
            settings.api_key,
            settings.base_url,
            serial_llm=settings.serial_llm,
            quota=QuotaLedger(warn_threshold=settings.quota_warn_threshold),
        )
    return _DEFAULT_CLIENT


def _encode_image(image_path: str | Path) -> str:
    """缩放编码为 JPEG base64 data URL（最长边 ≤1024px 控 token）。

    Args:
        image_path: 图片文件路径。

    Returns:
        data:image/jpeg;base64,... 形式字符串。

    Raises:
        PhotoError: 图片无法读取或解码。
    """
    try:
        with Image.open(image_path) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail((_MAX_EDGE, _MAX_EDGE))
            if image.mode != "RGB":
                image = image.convert("RGB")
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=_JPEG_QUALITY)
    except Exception as exc:
        raise PhotoError(f"图片读取失败：{exc}") from exc
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    size_kb = len(encoded) * 3 / 4 / 1024
    if size_kb > _BASE64_WARN_KB:
        logger.warning("图片编码后 %.0fKB，偏大可能挤压上下文", size_kb)
    return f"data:image/jpeg;base64,{encoded}"


def _read_exif(image_path: str | Path) -> dict[str, str]:
    """用 exifread 提取常用拍摄参数（无 EXIF 时返回空 dict，不阻塞分析）。

    Args:
        image_path: 图片文件路径。

    Returns:
        {中文键: 值} 精简 EXIF 摘要。
    """
    import exifread

    try:
        with open(image_path, "rb") as stream:
            tags = exifread.process_file(stream, details=False)
    except Exception as exc:  # noqa: BLE001 - EXIF 读取异常不阻塞分析
        logger.warning("EXIF 读取失败：%s", exc)
        return {}
    result: dict[str, str] = {}
    for tag_name, tag in tags.items():
        for needle, label in _EXIF_FIELDS.items():
            if label in result:
                continue
            if tag_name == needle or tag_name.endswith(" " + needle):
                result[label] = str(tag)
                break
    return result


def _read_gear() -> str:
    """从 data/profile.json 档案读取器材背景（缺省空串，显式 equipment 优先）。"""
    try:
        profile_path = Path(load_settings().data_dir) / "profile.json"
        if not profile_path.exists():
            return ""
        raw = json.loads(profile_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return ""
    parts: list[str] = []
    if raw.get("camera_body"):
        parts.append(str(raw["camera_body"]))
    lenses = raw.get("lenses") or []
    if lenses:
        parts.append("镜头：" + "、".join(str(item) for item in lenses))
    return "；".join(parts)


class PhotoError(Exception):
    """照片分析工具错误（图片读取 / 分析失败）。"""


@registry.tool(
    name="analyze_photo",
    description=(
        "多模态照片分析：读取图片 EXIF + 画面（场景/构图/曝光/色彩），"
        "给出结合用户器材的可执行处方（下次怎么拍的具体参数）。"
        "当用户上传/指定一张照片想复盘『这张怎么改进』或『下次同样场景怎么拍』时调用。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "image_path": {
                "type": "string",
                "description": "图片文件路径，如 D:/photos/2026-09-07_黄昏.jpg（支持 jpg/png）",
            },
            "focus": {
                "type": "string",
                "description": "分析重点，如『星空对焦与噪点』；留空做全面复盘",
                "default": "",
            },
            "equipment": {
                "type": "string",
                "description": "器材覆盖，如『松下 S5M2 + 14mm f/1.8』；缺省读用户档案器材",
                "default": "",
            },
        },
        "required": ["image_path"],
    },
)
def analyze_photo(image_path: str, focus: str = "", equipment: str = "") -> dict:
    """多模态分析一张照片：EXIF + 画面 → 构图/曝光/色彩评价 + 可执行处方。

    Args:
        image_path: 图片文件路径（支持 jpg/png/heic 转码，最长边自动压缩到 1024px）。
        focus: 分析重点（可空），如「星空对焦与噪点」。
        equipment: 器材覆盖（可空，缺省读用户档案）；如「松下 S5M2 + 14mm f/1.8」。

    Returns:
        中文结构分析 dict（场景/主体/构图/曝光/色彩/评语/处方/参数建议/EXIF/置信度）。
    """
    gear = equipment.strip() or _read_gear()
    exif = _read_exif(image_path)
    encoded = _encode_image(image_path)
    prompt = _build_analysis_prompt(exif, gear, focus)
    last_error = ""
    raw = ""
    for attempt in range(_MAX_RETRIES + 1):
        raw = _multimodal_call(encoded, prompt, last_error)
        try:
            report = PhotoAnalysisReport.model_validate_json(_extract_json(raw))
            return _report_to_result(report, exif, len(encoded) * 3 / 4 / 1024)
        except Exception as exc:  # noqa: BLE001 - 校验失败回传模型自愈
            last_error = str(exc)
            if attempt >= _MAX_RETRIES:
                break
    raise PhotoError(f"照片分析输出解析失败（重试 {_MAX_RETRIES} 次）：{last_error}")


def _build_analysis_prompt(exif: dict[str, str], gear: str, focus: str) -> str:
    """组装分析指令文本。"""
    lines = ["请分析这张照片并只输出一个 JSON 对象（不要任何其他文字），结构如下：", "{",
             "  \"scene\": \"场景识别（一句）\",",
             "  \"subject\": \"主体\",",
             "  \"composition\": \"构图评价与问题（一句）\",",
             "  \"exposure\": \"曝光评价（一句）\",",
             "  \"color\": \"色彩评价（一句）\",",
             "  \"assessment\": \"总体评语（一句）\",",
             "  \"prescription\": \"可执行处方：结合器材给出下次具体参数与时机（必填）\",",
             "  \"suggestions\": [{\"name\": \"光圈\", \"value\": \"f/2.8\", \"reason\": \"…\"}],",
             "  \"confidence\": \"high|medium|low\"",
             "}"]
    if exif:
        lines.append(f"已读到的 EXIF 参数：{json.dumps(exif, ensure_ascii=False)}")
    if gear:
        lines.append(f"用户器材（处方须结合）：{gear}")
    if focus:
        lines.append(f"本次分析重点：{focus}")
    return "\n".join(lines)


def _multimodal_call(encoded: str, prompt: str, last_error: str) -> str:
    """一次多模态调用：返回模型文本；校验错误拼进指令（自愈第 N 轮）。"""
    instruction = prompt
    if last_error:
        instruction += f"\n\n上一次输出未通过结构化校验：{last_error}\n请只输出符合结构的完整 JSON。"
    messages = [
        {"role": "system", "content": _ANALYZE_SYSTEM},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": instruction},
                {"type": "image_url", "image_url": {"url": encoded}},
            ],
        },
    ]
    model = RouteIntent.VISION.resolve(ModelRouter())
    response = _get_client().chat(messages, model=model, tools=None, temperature=0.2)
    return response.get("content", "").strip()


def _report_to_result(report: PhotoAnalysisReport, exif: dict[str, str], size_kb: float) -> dict:
    """把校验通过的契约转成面向模型/用户的中文结构字段。"""
    return {
        "场景": report.scene,
        "主体": report.subject,
        "构图评价": report.composition,
        "曝光评价": report.exposure,
        "色彩评价": report.color,
        "总体评语": report.assessment,
        "可执行处方": report.prescription,
        "参数建议": [{"参数": item.name, "值": item.value, "理由": item.reason} for item in report.suggestions],
        "已识别EXIF": exif,
        "置信度": report.confidence,
        "图片摘要": f"编码后约 {size_kb:.0f}KB（最长边≤1024px）",
    }
