"""U2-2 数值、缺失事件及生产规格接线的离线回归。"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from evals.fake_data import CassetteChatClient
from lighttrail.composition import build_application
from lighttrail.config import Settings
from lighttrail.contracts.context import RequestContext
from lighttrail.contracts.models import DecisionCard
from lighttrail.orchestrator.evidence import qualify_card
from lighttrail.orchestrator.pipelines import _diff_exif_vs_plan, _values_equivalent
from lighttrail.tools import astronomy, basic, site_match
from lighttrail.tools.exposure import equivalent_exposure


@pytest.mark.parametrize("camera", ["松下 S5M2", "Panasonic DC-S5M2"])
def test_production_camera_completion(tmp_path: Path, camera: str) -> None:
    """不注入知识替身，生产装配、请求上下文和注册表自动补全 S5M2。"""
    client = CassetteChatClient({})
    services = build_application(Settings(_env_file=None, data_dir=str(tmp_path)), client=client)
    request = RequestContext(user_id="synthetic")
    try:
        services.memory.update_profile(request, {"camera_body": camera})
        scope = services.create_scope(request)
        result = json.loads(
            services.registry.dispatch(
                "star_shutter_rule", '{"focal_length":14,"aperture":2.8}', scope.tool_context
            )
        )
        assert result["NPF 法则最大快门（秒）"] == 19.7
        assert result["像素间距（微米）"] == 5.93
        assert "S5M2" in result["像素间距来源"]
        assert result["规格版本"] == "2026-09"
        assert "6000" in result["像素间距估算说明"]
        assert services.knowledge.search("散射")
        assert client.calls == []
    finally:
        services.close()


@pytest.mark.parametrize("camera", ["未知相机", "S5M2-未知变体", "", "尼康 FE"])
def test_unknown_camera_never_guesses(tmp_path: Path, camera: str) -> None:
    """未知或未填机型明确缺规格，显式间距仍可覆盖。"""
    services = build_application(
        Settings(_env_file=None, data_dir=str(tmp_path)), client=CassetteChatClient({})
    )
    request = RequestContext(user_id="synthetic")
    try:
        services.memory.update_profile(request, {"camera_body": camera})
        scope = services.create_scope(request)
        args = {"focal_length": 14, "aperture": 2.8}
        result = json.loads(
            services.registry.dispatch("star_shutter_rule", json.dumps(args), scope.tool_context)
        )
        assert result["NPF 法则最大快门（秒）"] is None
        assert "规格" in result["缺失规格原因"]
        args["pixel_pitch"] = 6.0
        explicit = json.loads(
            services.registry.dispatch("star_shutter_rule", json.dumps(args), scope.tool_context)
        )
        assert explicit["NPF 法则最大快门（秒）"] == 19.9
        assert explicit["像素间距来源"] == "调用参数（未核验机型规格）"
    finally:
        services.close()


@pytest.mark.parametrize(
    "seconds,ev,expected", [(0.004, 0, 0.004), (0.004, -1, 0.002), (1 / 8000, 1, 1 / 4000)]
)
def test_short_shutter_retains_value(seconds: float, ev: float, expected: float) -> None:
    """连续曝光计算保留短快门数值，而非显示小数位。"""
    result = equivalent_exposure(8, seconds, 100, ev)
    assert result["等效方案"]["快门优先"]["shutter_speed"] == pytest.approx(expected)


@pytest.mark.parametrize(
    "left,right,equal",
    [
        ("1/125s", "0.008s", True),
        ("8ms", "0.008秒", True),
        ("1/125s", "0.00800001s", True),
        ("f/8", "8.0", True),
        ("ISO400", "400", True),
        ("1/125s", "1/120s", False),
        ("1/8000s", "1/4000s", False),
        ("8s", "8ms", False),
        ("f/8", "8s", False),
        ("8mm", "8s", False),
        ("1/0s", "0s", False),
        ("abc 8", "8", False),
    ],
)
def test_parameter_equivalence(left: str, right: str, equal: bool) -> None:
    """单位、分数和容差共同决定等价性，不从任意文本摘数字。"""
    assert _values_equivalent(left, right) is equal


def test_review_has_no_spurious_shutter_difference() -> None:
    """实际复盘对账入口接受分数与十进制等价快门。"""
    assert _diff_exif_vs_plan({"快门": "0.008s"}, [{"参数": "快门", "值": "1/125s"}]) == []


@pytest.mark.parametrize(
    "offset,hours,iso",
    [
        ("+08:00", 8, "2026-10-07T07:30:00+08:00"),
        ("-03:30", -3.5, "2026-10-06T20:00:00-03:30"),
        ("UTC", 0, "2026-10-06T23:30:00+00:00"),
    ],
)
def test_time_preserves_offset_and_instant(
    monkeypatch: pytest.MonkeyPatch, offset: str, hours: float, iso: str
) -> None:
    """固定绝对时刻，校验偏移、跨日与反向 UTC 换算。"""
    instant = datetime(2026, 10, 6, 23, 30, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        """当前时间的确定性替身。"""

        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            """返回固定瞬间的指定时区表示。"""
            return instant.astimezone(tz)

    monkeypatch.setattr(basic, "datetime", FixedDateTime)
    moment = datetime.fromisoformat(basic.get_current_time(offset)["iso"])
    assert moment.isoformat() == iso
    assert moment.utcoffset() == timedelta(hours=hours)
    assert moment.astimezone(timezone.utc) == instant


@pytest.mark.parametrize("offset", ["+08:99", "+24:00", "Asia/Shanghai", ""])
def test_invalid_time_offset_is_explicit(offset: str) -> None:
    """非法偏移不能静默返回 UTC 并冒充用户要求的时区。"""
    assert "error" in basic.get_current_time(offset)


@pytest.mark.parametrize(
    "phase,percent,name", [(0, 0, "新月"), (7, 50, "上弦月"), (14, 100, "满月"), (21, 50, "下弦月")]
)
def test_phase_index_is_not_age(
    monkeypatch: pytest.MonkeyPatch, phase: float, percent: int, name: str
) -> None:
    """Astral 的 0/7/14/21 索引须映射朔/上弦/望/下弦。"""
    monkeypatch.setattr(astronomy.moon, "phase", lambda day: phase)
    result = astronomy.moon_phase("2026-08-16")
    assert result["相位索引（0-28）"] == phase
    assert result["月龄（天）"] is None
    assert result["估算月龄（天）"] == round(phase / 28 * 29.5306, 1)
    assert result["照亮比例（%）"] == percent
    assert result["月相名称"] == name
    assert "估算" in result["提示"]


@pytest.mark.parametrize(
    "offset,utc_day", [("+14:00", "2026-08-15"), ("-12:00", "2026-08-17"), ("+08:00", "2026-08-16")]
)
def test_moon_uses_explicit_utc_date(
    monkeypatch: pytest.MonkeyPatch, offset: str, utc_day: str
) -> None:
    """本地午间映射到 UTC 日期，跨时区不是忽略偏移。"""
    calls: list[date] = []

    def phase(day: date) -> float:
        """记录 Astral 实际采样日期。"""
        calls.append(day)
        return 7.0

    monkeypatch.setattr(astronomy.moon, "phase", phase)
    result = astronomy.moon_phase("2026-08-16", offset)
    assert calls == [date.fromisoformat(utc_day)]
    assert result["日期"] == "2026-08-16"
    assert result["计算日期（UTC）"] == utc_day
    assert result["参考时刻"].endswith(offset)


def test_london_summer_keeps_sunset() -> None:
    """伦敦夏至无天文曙暮光，但正常日出日落仍完整可用。"""
    result = astronomy.sun_times(51.5074, -0.1278, "2026-06-21", "+01:00")
    assert result["日出"] == "04:43"
    assert result["日落"] == "21:21"
    assert result["民用晨光始"] is not None
    assert result["天文晨光始"] is None
    assert result["天文昏影终"] is None
    assert result["事件状态"]["天文昏影终"]["状态"] == "事件不存在"
    assert result["事件状态"]["日落"]["状态"] == "可用"


@pytest.mark.parametrize("day,reason", [("2026-06-21", "above"), ("2026-12-21", "below")])
def test_polar_events_absent_individually(day: str, reason: str) -> None:
    """朗伊尔城极昼/极夜不伪造日出日落，也不丢失正午。"""
    result = astronomy.sun_times(78.2232, 15.6469, day, "+01:00")
    assert result["日出"] is None and result["日落"] is None
    assert result["昼长（时分）"] is None
    assert result["正午"] is not None
    assert result["事件状态"]["日落"]["状态"] == "事件不存在"
    assert reason in result["事件状态"]["日落"]["原因"]
    sites = [{"名称": "合成极地机位", "纬度": 78.2232, "经度": 15.6469, "题材": "日落"}]
    assert site_match.match_sites(sites, day, "+01:00")["排序结果"][0]["匹配度"] is None


def test_calculation_failure_is_not_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """非物理缺失异常单独报告计算失败，其余事件继续返回。"""

    def fail(*args: object, **kwargs: object) -> datetime:
        """模拟计算实现异常。"""
        raise ValueError("synthetic calculation broken")

    monkeypatch.setattr(astronomy.sun, "sunrise", fail)
    result = astronomy.sun_times(31.23, 121.47, "2026-08-16")
    assert result["日出"] is None
    assert result["日落"] == "18:36"
    assert result["事件状态"]["日出"]["状态"] == "计算失败"


def test_zero_illumination_not_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    """消费端保留合法的 0%，不能用真假值默认成 50%。"""
    monkeypatch.setattr(site_match, "moon_phase", lambda *args: {"照亮比例（%）": 0})
    monkeypatch.setattr(
        site_match,
        "galaxy_visibility",
        lambda *args, **kwargs: {
            "可见窗口": [{"开始": "20:00", "结束": "23:30"}],
            "整夜最高高度角（度）": 30,
        },
    )
    entry = site_match._evaluate_galaxy_site("合成", 31.23, 121.47, "银河", "2026-08-16", "+08:00")
    assert entry["关键天象"]["月光（%）"] == 0
    assert entry["匹配度"] == 5


@pytest.mark.parametrize(
    "latitude,longitude,day,ready",
    [
        (51.5074, -0.1278, "2026-06-21", True),
        (78.2232, 15.6469, "2026-06-21", False),
        (78.2232, 15.6469, "2026-12-21", False),
    ],
)
def test_partial_sun_results_preserve_u1_qualification(
    latitude: float, longitude: float, day: str, ready: bool
) -> None:
    """新部分太阳结果经过原 U1 护栏：有效日落可用，缺日落不能出发。"""
    now = datetime.fromisoformat(f"{day}T07:00:00+00:00")
    tomorrow = date.fromisoformat(day) + timedelta(days=1)
    solar = astronomy.sun_times(latitude, longitude, day, "+01:00")
    data = {
        "目标机位": "合成机位",
        "目标坐标": f"{latitude},{longitude}",
        "目标时区": "+01:00",
        "sun_times": solar,
        "weather_forecast": {
            "位置": f"{latitude},{longitude}",
            "时区": "+01:00",
            "获取时间": now.isoformat(),
            "有效开始时间": f"{day}T00:00:00+01:00",
            "有效结束时间": f"{tomorrow}T00:00:00+01:00",
            "每日预报": [
                {
                    "日期": day,
                    "平均云量（%）": 50,
                    "最低能见度（km）": 25,
                    "最高降水概率（%）": 10,
                    "最大风速（km/h）": 15,
                }
            ],
        },
    }
    card = qualify_card(
        DecisionCard(
            conclusion="离线模型声称值得去",
            subject_type="日落",
            target_date=day,
            verdict="go",
            confidence="high",
            evidence=[],
        ),
        data,
        now,
    )
    assert card.data_status == ("ready" if ready else "needs_data")
    assert card.verdict == ("go" if ready else "wait")
    if not ready:
        assert card.confidence == "low"
        assert any("日落" in reason for reason in card.missing_data)
