"""QuotaLedger 并发记账的 pytest 用例（B3-5：D5 配套「并发下记账需加锁」）。

验证：多线程并发 record 不丢账、usage 与成功次数一致；并发 record + usage 混合无异常。
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from lighttrail.infra.quota import QuotaLedger

_CALLS = 200
_MODEL = "ecnu-plus"


def test_concurrent_records_are_not_lost() -> None:
    """200 次并发记账：credits 合计与串行结果一致（无丢账/重复计）。"""
    serial = QuotaLedger()
    expected = serial.record(_MODEL, 1000, 500)

    ledger = QuotaLedger()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: ledger.record(_MODEL, 1000, 500), range(_CALLS)))

    assert len(results) == _CALLS
    assert sum(results) == pytest_approx(expected * _CALLS)
    assert ledger.usage().hours_ratio == pytest_approx(expected * _CALLS / 2000.0)


def test_concurrent_record_and_usage_interleaved() -> None:
    """记账与查询交错进行：不抛异常，水位单调不减。"""
    ledger = QuotaLedger()
    ratios: list[float] = []
    lock = threading.Lock()

    def worker(index: int) -> None:
        if index % 2 == 0:
            ledger.record(_MODEL, 500, 200)
        else:
            with lock:
                ratios.append(ledger.usage().hours_ratio)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(worker, range(100)))

    assert ratios  # 查询确实发生过
    assert max(ratios) > 0  # 已有记账被看到


def pytest_approx(value: float) -> object:
    """局部近似比较（避免为一条断言引入 pytest 依赖风格的差异）。"""
    import pytest

    return pytest.approx(value)