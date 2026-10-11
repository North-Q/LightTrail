"""拍摄可执行性反例：实际管线、离线模型及不续期的历史恢复。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from lighttrail.api.app import ApiDeps, create_app
from lighttrail.api.routes import get_request_context
from lighttrail.api.session import SessionManager
from lighttrail.composition import build_application
from lighttrail.config import Settings
from lighttrail.contracts.context import RequestContext
from lighttrail.contracts.models import DecisionCard, Intent
from lighttrail.domain.shooting_constraints import ground_shooting_constraints
from lighttrail.memory import MemoryManager
from lighttrail.orchestrator.context import PipelineContext
from lighttrail.orchestrator.pipelines import LiveDecisionPipeline, PipelineEnv
from lighttrail.tools.astronomy import sun_times

NOW = datetime(2026, 10, 9, 16, tzinfo=timezone.utc)
DAY = "2026-10-10"
QUESTION = (
    "2026-10-10 在合成晨景拍日出前蓝调加日出，持续到日出后。"
    "最早04:00出发，单程一小时通勤，十分钟布置。"
)


def _data(fetched: str = "2026-10-09T23:30:00+08:00") -> dict[str, Any]:
    """构造明确目标和完整晨间天气；坐标仅为固定合成值。"""
    sun = sun_times(31.0, 121.0, DAY, "+08:00")
    return {
        "sun_times": sun,
        "weather_forecast": {
            "位置": "31.0,121.0",
            "时区": "+08:00",
            "获取时间": fetched,
            "有效开始时间": DAY + "T00:00:00+08:00",
            "有效结束时间": "2026-10-11T00:00:00+08:00",
            "每日预报": [
                {
                    "日期": DAY,
                    "平均云量（%）": 50,
                    "最低能见度（km）": 20,
                    "最高降水概率（%）": 0,
                    "最大风速（km/h）": 5,
                }
            ],
            "逐小时预报": [
                {
                    "时间": f"{DAY}T{hour:02d}:00:00+08:00",
                    "cloud_cover": 50,
                    "visibility": 20000,
                    "precipitation_probability": 0,
                    "wind_speed_10m": 5,
                }
                for hour in range(24)
            ],
        },
    }


def _card(**changes: Any) -> dict[str, Any]:
    """使用真实本地天文窗口，专门篡改行程或器材建议。"""
    sun = _data()["sun_times"]
    start = sun["蓝调时刻（晨）"][0]["开始_iso"]
    end = sun["黄金时刻（晨）"][0]["结束_iso"]
    return {
        "conclusion": "可按晨间窗口拍摄。",
        "evidence": [],
        "confidence": "high",
        "verdict": "go",
        "window_start": start,
        "window_end": end,
        "time_window": f"{start} 至 {end}",
        **changes,
    }


def _exercise(
    root: Path,
    model: dict[str, Any],
    question: str = QUESTION,
    fetched: str = "2026-10-09T23:30:00+08:00",
    lenses: list[str] | None = None,
) -> tuple[DecisionCard, PipelineContext, list[str]]:
    """执行实际临场管线并保留综合输入，不调用真实供应商。"""
    memory = MemoryManager(root)
    memory.update_profile(
        {
            "camera_body": "S5M2",
            "lenses": lenses if lenses is not None else ["14mm", "24-105mm F4"],
            "favorite_spots": [
                {
                    "name": "合成晨景",
                    "latitude": 31.0,
                    "longitude": 121.0,
                    "coordinate_system": "WGS84",
                }
            ],
        }
    )
    data = _data(fetched)
    prompts: list[str] = []

    def dispatch(name: str, arguments: str) -> str:
        """仅交付固定天气与真实本地天文结果。"""
        return json.dumps(data.get(name, {"匹配结果": []}), ensure_ascii=False)

    def reason(prompt: str, system: str) -> str:
        """保留实际综合输入并返回恶意或正常离线卡。"""
        prompts.append(prompt)
        return json.dumps(model, ensure_ascii=False)

    ctx = PipelineContext(
        user_request=question,
        intent=Intent(
            subject_type="蓝调",
            time_hint=DAY,
            mode="live",
            solar_event="sunrise",
            solar_window="blue_through_event",
        ),
    )
    card = LiveDecisionPipeline().run(
        ctx,
        PipelineEnv(
            dispatch=dispatch,
            reason=reason,
            memory=memory,
            request=RequestContext(requested_at=NOW, tz_offset="+08:00"),
        ),
    )
    return card, ctx, prompts


def test_impossible_alternative_is_removed(tmp_path: Path) -> None:
    """60分钟通勤加10分钟布置，04:00出发不能在04:32开拍。"""
    card, _, _ = _exercise(tmp_path, _card(alternatives=["04:00出发、04:32先拍蓝调。 "]))
    assert card.verdict == "wait" and card.data_status == "needs_data"
    assert "04:32" not in card.model_dump_json()
    assert "05:10" in card.model_dump_json()


@pytest.mark.parametrize(
    "params",
    [
        [{"name": "快门", "value": "1–4秒", "reason": "用三脚架稳定长曝光"}],
        [{"name": "滤镜", "value": "ND1000", "reason": "减光延长曝光"}],
        [{"name": "镜头", "value": "14mm"}, {"name": "光圈", "value": "f/2.8"}],
    ],
)
def test_unknown_equipment_cannot_support_parameters(
    tmp_path: Path, params: list[dict[str, str]]
) -> None:
    """相机和焦距不证明三脚架、ND或最大光圈已确认。"""
    card, _, _ = _exercise(tmp_path, _card(params=params))
    assert card.verdict == "wait" and card.data_status == "needs_data"
    assert not card.params and card.missing_data


def test_expiry_before_departure_needs_update(tmp_path: Path) -> None:
    """六小时有效期仍为03:36，出发前须更新，禁止延长或额外综合。"""
    card, _, prompts = _exercise(tmp_path, _card(), fetched="2026-10-09T21:36:28+08:00")
    assert card.valid_until == "2026-10-10T03:36:28+08:00"
    assert "出发前" in card.model_dump_json() and "更新" in card.model_dump_json()
    assert card.verdict == "wait" and len(prompts) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"conclusion": "04:00出发、04:32先拍。"},
        {"conclusion": "04:00出发、04:32先拍。", "degraded": "模型自报标记"},
        {"alternatives": ["04:00出发，05:00到达，05:09开拍。"]},
        {"alternatives": ["04:10出发，05:00到达，05:10开拍。"]},
        {"alternatives": ["04:00出发04:32开拍。"]},
        {"alternatives": ["四点出发，四点三十二分开拍。"]},
        {"locations": [{"name": "合成晨景", "reason": "04:32开拍。"}]},
        {"params": [{"name": "开拍", "value": "04:32"}]},
    ],
)
def test_all_visible_schedule_claims_are_checked(tmp_path: Path, changes: dict[str, Any]) -> None:
    """结论、备选、参数和机位理由均不能留下不可执行行程。"""
    card, _, _ = _exercise(tmp_path, _card(**changes))
    assert card.verdict == "wait" and card.data_status == "needs_data"
    assert any("行程" in reason for reason in card.missing_data)
    assert "04:32" not in card.model_dump_json() and "四点三十二" not in card.model_dump_json()


def test_valid_schedule_and_grounded_prompt(tmp_path: Path) -> None:
    """05:10后开拍可行，前提在模型发送前固定，原完整数据不被摘要替代。"""
    card, ctx, prompts = _exercise(
        tmp_path, _card(alternatives=["04:00出发，05:00到达，05:10布置完毕后开拍。 "])
    )
    assert card.verdict == "go" and card.data_status == "ready"
    assert ctx.data["拍摄前提"]["单程通勤分钟"] == 60
    assert ctx.data["拍摄前提"]["布置分钟"] == 10
    assert ctx.data["拍摄前提"]["最早开拍"] == DAY + "T05:10:00+08:00"
    assert ctx.data["拍摄前提"]["三脚架"] is None
    assert ctx.data["拍摄前提"]["ND"] is None
    assert ctx.data["拍摄前提"]["镜头最大光圈"]["14mm"] is None
    assert '"拍摄前提"' in prompts[0] and len(ctx.data["weather_forecast"]["逐小时预报"]) == 24


@pytest.mark.parametrize(
    "declaration", ["三脚架未确认", "没有三脚架", "可能带三脚架", "如果有三脚架"]
)
def test_uncertain_or_negative_tripod_is_not_available(tmp_path: Path, declaration: str) -> None:
    """未知、否定和假设声明均不能成为已确认器材。"""
    card, _, _ = _exercise(
        tmp_path, _card(params=[{"name": "快门", "value": "4秒"}]), QUESTION + declaration
    )
    assert card.verdict == "wait" and not card.params


def test_confirmed_equipment_allows_supported_parameters(tmp_path: Path) -> None:
    """本人明确确认三脚架、ND型号和14mm光圈后，合法参数不被误拦。"""
    params = [
        {"name": "镜头", "value": "14mm"},
        {"name": "光圈", "value": "f/2.8"},
        {"name": "快门", "value": "4秒"},
        {"name": "滤镜", "value": "ND8"},
    ]
    card, _, _ = _exercise(
        tmp_path, _card(params=params), QUESTION + "确认携带三脚架，带ND8，14mm最大光圈f/2.8。"
    )
    assert card.verdict == "go" and len(card.params) == 4


@pytest.mark.parametrize(
    "declaration",
    [
        "如果14mm最大光圈为f2.8，就使用f2.8。",
        "若14mm最大光圈为f2.8，就使用f2.8。",
        "假设14mm最大光圈是f/2.8。",
        "14mm最大光圈可能为f2.8。",
        "14mm最大光圈为f2.8未确认。",
        "14mm最大光圈不是f2.8。",
        "14mm最大光圈并非f/2.8。",
        "14mm最大光圈不为f2.8。",
        "不能确认14mm最大光圈为f2.8。",
        "14mm最大光圈f2.8，14mm最大光圈f4。",
        "14mm最大光圈f4，14mm最大光圈f2.8。",
        "14mm最大光圈f2.8，若14mm最大光圈为f2.8。",
        "若14mm最大光圈为f2.8，14mm最大光圈f2.8。",
        "14mm最大光圈f2.8，14mm最大光圈不是f2.8。",
        "14mm最大光圈不是f2.8，14mm最大光圈f2.8。",
    ],
)
@pytest.mark.parametrize("profile_aperture", [False, True])
def test_aperture_nonassertions_remain_unknown_in_live_pipeline(
    tmp_path: Path, declaration: str, profile_aperture: bool
) -> None:
    """条件、否定与冲突规格不能在综合输入或最终临场卡中成为已确认事实。"""
    params = [{"name": "镜头", "value": "14mm"}, {"name": "光圈", "value": "f/2.8"}]
    card, ctx, prompts = _exercise(
        tmp_path,
        _card(params=params),
        QUESTION + declaration,
        lenses=["14mm F2.8" if profile_aperture else "14mm", "24-105mm F4"],
    )
    assert ctx.data["拍摄前提"]["镜头最大光圈"]["14mm"] is None
    assert '"14mm": null' in prompts[0] and len(prompts) == 1
    assert card.verdict == "wait" and card.data_status == "needs_data"
    assert card.confidence == "low" and not card.params
    assert any("最大光圈未确认" in reason for reason in card.missing_data)


@pytest.mark.parametrize(
    "declaration",
    [
        "已确认14mm最大光圈为f2.8。",
        "14mm最大光圈是f/2.8。",
        "14mm最大光圈：F2.8。",
        "14mm最大光圈f2.8，14mm最大光圈为f/2.8。",
    ],
)
def test_explicit_aperture_confirmation_stays_ready(tmp_path: Path, declaration: str) -> None:
    """明确肯定及重复一致声明保留，其他焦段规格不受本次修复影响。"""
    params = [{"name": "镜头", "value": "14mm"}, {"name": "光圈", "value": "f/2.8"}]
    card, ctx, prompts = _exercise(tmp_path, _card(params=params), QUESTION + declaration)
    assert ctx.data["拍摄前提"]["镜头最大光圈"] == {"14mm": 2.8, "24-105mm": 4.0}
    assert '"14mm": 2.8' in prompts[0] and len(prompts) == 1
    assert card.verdict == "go" and card.data_status == "ready" and len(card.params) == 2


@pytest.mark.parametrize(
    "params, declaration",
    [
        ([{"name": "滤镜", "value": "ND1000"}], "带ND8"),
        ([{"name": "镜头", "value": "24-105mm"}, {"name": "光圈", "value": "f/2.8"}], ""),
        (
            [{"name": "镜头", "value": "14mm"}, {"name": "光圈", "value": "f/2.8"}],
            "14mm最大光圈f/2.8未确认",
        ),
    ],
)
def test_wrong_equipment_model_or_aperture_is_rejected(
    tmp_path: Path, params: list[dict[str, str]], declaration: str
) -> None:
    """拥有另一款ND、另一个镜头或未确认参数不能补齐本建议前提。"""
    card, _, _ = _exercise(tmp_path, _card(params=params), QUESTION + declaration)
    assert card.verdict == "wait" and not card.params


def test_missing_setup_is_not_zero(tmp_path: Path) -> None:
    """只知道通勤时长不能默认布置为零。"""
    card, _, _ = _exercise(tmp_path, _card(), QUESTION.replace("十分钟布置。", ""))
    assert card.verdict == "wait"
    assert "布置时间未确认" in str(card.missing_data)


def test_late_earliest_departure_cannot_shift_window(tmp_path: Path) -> None:
    """最早05:00出发赶不上蓝调，不得偷偷移动已核验窗口。"""
    card, _, _ = _exercise(tmp_path, _card(), QUESTION.replace("04:00", "05:00"))
    assert card.verdict == "wait" and not card.window_start
    assert any("主拍摄窗口" in reason for reason in card.missing_data)


def test_chinese_duration_and_conflict_grounding() -> None:
    """中文、复合时长和冲突保持可核验，避免相互覆盖。"""
    facts = ground_shooting_constraints(
        "最早四点出发，通勤一小时十分钟，布置半小时", DAY, "+08:00", []
    )
    assert facts["单程通勤分钟"] == 70 and facts["布置分钟"] == 30
    assert facts["最早开拍"] == DAY + "T05:40:00+08:00"
    conflict = ground_shooting_constraints(
        "最早04:00出发，通勤60分钟或90分钟，布置10分钟", DAY, "+08:00", []
    )
    assert conflict["待确认"] or conflict["单程通勤分钟"] is None


def test_bare_aperture_number_cannot_bypass_unknown_lens(tmp_path: Path) -> None:
    """光圈值未写f前缀也须核验镜头，不从焦距猜测f/2.8。"""
    card, _, _ = _exercise(
        tmp_path,
        _card(params=[{"name": "镜头", "value": "14mm"}, {"name": "光圈", "value": "2.8"}]),
    )
    assert card.verdict == "wait" and not card.params


def test_zoom_aperture_applies_only_to_covered_focal(tmp_path: Path) -> None:
    """24–105mm F4可支撑24mm/f8，不支撑独立14mm或f2.8。"""
    card, _, _ = _exercise(
        tmp_path,
        _card(params=[{"name": "焦距", "value": "24mm"}, {"name": "光圈", "value": "f/8"}]),
    )
    assert card.verdict == "go" and len(card.params) == 2


def test_main_schedule_is_checked_across_fields(tmp_path: Path) -> None:
    """结论给04:50出发时，主窗口不能仍在05:30开拍。"""
    card, _, _ = _exercise(tmp_path, _card(conclusion="04:50出发。"))
    assert card.verdict == "wait" and not card.window_start


def test_uncertain_or_variable_lens_aperture_is_not_confirmed() -> None:
    """本轮未知声明覆盖旧档案；变光圈范围和待确认规格不猜测可用最大光圈。"""
    facts = ground_shooting_constraints(
        "14mm最大光圈未确认", DAY, "+08:00", ["14mm F2.8", "70-300mm F4.5-5.6", "24mm F2.8待确认"]
    )
    assert all(value is None for value in facts["镜头最大光圈"].values())


@pytest.mark.parametrize("old", [False, True])
async def test_production_api_save_and_restore_do_not_refresh(tmp_path: Path, old: bool) -> None:
    """生产API保存受约束新卡；已有错误卡只读原样恢复，零模型工具和写入。"""
    memory = MemoryManager(tmp_path)
    memory.update_profile(
        {
            "camera_body": "S5M2",
            "lenses": ["14mm"],
            "favorite_spots": [
                {
                    "name": "合成晨景",
                    "latitude": 31.0,
                    "longitude": 121.0,
                    "coordinate_system": "WGS84",
                }
            ],
        }
    )
    calls: list[str] = []
    output = (
        _card(alternatives=["04:00出发、04:32先拍。"])
        if not old
        else _card(alternatives=["04:00出发、04:32先拍。"], valid_until=DAY + "T03:36:28+08:00")
    )

    class Model:
        """记录合成模型调用，不使用供应商客户端。"""

        def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
            """返回指定合成卡。"""
            calls.append("model")
            return {"role": "assistant", "content": json.dumps(output, ensure_ascii=False)}

        async def acall(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
            """共用调用计数。"""
            return self.chat(messages, **kwargs)

        def queue_position(self) -> int:
            """替身无需排队。"""
            return 0

    def dispatch(name: str, arguments: str) -> str:
        """只返回确定的合成工具结果。"""
        calls.append(name)
        return json.dumps(_data("2026-10-09T21:36:28+08:00").get(name, {}), ensure_ascii=False)

    def assemble(at: datetime) -> tuple[Any, Any, SessionManager]:
        """重建真实应用，使用同一隔离目录与计数替身。"""
        services = build_application(
            Settings(_env_file=None, data_dir=str(tmp_path), reason_thinking=False),
            client=Model(),
            memory=memory,
            dispatch=dispatch,
        )
        sessions = SessionManager(tmp_path)
        app = create_app(ApiDeps(services, sessions))
        app.dependency_overrides[get_request_context] = lambda: RequestContext(
            requested_at=at, tz_offset="+08:00"
        )
        return app, services, sessions

    app, services, sessions = assemble(NOW)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://localhost"
        ) as api:
            if old:
                session = sessions.create(user_id="_local")
                result = sessions.save_result(
                    session,
                    kind="decide",
                    request={"request": QUESTION},
                    card=DecisionCard.model_validate(output),
                )
                url = f"/api/sessions/{session.session_id}/results/{result.result_id}"
            else:
                response = await api.post("/api/decide", json={"request": QUESTION})
                frames = [
                    json.loads(line[6:])
                    for line in response.text.splitlines()
                    if line.startswith("data: ")
                ]
                done = frames[-1]
                assert done["saved"] is True
                assert calls.count("model") == 1
                url = f"/api/sessions/{done['session_id']}/results/{done['result_id']}"
            response = await api.get(url)
            assert response.status_code == 200
            original = response.json()
            assert original["card"]["valid_until"] == DAY + "T03:36:28+08:00"
            assert ("04:32" in str(original["card"])) is old
    finally:
        services.close()
    before = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (tmp_path / "sessions").glob("*.json")
    }
    calls.clear()
    app, services, _ = assemble(NOW + timedelta(days=2))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://localhost"
        ) as api:
            restored = await api.get(url)
            assert restored.status_code == 200 and restored.json() == original
            assert calls == []
    finally:
        services.close()
    assert before == {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (tmp_path / "sessions").glob("*.json")
    }
    (tmp_path / "restore-summary.json").write_text(
        json.dumps(
            {
                "old": old,
                "restore_calls": len(calls),
                "session_files_unchanged": True,
                "original_result": original,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf8",
    )
