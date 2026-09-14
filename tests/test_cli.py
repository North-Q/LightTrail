"""CLI 入口的 pytest 用例（离线，不触网）。

覆盖：
- `_configure_stdio()`：Windows GBK 控制台下把不可编码字符降级为替代字符，
  避免整条 CLI 因模型输出里的 `⚠` / `→` 抛 UnicodeEncodeError 崩掉（实测真 bug）。
"""

from __future__ import annotations

import io
import sys

from lighttrail.cli import _configure_stdio


def test_configure_stdio_reconfigures_both_streams(monkeypatch) -> None:
    """stdout / stderr 都被设为容错模式（errors=replace，不动编码）。"""
    calls: list[dict] = []

    class _Stream:
        def reconfigure(self, **kwargs) -> None:
            calls.append(kwargs)

    out, err = _Stream(), _Stream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    _configure_stdio()
    assert calls == [{"errors": "replace"}, {"errors": "replace"}]


def test_configure_stdio_makes_gbk_stream_lossy(monkeypatch) -> None:
    """修复前：GBK 流写「⚠」直接抛 UnicodeEncodeError；修复后降级为替代字符，中文不受影响。"""
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp936")
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", io.StringIO())

    _configure_stdio()
    print("- ⚠ 降级标注：配额不足")
    stream.flush()

    written = buffer.getvalue().decode("cp936")
    assert "降级标注" in written
    assert "⚠" not in written  # GBK 编不出，已降级