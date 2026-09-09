"""评估 runner（E8）：L2 黄金用例回归（cassette 回放零成本）+ L3 质量打分。

用法：
    python -m evals.runner --level L2                 # 回放（零 LLM 成本）
    python -m evals.runner --level L2 --record        # 真实 Key 录制最小样本（配额克制）
    python -m evals.runner --level L3 [--llm-judge --limit N]  # 打分 + 工具交叉校验
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.fake_data import (
    CassetteChatClient,
    FakeDispatcher,
    RecordingChatClient,
    dispatch_with_trace,
)
from evals.judge import LLMJudge, LocalRubric, ToolCrossCheck
from lighttrail.agent import Agent
from lighttrail.agent.tools import registry
from lighttrail.config import load_settings
from lighttrail.infra.quota import QuotaLedger
from lighttrail.infra.trace import TraceRecorder
from lighttrail.llm.client import ChatClient
from lighttrail.llm.router import ModelRouter, RouteIntent
from lighttrail.memory import MemoryManager
from lighttrail.orchestrator import Orchestrator
from lighttrail.orchestrator.schemas import DecisionCard
from lighttrail.tools import (  # noqa: F401  触发注册
    astronomy,
    basic,
    exposure,
    site_match,
    weather,
)

_EVALS_ROOT = Path(__file__).resolve().parent
_GOLDEN_FILE = _EVALS_ROOT / "golden" / "L2-cases.json"
_CASSETTES_DIR = _EVALS_ROOT / "cassettes"
_RESULTS_DIR = _EVALS_ROOT / "results"

# L3 交叉校验的默认等效焦距（mm）：按题材分组近似
_GROUP_FOCAL: dict[str, float] = {
    "galaxy": 14.0,
    "live": 24.0,
    "planning": 24.0,
    "sun": 105.0,
    "polar": 14.0,
}


def _now_iso() -> str:
    """当前 UTC 时间戳（结果文件名）。"""
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def assert_card(card: DecisionCard, expect: dict[str, Any]) -> list[str]:
    """黄金断言：schema 合法 + 结构性字段（M2 不落空）。

    Args:
        card: 管线产出的决策卡。
        expect: 期望（conclusion_contains / min_evidence / max_locations /
            confidence_allowed / allow_degraded / time_window_note）。

    Returns:
        违规清单（空 = 通过）。
    """
    problems: list[str] = []
    if not card.conclusion or not card.conclusion.strip():
        problems.append("结论为空")
    allowed = expect.get("confidence_allowed") or ["high", "medium", "low"]
    if card.confidence not in allowed:
        problems.append(f"置信度 {card.confidence} 不在允许范围 {allowed}")
    min_evidence = int(expect.get("min_evidence", 1))
    if len(card.evidence) < min_evidence:
        problems.append(f"evidence 数量 {len(card.evidence)} < {min_evidence}")
    max_locations = expect.get("max_locations")
    if max_locations is not None and len(card.locations) > int(max_locations):
        problems.append(f"机位数 {len(card.locations)} 超过 {max_locations}")
    if not expect.get("allow_degraded") and card.degraded:
        problems.append(f"意外降级标注：{card.degraded}")
    for keyword in expect.get("conclusion_contains") or []:
        if keyword not in card.conclusion:
            problems.append(f"结论缺少关键词「{keyword}」")
    if expect.get("time_window_note") and not card.time_window:
        problems.append("缺时间窗口（time_window）")
    return problems


def _walk_path(data: Any, path: list[str]) -> Any:
    """按键路径取值。"""
    current = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            raise KeyError(f"路径缺失：{'.'.join(path)}（位于 {key}）")
        current = current[key]
    return current


def _check_tool_case(name: str, args: dict[str, Any], checks: list[dict[str, Any]]) -> list[str]:
    """执行工具用例断言，返回违规清单。"""

    raw = registry.dispatch(name, json.dumps(args, ensure_ascii=False))
    try:
        result = json.loads(raw)
    except ValueError:
        return [f"工具返回非 JSON：{raw[:80]}"]
    problems: list[str] = []
    for check in checks:
        op = check.get("op")
        if op == "error":
            if "error" not in result:
                problems.append(f"期望 error 但返回正常：{str(result)[:80]}")
            continue
        try:
            value = _walk_path(result, check.get("path") or [])
        except KeyError as exc:
            problems.append(str(exc))
            continue
        if op == "eq":
            problems += _eq(name, value, check)
        elif op == "close":
            tolerance = check.get("tolerance", 1e-6)
            if not isinstance(value, (int, float)) or abs(float(value) - float(check["expect"])) > tolerance:
                problems.append(f"{name}: {value} 与期望 {check['expect']} 差超 {tolerance}")
        elif op == "range":
            low, high = check["expect"]
            if not isinstance(value, (int, float)) or not low <= float(value) <= high:
                problems.append(f"{name}: {value} 不在 [{low}, {high}]")
        elif op == "truthy":
            if not value:
                problems.append(f"{name}: 字段为空/假值")
        elif op == "match":
            if not re.search(check["expect"], str(value)):
                problems.append(f"{name}: {value} 不匹配 {check['expect']}")
        elif op == "contains":
            if str(check["expect"]) not in str(value):
                problems.append(f"{name}: 缺关键词「{check['expect']}」")
        else:
            problems.append(f"未知断言 op：{op}")
    return problems


def _eq(name: str, value: Any, check: dict[str, Any]) -> list[str]:
    """数值/字符串相等断言。"""
    if isinstance(value, (int, float)) and isinstance(check["expect"], (int, float)):
        return [] if abs(float(value) - float(check["expect"])) < 1e-6 else [f"{name}: {value} != {check['expect']}"]
    return [] if value == check["expect"] else [f"{name}: {value!r} != {check['expect']!r}"]


def _load_cases() -> dict[str, Any]:
    """读取 L2 用例集。"""
    return json.loads(_GOLDEN_FILE.read_text(encoding="utf-8"))


def _load_cassettes() -> dict[str, dict[str, dict[str, str]]]:
    """读取全部已录制 cassette。"""
    cassettes: dict[str, dict[str, dict[str, str]]] = {}
    if not _CASSETTES_DIR.exists():
        return cassettes
    for path in sorted(_CASSETTES_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        group = data["group"]
        cassettes[group] = {
            "intent": {"content": data["intent"]["content"]},
            "card": {"content": data["card"]["content"]},
        }
    return cassettes


def _record_group(group: str, request: str, router: ModelRouter, settings: Any) -> dict[str, Any]:
    """真实 Key 录制一组 cassette（意图 + 综合两段 LLM 响应）。"""
    recorder = TraceRecorder()
    client = ChatClient(
        settings.api_key,
        settings.base_url,
        serial_llm=settings.serial_llm,
        quota=QuotaLedger(warn_threshold=settings.quota_warn_threshold),
    )
    recording = RecordingChatClient(client)
    memory_dir = Path(tempfile.mkdtemp(prefix="lt_evals_record_"))
    try:
        memory = MemoryManager(memory_dir)
        fake = FakeDispatcher()
        dispatch = dispatch_with_trace(fake, recorder)
        agent = Agent(
            recording,
            registry,
            model=settings.model,
            memory=memory,
            recorder=recorder,
            reason_thinking=settings.reason_thinking,
        )
        orchestrator = Orchestrator(
            recording, registry, agent, memory=memory, recorder=recorder, dispatch=dispatch
        )
        _ = orchestrator.run_pipeline(request)
    finally:
        shutil.rmtree(memory_dir, ignore_errors=True)
    if "intent" not in recording.recorded or "card" not in recording.recorded:
        raise RuntimeError(f"录制 {group} 失败：未捕获完整响应（{sorted(recording.recorded)}）")
    return {
        "group": group,
        "intent": {"content": recording.recorded["intent"]},
        "card": {"content": recording.recorded["card"]},
        "recorded_at": _now_iso(),
        "model_hint": settings.model,
    }


def _run_pipeline_case(case: dict[str, Any], cassettes: dict[str, Any]) -> tuple[DecisionCard, list[str], list[str]]:
    """回放单条管线用例（cassette + Fake 数据）；返回 (card, trace 摘要, 违规清单)。"""
    group = case["group"]
    memo_dir = Path(tempfile.mkdtemp(prefix="lt_evals_l2_"))
    try:
        memory = MemoryManager(memo_dir)
        recorder = TraceRecorder()
        client = CassetteChatClient(cassettes, group_for={case["id"]: group})
        client.set_group(group)
        fake = FakeDispatcher(scenario=case.get("scenario", "normal"))
        dispatch = dispatch_with_trace(fake, recorder)
        agent = Agent(
            client,
            registry,
            model="ecnu-plus",
            memory=memory,
            recorder=recorder,
            reason_thinking=False,
        )
        orchestrator = Orchestrator(
            client, registry, agent, memory=memory, recorder=recorder, dispatch=dispatch
        )
        card = orchestrator.run_pipeline(case["request"], mode=case.get("mode", ""))
        trace = [f"{ref.name}" for ref in recorder.to_report().tool_calls]
        problems = assert_card(card, case.get("expect") or {})
        return card, trace, problems
    finally:
        shutil.rmtree(memo_dir, ignore_errors=True)


def run_l2(*, record: bool = False, groups: set[str] | None = None) -> dict[str, Any]:
    """执行 L2 回归；返回报告 dict（含 pass/fail 明细与耗时）。

    Args:
        record: True 时用真实 Key 录缺失的 cassette（最小样本，配额克制）。
        groups: 只跑指定 group 的子集（调试用）。

    Returns:
        报告字典（写 evals/results/）。
    """
    cases = _load_cases()
    started = time.perf_counter()
    cassettes = _load_cassettes()

    if record:
        settings = load_settings()
        if not settings.has_api_key:
            raise RuntimeError("--record 需要真实 API Key（检查 .env）")
        router = ModelRouter()
        needed = {case["group"] for case in cases["pipeline_cases"] if case["group"] not in cassettes}
        if groups:
            needed &= groups
        for group in sorted(needed):
            sample = next(case for case in cases["pipeline_cases"] if case["group"] == group)
            cassette = _record_group(group, sample["request"], router, settings)
            _CASSETTES_DIR.mkdir(parents=True, exist_ok=True)
            (_CASSETTES_DIR / f"{group}.json").write_text(
                json.dumps(cassette, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            cassettes = _load_cassettes()  # 刷新

    missing = sorted({case["group"] for case in cases["pipeline_cases"]} - set(cassettes))
    if missing:
        raise RuntimeError(f"缺少 cassette 分组 {missing}——请先 python -m evals.runner --level L2 --record")

    pipeline_rows: list[dict[str, Any]] = []
    for case in cases["pipeline_cases"]:
        if groups and case["group"] not in groups:
            continue
        card, trace, problems = _run_pipeline_case(case, cassettes)
        pipeline_rows.append(
            {
                "id": case["id"],
                "group": case["group"],
                "request": case["request"],
                "pass": not problems,
                "problems": problems,
                "conclusion": card.conclusion,
                "confidence": card.confidence,
                "evidence": len(card.evidence),
                "locations": len(card.locations),
                "tools": trace,
            }
        )

    tool_rows: list[dict[str, Any]] = []
    for case in cases["tool_cases"]:
        problems = _check_tool_case(case["name"], case["args"], case["checks"])
        tool_rows.append({"id": case["id"], "name": case["name"], "pass": not problems, "problems": problems})

    elapsed = round(time.perf_counter() - started, 2)
    report: dict[str, Any] = {
        "level": "L2",
        "ts": _now_iso(),
        "elapsed_s": elapsed,
        "llm_calls": 0,  # 回放模式零 LLM 成本
        "pipeline_total": len(pipeline_rows),
        "pipeline_pass": sum(1 for row in pipeline_rows if row["pass"]),
        "tool_total": len(tool_rows),
        "tool_pass": sum(1 for row in tool_rows if row["pass"]),
        "e6_archived": cases["archived_e6"]["count"],
        "pipeline_details": pipeline_rows,
        "tool_details": tool_rows,
    }
    return report


def _cards_for_l3() -> list[tuple[dict[str, Any], DecisionCard]]:
    """回放管线用例得到卡片（供 L3 打分）。"""
    cases = _load_cases()
    cassettes = _load_cassettes()
    rows: list[tuple[dict[str, Any], DecisionCard]] = []
    for case in cases["pipeline_cases"]:
        card, _, _ = _run_pipeline_case(case, cassettes)
        rows.append((case, card))
    return rows


def _mean(scores: dict[str, list[int]]) -> dict[str, float]:
    """分维度均值。"""
    return {dim: round(sum(values) / len(values), 2) for dim, values in scores.items()}


def _latest_l3() -> dict[str, Any] | None:
    """读取最近一次 L3 报告（质量曲线 diff 用）。"""
    if not _RESULTS_DIR.exists():
        return None
    candidates = sorted(_RESULTS_DIR.glob("*-L3.json"))
    if not candidates:
        return None
    return json.loads(candidates[-1].read_text(encoding="utf-8"))


def run_l3(*, use_llm: bool = False, limit: int = 10) -> dict[str, Any]:
    """执行 L3：rubric 打分 + 工具交叉校验（可选 LLM 判官，受限条数）。

    Args:
        use_llm: 是否用真实 LLM 判官（配额克制：默认关，开则受 limit 约束）。
        limit: 参与 LLM 判官的最大卡片数（默认 10，红线内）。

    Returns:
        报告 dict（写 evals/results/）。
    """
    started = time.perf_counter()
    samples = _cards_for_l3()
    rubric = LocalRubric()
    cross = ToolCrossCheck()

    per_dim: dict[str, list[int]] = {dim: [] for dim in rubric.score(samples[0][1])}
    llm_scores: dict[str, list[int]] = {dim: [] for dim in per_dim}
    findings: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    for case, card in samples:
        local = rubric.score(card)
        for dim, entry in local.items():
            per_dim[dim].append(entry["score"])
        focal = _GROUP_FOCAL.get(case["group"])
        hits = cross.cross_check(card, focal_mm=focal) if focal else []
        findings.extend(hits)
        row = {"id": case["id"], "group": case["group"], "local": local, "cross_check": hits, "conclusion": card.conclusion[:60]}
        rows.append(row)

    if use_llm:
        settings = load_settings()
        if not settings.has_api_key:
            raise RuntimeError("--llm-judge 需要真实 API Key（检查 .env）")
        router = ModelRouter()
        judge_model = RouteIntent.DEEP_REASONING.resolve(router)
        client = ChatClient(settings.api_key, settings.base_url, serial_llm=settings.serial_llm)
        judge = LLMJudge(lambda prompt: client.chat(
            [{"role": "system", "content": "你是严格的评估判官。"}, {"role": "user", "content": prompt}],
            model=judge_model,
            temperature=0.2,
        ).get("content", ""))
        for case, card in samples[:limit]:
            llm_score = judge.score(card)
            rows.append({"id": f"{case['id']}-llm", "group": case["group"], "llm_judge": llm_score})
            for dim, entry in llm_score.items():
                llm_scores[dim].append(entry["score"])

    means = _mean(per_dim)
    means_llm = _mean(llm_scores) if use_llm and llm_scores.get(next(iter(per_dim))) else {}
    previous = _latest_l3()
    diff = {dim: round(means[dim] - (previous["means"].get(dim, 0) if previous else 0), 2) for dim in means}
    elapsed = round(time.perf_counter() - started, 2)

    report: dict[str, Any] = {
        "level": "L3",
        "ts": _now_iso(),
        "elapsed_s": elapsed,
        "samples": len(samples),
        "means": means,
        "means_llm": means_llm,
        "diff_vs_last": diff,
        "cross_check_findings": findings,
        "rows": rows,
        "llm_judge_used": use_llm,
        "llm_judge_count": min(len(samples), limit) if use_llm else 0,
    }
    return report


def _save(report: dict[str, Any]) -> Path:
    """报告存档到 evals/results/。"""
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _RESULTS_DIR / f"{report['ts']}-{report['level']}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _print_report(report: dict[str, Any]) -> None:
    """控制台输出可读摘要。"""
    if report["level"] == "L2":
        print(f"L2 黄金回归：pipeline {report['pipeline_pass']}/{report['pipeline_total']}，"
              f"tool {report['tool_pass']}/{report['tool_total']}，E6 归档 {report['e6_archived']}，"
              f"耗时 {report['elapsed_s']}s，LLM 调用 {report['llm_calls']}")
        for row in report["pipeline_details"]:
            mark = "PASS" if row["pass"] else "FAIL"
            print(f"  [{mark}] {row['id']} {row['group']} {row['request'][:24]}"
                  f" conf={row['confidence']} evidence={row['evidence']} spots={row['locations']}"
                  + (f" 违规：{'；'.join(row['problems'])}" if row["problems"] else ""))
        for row in report["tool_details"]:
            mark = "PASS" if row["pass"] else "FAIL"
            print(f"  [{mark}] {row['id']} {row['name']}" + (f" 违规：{'；'.join(row['problems'])}" if row["problems"] else ""))
        path = _save(report)
        print(f"报告：{path}")
    else:
        print("L3 rubric 打分（均值 / 与上次 diff）：")
        for dim, value in report["means"].items():
            print(f"  {dim}: {value}（diff {report['diff_vs_last'].get(dim, 0):+.2f}）")
        if report.get("means_llm"):
            print("L3 LLM 判官均值（真实子集）：")
            for dim, value in report["means_llm"].items():
                print(f"  {dim}: {value}")
        print(f"工具交叉校验违规：{len(report['cross_check_findings'])} 处")
        for finding in report["cross_check_findings"]:
            print(f"  ⚠ {finding['rule']}｜{finding['param']}｜{finding['detail']}")
        path = _save(report)
        print(f"报告：{path}")


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(prog="evals.runner", description="LightTrail 评估 runner（L2/L3）")
    parser.add_argument("--level", required=True, choices=["L2", "L3"])
    parser.add_argument("--record", action="store_true", help="L2：真实 Key 录制缺失 cassette（配额克制）")
    parser.add_argument("--group", action="append", help="只跑指定 group（可重复）")
    parser.add_argument("--llm-judge", action="store_true", help="L3：启用真实 LLM 判官（≤limit 条）")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args(argv)

    groups = set(args.group) if args.group else None
    if args.level == "L2":
        report = run_l2(record=args.record, groups=groups)
    else:
        report = run_l3(use_llm=args.llm_judge, limit=args.limit)
    _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
