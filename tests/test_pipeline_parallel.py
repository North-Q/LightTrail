"""管线并发采集的 pytest 用例（B3-3）。

验证：
- 采集阶段并发：4 个无依赖源（各 0.15s）在 `asyncio.TaskGroup` 下并发执行，
  耗时显著低于顺序 for 循环（验收：延迟降 ≥40%）；
- 单源失败降级：该源写 {"error": ...}，其余源照常返回，管线不被打断；
- 取数并发与 LLM 并发相互独立（不占用 LLM 信号量，测试以 dispatch 侧阻塞证明）。
"""

from __future__ import annotations

import json
import time

from lighttrail.orchestrator import pipelines as pipelines_mod
from lighttrail.orchestrator.context import PipelineContext
from lighttrail.orchestrator.pipelines import PipelineEnv

_SOURCE_DELAY = 0.15
_SOURCES = 4


def _env(dispatch) -> PipelineEnv:
    """构造最小管线环境（reason 不参与采集测试）。"""
    return PipelineEnv(dispatch=dispatch, reason=lambda prompt, system: "{}")


def _steps() -> list[tuple[str, str]]:
    """4 个无依赖采集步骤。"""
    return [(f"tool_{index}", json.dumps({"index": index})) for index in range(_SOURCES)]


def test_collect_runs_sources_concurrently() -> None:
    """并发采集：4 源各 0.15s → 并发耗时远低于顺序 0.6s（降幅 ≥40%）。"""

    def slow_dispatch(name: str, args: str) -> str:
        time.sleep(_SOURCE_DELAY)
        return json.dumps({"ok": name})

    env = _env(slow_dispatch)
    started = time.perf_counter()
    data = pipelines_mod._collect(env, PipelineContext(user_request="x"), _steps())
    elapsed = time.perf_counter() - started
    sequential = _SOURCES * _SOURCE_DELAY

    assert set(data) == {f"tool_{index}" for index in range(_SOURCES)}
    assert all(item == {"ok": name} for name, item in data.items())
    assert elapsed < sequential * 0.6, f"并发采集未生效：{elapsed:.3f}s vs 顺序 {sequential:.3f}s"


def test_collect_degrades_single_source_failure() -> None:
    """单源抛错 → 该源 {"error": ...}，其余源正常（管线不被打断）。"""

    def flaky_dispatch(name: str, args: str) -> str:
        if name == "boom":
            raise RuntimeError("网络炸了")
        return json.dumps({"ok": name})

    env = _env(flaky_dispatch)
    data = pipelines_mod._collect(
        env, PipelineContext(user_request="x"), [("good", "{}"), ("boom", "{}")]
    )

    assert data["good"] == {"ok": "good"}
    assert "网络炸了" in data["boom"]["error"]


def test_collect_records_trace_step_per_source() -> None:
    """每源一条 采集_<工具> 步骤事件（trace 口径不变）。"""
    from lighttrail.infra.trace import TraceRecorder

    env = PipelineEnv(
        dispatch=lambda name, args: json.dumps({"ok": name}),
        reason=lambda prompt, system: "{}",
        recorder=TraceRecorder(),
    )
    pipelines_mod._collect(env, PipelineContext(user_request="x"), _steps())

    steps = [step.name for step in env.recorder.to_report().steps]
    assert steps == [f"采集_tool_{index}" for index in range(_SOURCES)]