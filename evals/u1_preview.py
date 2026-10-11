"""U1 生产装配离线样本导出：合成收藏与固定时钟，无真实模型或天气调用。"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from httpx import ASGITransport, AsyncClient

from evals.fake_data import CassetteChatClient, FakeDispatcher
from lighttrail.api.app import ApiDeps, create_app
from lighttrail.api.routes import get_request_context
from lighttrail.api.session import SessionManager
from lighttrail.composition import build_application
from lighttrail.config import Settings
from lighttrail.contracts.context import RequestContext

FIXED_NOW = datetime(2026, 10, 6, 8, tzinfo=timezone.utc)


async def export_cards(output: Path, data_root: Path) -> None:
    """经生产 API 导出失败与完整样本，供真实前端组件共用。

    Args:
        output: 样本 JSON 目标路径。
        data_root: 隔离的合成档案目录。
    """
    cards: dict[str, Any] = {}
    for mode in ("failed", "ready"):
        fake = CassetteChatClient(
            {
                mode: {
                    "card": {
                        "content": json.dumps(
                            {
                                "conclusion": "今晚在合成西岸观察火烧云，按窗口出发。",
                                "verdict": "go",
                                "confidence": "high",
                                "evidence": [
                                    {
                                        "tool": "weather_forecast",
                                        "field": "不存在",
                                        "confidence": "high",
                                        "note": "模型自报天气完美",
                                    }
                                ],
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            }
        )
        fake.set_group(mode)
        dispatcher = FakeDispatcher(now=datetime(2026, 10, 6, 7, tzinfo=timezone.utc))
        services = build_application(
            Settings(_env_file=None, data_dir=str(data_root / mode), reason_thinking=False),
            client=fake,
            dispatch=dispatcher
            if mode == "ready"
            else lambda name, args: json.dumps(
                {"error": "离线替身：数据源不可用"}, ensure_ascii=False
            ),
        )
        context = RequestContext(requested_at=FIXED_NOW)
        services.memory.update_profile(
            context,
            {
                "favorite_spots": [
                    {
                        "name": "合成西岸",
                        "latitude": 0.0,
                        "longitude": 0.0,
                        "coordinate_system": "WGS84",
                        "tz_offset": "+08:00",
                    }
                ]
            },
        )
        app = create_app(ApiDeps(services, SessionManager(data_root / mode)))
        app.dependency_overrides[get_request_context] = lambda context=context: context
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://offline"
            ) as client:
                response = await client.post(
                    "/api/decide", json={"request": "今晚火烧云是否值得去"}
                )
            frames = [
                json.loads(line[5:])
                for line in response.text.splitlines()
                if line.startswith("data:")
            ]
            cards[mode] = next(frame["card"] for frame in frames if "card" in frame)
        finally:
            services.close()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(cards, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    """导出可重生成的离线页面样本，数据只落入项目隔离目录。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("frontend/tests/fixtures/u1-cards.json")
    )
    parser.add_argument("--data-root", type=Path, default=Path(".pytest-run-u1/preview"))
    args = parser.parse_args()
    asyncio.run(export_cards(args.output, args.data_root))


if __name__ == "__main__":
    main()
