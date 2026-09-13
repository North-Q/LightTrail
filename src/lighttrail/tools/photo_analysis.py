"""照片分析智能工具（E6-1，PRD D4-01~04）。

设计要点（架构 v2.0 §2.4 智能工具约束）：
- **深度=1 红线**：本工具内部调用一次多模态 LLM（不携带工具），绝不调用
  `registry.dispatch`，防止智能工具递归；
- **走同一个并发边界与配额账本**：客户端由 `default_client_factory()` 按 settings 构造
  （并发开关 + QuotaLedger 同 CLI 链路），依赖经工厂/参数显式注入（B2-3 去模块级全局）；
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
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from lighttrail.config import load_settings
from lighttrail.contracts.models import PhotoAnalysisReport, PhotoReverseReport
from lighttrail.contracts.tool import Confidence, Tool, ToolContext, ToolResult, ToolSpec
from lighttrail.infra.quota import QuotaLedger
from lighttrail.infra.validation import _extract_json
from lighttrail.llm.client import ChatClient
from lighttrail.llm.router import ModelRouter, RouteIntent

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


def default_client_factory() -> ChatClient:
    """按部署配置构造多模态客户端（并发开关 + 配额账本与 CLI 链路一致）。

    本模块**不持有任何全局客户端**：装配根/迁移期收集点调用本工厂，测试注入自己的
    Fake 工厂——依赖经构造或参数显式注入，无 setter 全局态（B2-3 去服务定位器）。

    Returns:
        新的 ChatClient 实例。
    """
    settings = load_settings()
    return ChatClient(
        settings.api_key,
        settings.base_url,
        serial_llm=settings.serial_llm,
        quota=QuotaLedger(warn_threshold=settings.quota_warn_threshold),
    )


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
    except exifread.ExifNotFound:
        # 无 EXIF（截图/部分格式）是常态：静默返回空，不阻塞分析
        return {}
    except Exception as exc:  # noqa: BLE001 - 其它 EXIF 读取异常不阻塞分析
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


_SPEC_ANALYZE_PHOTO = ToolSpec(
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
    capabilities=frozenset(['tools', 'vision']),
    main_field="总体评语",
    confidence=Confidence.LOW,
)

def analyze_photo(
    image_path: str,
    focus: str = "",
    equipment: str = "",
    *,
    client: ChatClient | None = None,
) -> dict:
    """多模态分析一张照片：EXIF + 画面 → 构图/曝光/色彩评价 + 可执行处方。

    Args:
        image_path: 图片文件路径（支持 jpg/png/heic 转码，最长边自动压缩到 1024px）。
        focus: 分析重点（可空），如「星空对焦与噪点」。
        equipment: 器材覆盖（可空，缺省读用户档案）；如「松下 S5M2 + 14mm f/1.8」。
        client: 多模态客户端；None 时用部署级默认（测试注入 Fake）。

    Returns:
        中文结构分析 dict（场景/主体/构图/曝光/色彩/评语/处方/参数建议/EXIF/置信度）。
    """
    active_client = client or default_client_factory()
    gear = equipment.strip() or _read_gear()
    exif = _read_exif(image_path)
    encoded = _encode_image(image_path)
    prompt = _build_analysis_prompt(exif, gear, focus)
    last_error = ""
    raw = ""
    for attempt in range(_MAX_RETRIES + 1):
        raw = _multimodal_call(active_client, encoded, prompt, last_error)
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


def _multimodal_call(client: ChatClient, encoded: str, prompt: str, last_error: str) -> str:
    """一次多模态调用：返回模型文本；校验错误拼进指令（自愈第 N 轮）。

    Args:
        client: 多模态客户端（显式传入，无模块级全局）。
        encoded: 图片 data URL。
        prompt: 分析指令。
        last_error: 上一轮结构化校验错误（拼进指令供模型自愈）。

    Returns:
        模型输出文本。
    """
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
    response = client.chat(messages, model=model, tools=None, temperature=0.2)
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


_REVERSE_SYSTEM = (
    "你是 LightTrail（光迹）的拍摄方案反推专家：从参考照片反推它的拍摄条件"
    "（场景 / 光线方向 / 时段与季节 / 机位特征 / 后期风格），并给出可执行的复刻计划"
    "（在哪 / 什么时候 / 怎么拍），供用户『我也想要这种』时照着去拍。"
)


_SPEC_REVERSE_ENGINEER_PHOTO = ToolSpec(
    name="reverse_engineer_photo",
    description=(
        "照片反推拍摄方案：从一张参考图反推场景/光线方向/时段/机位特征/后期风格，"
        "并给出复刻计划（在哪、什么时候、怎么拍）。"
        "当用户丢一张照片/参考图说『我也想要这种』『这张怎么拍的』时调用。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "image_path": {
                "type": "string",
                "description": "参考图片文件路径，如 D:/photos/reference.jpg",
            },
            "note": {
                "type": "string",
                "description": "补充说明，如『我在杭州，只能周末去』『想要更广的视角』",
                "default": "",
            },
            "equipment": {
                "type": "string",
                "description": "器材覆盖，如『松下 S5M2 + 24-105mm F4』；缺省读用户档案",
                "default": "",
            },
        },
        "required": ["image_path"],
    },
    capabilities=frozenset(['tools', 'vision']),
    main_field="复刻计划",
    confidence=Confidence.LOW,
)

def reverse_engineer_photo(
    image_path: str,
    note: str = "",
    equipment: str = "",
    *,
    client: ChatClient | None = None,
) -> dict:
    """反推一张参考图的拍摄方案并给复刻计划。

    Args:
        image_path: 参考图片文件路径。
        note: 用户补充约束（时间/地点/器材限制等）。
        equipment: 器材覆盖（缺省读用户档案）。
        client: 多模态客户端；None 时用部署级默认（测试注入 Fake）。

    Returns:
        中文结构字段（场景/光向/推断时段/机位特征/后期风格/复刻计划/参数建议/置信度）。

    Raises:
        PhotoError: 图片读取或输出解析失败（重试后）。
    """
    active_client = client or default_client_factory()
    gear = equipment.strip() or _read_gear()
    exif = _read_exif(image_path)
    encoded = _encode_image(image_path)
    prompt = _build_reverse_prompt(exif, gear, note)
    last_error = ""
    for attempt in range(_MAX_RETRIES + 1):
        raw = _multimodal_call(active_client, encoded, prompt, last_error)
        try:
            report = PhotoReverseReport.model_validate_json(_extract_json(raw))
            return {
                "场景": report.scene,
                "光向": report.light_direction,
                "推断时段": report.estimated_time,
                "机位特征": report.site_features,
                "后期风格": report.post_style,
                "复刻计划": report.replication_plan,
                "参数建议": [{"参数": item.name, "值": item.value, "理由": item.reason} for item in report.suggestions],
                "已识别EXIF": exif,
                "置信度": report.confidence,
            }
        except Exception as exc:  # noqa: BLE001 - 校验失败回传模型自愈
            last_error = str(exc)
            if attempt >= _MAX_RETRIES:
                break
    raise PhotoError(f"照片反推输出解析失败（重试 {_MAX_RETRIES} 次）：{last_error}")


def _build_reverse_prompt(exif: dict[str, str], gear: str, note: str) -> str:
    """组装反推指令文本（要求输出 PhotoReverseReport JSON 结构）。"""
    lines = ["请反推这张参考图并只输出一个 JSON 对象（不要任何其他文字），结构如下：", "{",
             "  \"scene\": \"场景识别（如海边日落/城市悬日/银河拱桥）\",",
             "  \"light_direction\": \"光线方向（顺/侧/逆光，日出或日落侧）\",",
             "  \"estimated_time\": \"推断拍摄时段与季节（如夏季傍晚日落前 20 分钟）\",",
             "  \"site_features\": \"机位特征（海拔/前景/朝向/遮挡）\",",
             "  \"post_style\": \"后期风格（色彩/对比/合成）\",",
             "  \"replication_plan\": \"复刻计划：在哪拍、什么时候去、怎么拍（必填三要素）\",",
             "  \"suggestions\": [{\"name\": \"焦段\", \"value\": \"24mm\", \"reason\": \"…\"}],",
             "  \"confidence\": \"high|medium|low\"",
             "}"]
    if exif:
        lines.append(f"参考图 EXIF 参数：{json.dumps(exif, ensure_ascii=False)}")
    if gear:
        lines.append(f"用户器材（复刻计划须按此给参数）：{gear}")
    if note:
        lines.append(f"用户补充约束：{note}")
    return "\n".join(lines)


# ------ 声明式工具（依赖经构造注入；无模块级可写全局）------
class AnalyzePhotoTool:
    """照片分析工具：多模态客户端经构造注入。

    Args:
        client_factory: 客户端工厂（每次调用取一次；测试注入 Fake）。
    """

    spec = _SPEC_ANALYZE_PHOTO

    def __init__(self, client_factory: Callable[[], ChatClient]) -> None:
        self._client_factory = client_factory

    def __call__(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        """执行多模态分析（依赖从工厂取，ctx 未用）。"""
        data = analyze_photo(client=self._client_factory(), **kwargs)
        return ToolResult(content=json.dumps(data, ensure_ascii=False, default=str), data=data)


class ReverseEngineerPhotoTool:
    """照片反推工具：多模态客户端经构造注入。

    Args:
        client_factory: 客户端工厂（每次调用取一次；测试注入 Fake）。
    """

    spec = _SPEC_REVERSE_ENGINEER_PHOTO

    def __init__(self, client_factory: Callable[[], ChatClient]) -> None:
        self._client_factory = client_factory

    def __call__(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        """执行照片反推（依赖从工厂取，ctx 未用）。"""
        data = reverse_engineer_photo(client=self._client_factory(), **kwargs)
        return ToolResult(content=json.dumps(data, ensure_ascii=False, default=str), data=data)


def build_tools(client_factory: Callable[[], ChatClient]) -> tuple[Tool, ...]:
    """构造本模块工具（依赖显式注入；装配根/测试各自构造自己的实例）。

    Args:
        client_factory: 多模态客户端工厂。

    Returns:
        本模块的声明式工具元组。
    """
    return (AnalyzePhotoTool(client_factory), ReverseEngineerPhotoTool(client_factory))


# 默认收集点：工厂惰性构造客户端，导入时不建连接（TODO(B2-4): 由装配根注入工厂）
TOOLS: tuple[Tool, ...] = build_tools(default_client_factory)


__all__ = [
    "AnalyzePhotoTool",
    "PhotoError",
    "ReverseEngineerPhotoTool",
    "analyze_photo",
    "build_tools",
    "default_client_factory",
    "reverse_engineer_photo",
]
