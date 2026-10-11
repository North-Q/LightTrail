"""R0-4 生产照片链路：请求器材、专用提示词、结构契约与临时文件。"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from httpx import ASGITransport, AsyncClient
from PIL import Image

from evals.fake_data import FakeDispatcher
from lighttrail.api.app import ApiDeps, create_app
from lighttrail.api.routes import get_request_context
from lighttrail.api.session import SessionManager
from lighttrail.composition import build_application
from lighttrail.config import Settings
from lighttrail.contracts.context import RequestContext
from lighttrail.infra.quota import QuotaLedger
from lighttrail.llm.router import ModelRouter
from lighttrail.tools import photo_analysis

_ANALYSIS = {
    "scene": "海边日落",
    "subject": "礁石",
    "composition": "前景清楚",
    "exposure": "暗部略欠曝",
    "color": "暖色自然",
    "assessment": "可以改善暗部",
    "prescription": "下次日落前用 f/8、ISO 100 拍摄",
    "suggestions": [{"name": "光圈", "value": "f/8", "reason": "保留景深"}],
    "confidence": "medium",
}
_REVERSE = {
    "scene": "海边日落",
    "light_direction": "逆光",
    "estimated_time": "日落前 20 分钟",
    "site_features": "礁石前景",
    "post_style": "暖调",
    "replication_plan": "去海边机位，日落前到达，用 f/8 拍摄",
    "suggestions": [{"name": "光圈", "value": "f/8", "reason": "前景清晰"}],
    "confidence": "medium",
}
_CARD = {
    "conclusion": "日落前到海边机位，使用 f/8 拍摄。",
    "evidence": [{"tool": "analyze_photo", "field": "总体评语", "confidence": "medium"}],
    "confidence": "medium",
    "locations": [{"name": "海边机位", "reason": "档案坐标"}],
}


class _FakeClient:
    """按照片专用系统提示词返回不同 schema，并记录实际模型。"""

    def __init__(self) -> None:
        self.quota = QuotaLedger()
        self.photo_calls: list[dict[str, Any]] = []

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        """同步多模态调用只接受分析或反推专用提示词。"""
        system = messages[0]["content"]
        self.photo_calls.append(
            {"system": system, "prompt": messages[1]["content"][0]["text"], **kwargs}
        )
        payload = _REVERSE if "拍摄方案反推专家" in system else _ANALYSIS
        return {"role": "assistant", "content": json.dumps(payload, ensure_ascii=False)}

    async def acall(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        """异步综合调用返回有效决策卡。"""
        return {"role": "assistant", "content": json.dumps(_CARD, ensure_ascii=False)}

    def queue_position(self) -> int:
        """离线客户端无需排队。"""
        return 0


def _png_bytes() -> bytes:
    """生成可被真实编码器打开的 PNG。"""
    buffer = io.BytesIO()
    Image.new("RGB", (100, 80), (200, 100, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


def _events(body: str) -> list[dict[str, Any]]:
    """提取 SSE 数据事件。"""
    return [json.loads(line[5:].strip()) for line in body.splitlines() if line.startswith("data:")]


async def test_photo_api_uses_scoped_gear_prompts_and_cleans_uploads(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """真实装配下双用户复盘/反推只读自己的器材，文件在调用后删除。"""
    settings = Settings(
        _env_file=None,
        api_key="offline",
        data_dir=str(tmp_path),
        model="tools-fake",
        model_reason="reason-fake",
        reason_thinking=False,
    )
    router = ModelRouter(
        default_model="tools-fake",
        reason_model="reason-fake",
        capability_matrix={
            "vision-fake": {"vision": True},
            "tools-fake": {"tools": True},
            "reason-fake": {"deep": True},
        },
    )
    fake = _FakeClient()
    services = build_application(settings, client=fake, router=router, dispatch=FakeDispatcher())
    users = {
        "north": ("北方相机", "北方镜头"),
        "south": ("南方相机", "南方镜头"),
    }
    for user, (body, lens) in users.items():
        services.memory.update_profile(
            RequestContext(user_id=user),
            {
                "camera_body": body,
                "lenses": [lens],
                "favorite_spots": [
                    {
                        "name": "海边机位",
                        "latitude": 31.2,
                        "longitude": 121.5,
                        "coordinate_system": "WGS84",
                    }
                ],
            },
        )
    app = create_app(ApiDeps(services, SessionManager(tmp_path)))
    uploaded: list[Path] = []
    original_encode = photo_analysis._encode_image

    def observe_encode(image_path: str | Path) -> str:
        """在实际多模态读图时检查文件尚存，并记录清理目标。"""
        path = Path(image_path)
        assert path.is_file()
        uploaded.append(path)
        return original_encode(image_path)

    monkeypatch.setattr(photo_analysis, "_encode_image", observe_encode)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://localhost"
        ) as client:
            for user, (body, lens) in users.items():
                app.dependency_overrides[get_request_context] = lambda user=user: RequestContext(
                    user_id=user
                )
                for route in ("review", "reverse"):
                    response = await client.post(
                        f"/api/photos/{route}",
                        files={"file": ("shot.png", _png_bytes(), "image/png")},
                        data={"focus": "构图", "note": "只能周末", "equipment": ""},
                    )
                    assert response.status_code == 200
                    events = _events(response.text)
                    assert not [event for event in events if event["type"] == "error"]
                    assert any(event["type"] == "card" for event in events)
                    assert events[-1]["type"] == "done"
                    call = fake.photo_calls[-1]
                    assert call["model"] == "vision-fake"
                    assert call["tools"] is None
                    assert body in call["prompt"] and lens in call["prompt"]
                    other = users["south" if user == "north" else "north"]
                    assert other[0] not in call["prompt"]
                    if route == "review":
                        assert "摄影复盘专家" in call["system"]
                        assert "拍摄方案反推专家" not in call["system"]
                    else:
                        assert "拍摄方案反推专家" in call["system"]
                        assert "摄影复盘专家" not in call["system"]
                    assert uploaded and not uploaded[-1].exists()
        assert len(fake.photo_calls) == len(uploaded) == 4
        assert not (tmp_path / "profile.json").exists()
    finally:
        services.close()
