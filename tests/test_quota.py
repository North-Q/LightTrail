"""配额账本（E4-2）的 pytest 用例。

验证：
- record / estimate 按计价表折算 credits（含缓存命中价）；
- 三窗口滚动水位与 check 放行判定；
- 水位超阈时 degrade 返回降级建议（能力级降级链 + 矩阵解析模型名），
  且降级链/计价表可注入（平台中立：不硬编码 ecnu 品牌对）；
- ChatClient 注入 quota 后按 usage 自动记账；
- 参数校验（负 token / 未知模型计价 / 非法阈值）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lighttrail.infra.quota import CallPlan, Pricing, QuotaError, QuotaLedger
from lighttrail.infra.trace import TraceReport
from lighttrail.llm.client import ChatClient
from lighttrail.llm.router import ModelRouter, RouteIntent

_FIXED_NOW = 1_800_000_000.0


def _make_ledger(**kwargs) -> QuotaLedger:
    """固定时钟的账本（测试可精确控制窗口）。"""
    return QuotaLedger(now=_FIXED_NOW, **kwargs)


# ------ 记账与预估 ------
def test_record_converts_tokens_to_credits() -> None:
    """plus 计价：100 万输入 → 100 credits，100 万输出 → 400 credits。"""
    ledger = _make_ledger()
    assert ledger.record("ecnu-plus", 1_000_000, 0) == pytest.approx(100.0)
    assert ledger.record("ecnu-plus", 0, 1_000_000) == pytest.approx(400.0)
    assert ledger.record("ecnu-max", 1_000_000, 0) == pytest.approx(300.0)


def test_record_counts_cached_input_at_cached_price() -> None:
    """命中缓存输入按命中价计价（plus 命中 20/M）。"""
    ledger = _make_ledger()
    credits = ledger.record("ecnu-plus", 1_000_000, 0, cached_input_tokens=1_000_000)
    assert credits == pytest.approx(20.0)


def test_estimate_sums_plan() -> None:
    """CallPlan 分步预估合计。"""
    ledger = _make_ledger()
    plan = CallPlan(steps=(("ecnu-plus", 1_000_000, 1_000_000, 0), ("ecnu-max", 1_000_000, 0, 0)))
    assert ledger.estimate(plan) == pytest.approx(500.0 + 300.0)


def test_custom_pricing_injected() -> None:
    """计价表可注入：换 API 无需改账本逻辑。"""
    ledger = QuotaLedger(pricing={"custom-model": Pricing(input_per_m=10.0, output_per_m=20.0)}, now=_FIXED_NOW)
    assert ledger.record("custom-model", 1_000_000, 1_000_000) == pytest.approx(30.0)
    with pytest.raises(QuotaError):
        ledger.record("unknown-model", 1, 1)


# ------ 窗口水位与放行 ------
def test_usage_ratios_roll_by_window() -> None:
    """5h 窗口滚动：窗口外的记录不计入。"""
    ledger = _make_ledger()
    ledger.record("ecnu-plus", 1_000_000, 0)  # 100 credits within 5h window
    ledger._records.append((_FIXED_NOW - 6 * 3600.0, 100.0))  # 6h 前，滚出窗口
    usage = ledger.usage()
    assert usage.hours_ratio == pytest.approx(100.0 / 2000.0)
    assert usage.daily_ratio == pytest.approx(200.0 / 5000.0)
    assert usage.peak_ratio == pytest.approx(100.0 / 2000.0)


def test_check_allows_within_limit_and_rejects_over() -> None:
    """check：预估后不超限放行，超限拒绝。"""
    ledger = _make_ledger()
    assert ledger.check() is True
    assert ledger.check(2000.0) is True  # 恰好等于 5h 限额
    assert ledger.check(2000.01) is False
    ledger.record("ecnu-plus", 10_000_000, 0)  # 1000 credits
    assert ledger.check(1000.0) is True
    assert ledger.check(1000.01) is False


def test_negative_estimate_raises() -> None:
    """负的预估抛 QuotaError。"""
    with pytest.raises(QuotaError):
        _make_ledger().check(-1.0)


# ------ 降级建议（能力级可注入）------
def test_degrade_suggests_fallback_when_water_level_high() -> None:
    """水位 95% 时深推理降级：建议文本含降级原因与矩阵解析的目标模型。"""
    ledger = _make_ledger()
    ledger.record("ecnu-plus", 19_000_000, 0)  # 1900/2000 = 95%
    result = ledger.degrade(RouteIntent.DEEP_REASONING, ModelRouter())
    assert result is not None
    assert "95%" in result
    assert "deep" in result
    assert "ecnu-plus" in result  # 目标模型经默认矩阵解析（deep→tools）
    # 工具调用意图无可降级项 → None
    assert ledger.degrade(RouteIntent.TOOLS, ModelRouter()) is None


def test_degrade_none_when_water_level_normal() -> None:
    """水位正常时无降级建议。"""
    ledger = _make_ledger()
    ledger.record("ecnu-plus", 1_000_000, 0)  # 5%
    assert ledger.degrade(RouteIntent.DEEP_REASONING, ModelRouter()) is None


def test_degrade_map_injectable() -> None:
    """降级链可注入（能力语义）：deep → vision + thinking。"""
    ledger = _make_ledger(degrade_map={"deep": "vision"})
    ledger.record("ecnu-plus", 19_000_000, 0)
    result = ledger.degrade(RouteIntent.DEEP_REASONING, ModelRouter())
    assert result is not None
    assert "vision" in result
    assert "ecnu-plus" in result


def test_warn_threshold_validated() -> None:
    """非法阈值抛 QuotaError。"""
    with pytest.raises(QuotaError):
        QuotaLedger(warn_threshold=0.0)
    with pytest.raises(QuotaError):
        QuotaLedger(warn_threshold=1.5)


# ------ ChatClient 集成 ------
def _make_resp(prompt: int, completion: int, cached: int, content: str = "ok"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))],
        usage=SimpleNamespace(
            prompt_tokens=prompt,
            completion_tokens=completion,
            prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
        ),
    )


def test_client_records_usage_into_ledger() -> None:
    """ChatClient 注入 quota 后按响应 usage 记账；未注入不记账。"""
    ledger = _make_ledger()
    client = ChatClient(api_key="sk-test", base_url="http://127.0.0.1:1", quota=ledger)
    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_: _make_resp(100, 50, 20)))
    )
    client.chat([{"role": "user", "content": "hi"}], model="ecnu-plus")
    # 输入 100（其中缓存 20 按命中价）+ 输出 50
    # = (80*100 + 20*20 + 50*400) / 1M = 0.0284 credits
    assert ledger.usage().hours_ratio == pytest.approx(0.0284 / 2000.0)
    # 未注入 quota 的客户端零记账、不抛错
    bare = ChatClient(api_key="sk-test", base_url="http://127.0.0.1:1")
    bare._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_: _make_resp(1, 1, 0)))
    )
    bare.chat([{"role": "user", "content": "hi"}], model="ecnu-plus")


def test_trace_report_degradation_field_default_empty() -> None:
    """TraceReport.degradation 字段缺省为空串（E5 管线降级时写入）。"""
    assert TraceReport().degradation == ""
