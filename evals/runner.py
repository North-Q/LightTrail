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
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.fake_data import (
    CassetteChatClient,
    FakeDispatcher,
    RecordingChatClient,
)
from evals.judge import LLMJudge, LocalRubric, ToolCrossCheck
from evals.semantic import assert_semantics
from lighttrail.composition import (
    ApplicationServices,
    RequestScope,
    build_application,
    build_client,
)
from lighttrail.config import Settings, load_settings
from lighttrail.contracts.context import RequestContext
from lighttrail.contracts.models import DecisionCard

_EVALS_ROOT = Path(__file__).resolve().parent
_GOLDEN_FILE = _EVALS_ROOT / "golden" / "L2-cases.json"
_CASSETTES_DIR = _EVALS_ROOT / "cassettes" / "v2"
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
            confidence_allowed / allow_degraded / time_window_note / verdict_allowed；
            B4-3 起还校验 confidence_detail 契约不变量）。

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
    # B4-3 契约不变量：置信度明细必填、区间有序、三档计数与依据条数一致、verdict 在允许集合
    detail = card.confidence_detail
    if detail is None:
        problems.append("缺 confidence_detail（B4-3 起必填：前端置信度三层的唯一来源）")
    else:
        if not 0 <= detail.low <= detail.score <= detail.high <= 100:
            problems.append(
                f"confidence_detail 区间非法：{detail.low}/{detail.score}/{detail.high}"
            )
        counted = detail.high_count + detail.medium_count + detail.low_count
        if counted != len(card.evidence):
            problems.append(
                f"confidence_detail 计数 {counted} 与 evidence {len(card.evidence)} 条不一致"
            )
    allowed_verdicts = expect.get("verdict_allowed") or ["go", "wait", "risk", ""]
    if card.verdict not in allowed_verdicts:
        problems.append(f"verdict {card.verdict!r} 不在允许集合 {allowed_verdicts}")
    return problems


def _walk_path(data: Any, path: list[str]) -> Any:
    """按键路径取值。"""
    current = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            raise KeyError(f"路径缺失：{'.'.join(path)}（位于 {key}）")
        current = current[key]
    return current


def _check_tool_case(
    name: str, args: dict[str, Any], checks: list[dict[str, Any]], scope: RequestScope
) -> list[str]:
    """执行工具用例断言，返回违规清单。"""

    raw = scope.services.registry.dispatch(
        name, json.dumps(args, ensure_ascii=False), scope.tool_context, recorder=scope.recorder
    )
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
            if (
                not isinstance(value, (int, float))
                or abs(float(value) - float(check["expect"])) > tolerance
            ):
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
        return (
            []
            if abs(float(value) - float(check["expect"])) < 1e-6
            else [f"{name}: {value} != {check['expect']}"]
        )
    return [] if value == check["expect"] else [f"{name}: {value!r} != {check['expect']!r}"]


def _load_cases() -> dict[str, Any]:
    """读取 L2 用例集。"""
    return json.loads(_GOLDEN_FILE.read_text(encoding="utf-8"))


def _load_cassettes() -> dict[str, dict[str, Any]]:
    """按 case_id 读取独立夹具，旧分组 cassette 仅作历史证据。"""
    cassettes: dict[str, dict[str, Any]] = {}
    if not _CASSETTES_DIR.exists():
        return cassettes
    for path in sorted(_CASSETTES_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        case_id = data["case_id"]
        if case_id != path.stem or case_id in cassettes:
            raise ValueError(f"cassette 文件名或 case_id 重复：{path}")
        if data.get("kind") not in {"offline_fixture", "api_recording"}:
            raise ValueError(f"cassette 未标明合成/真实来源：{path}")
        responses = data.get("responses") or {}
        cassettes[case_id] = {
            "request": data["request"],
            "kind": data["kind"],
            "tool_results": data.get("tool_results") or {},
            **{
                key: {"content": json.dumps(value, ensure_ascii=False)}
                for key, value in responses.items()
            },
        }
    return cassettes


def _record_case(case: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """显式 --record 时按用例录制模型响应；工具仍由离线夹具提供。"""
    recording = RecordingChatClient(build_client(settings))
    with tempfile.TemporaryDirectory(prefix="lt_evals_record_") as directory:
        services = build_application(
            settings.model_copy(update={"data_dir": directory}),
            client=recording,
            dispatch=FakeDispatcher(scenario=case.get("scenario", "normal")),
        )
        try:
            context = _case_context(case)
            _seed_eval_profile(services, context, case.get("profile"))
            services.create_scope(context).orchestrator.run_pipeline(
                case["request"], mode=case.get("mode", "")
            )
        finally:
            services.close()
    if "card" not in recording.recorded:
        raise RuntimeError(f"录制 {case['id']} 失败：未捕获综合响应")
    return {
        "case_id": case["id"],
        "kind": "api_recording",
        "request": case["request"],
        "responses": {key: json.loads(value) for key, value in recording.recorded.items()},
        "tool_results": {},
        "recorded_at": _now_iso(),
        "model_hint": settings.model,
    }


def _case_context(case: dict[str, Any]) -> RequestContext:
    """用固定时刻、时区和身份创建可复现的请求上下文。"""
    values = case.get("request_context") or {"user_id": "_local"}
    if isinstance(values.get("requested_at"), str):
        values = {**values, "requested_at": datetime.fromisoformat(values["requested_at"])}
    return RequestContext(**values)


def _seed_eval_profile(
    services: ApplicationServices, context: RequestContext, profile: dict[str, Any] | None = None
) -> None:
    """为用例用户写入自己的档案；空档案场景不编造机位。"""
    if profile:
        services.memory.update_profile(context, profile)


def _evaluate_pipeline(
    case: dict[str, Any], scope: RequestScope
) -> tuple[DecisionCard, list[str], list[str]]:
    """在统一请求作用域执行管线，保留现有评估断言口径。"""
    card = scope.orchestrator.run_pipeline(case["request"], mode=case.get("mode", ""))
    trace = [ref.name for ref in scope.recorder.to_report().tool_calls]
    return card, trace, assert_card(card, case.get("expect") or {})


def _run_pipeline_case(
    case: dict[str, Any], cassettes: dict[str, Any], *, services: ApplicationServices | None = None
) -> tuple[DecisionCard, list[str], list[str]]:
    """只回放本 case cassette；模型、知识、配额和身份走生产装配。

    Args:
        case: 黄金用例，含固定请求时刻、档案及独立语义预期。
        cassettes: 按 case_id 索引的响应夹具。
        services: 应用服务覆盖，供跨入口生产装配集成测试使用。

    Returns:
        决策卡、工具轨迹摘要与违规清单。
    """
    case_id = case["id"]
    cassette = cassettes.get(case_id)
    if cassette is None and services is not None:
        cassette = cassettes.get(case.get("group"), {})
    if cassette is None:
        raise KeyError(f"缺少用例独占 cassette：{case_id}")
    if cassette.get("request") and cassette["request"] != case["request"]:
        raise ValueError(f"cassette 请求与黄金用例不一致：{case_id}")
    context = (
        _case_context(case)
        if case.get("request_context")
        else RequestContext(
            user_id=str(cassette.get("request_context", {}).get("user_id", "_local"))
        )
    )
    if services is not None:
        return _evaluate_pipeline(case, services.create_scope(context))
    with tempfile.TemporaryDirectory(prefix="lt_evals_l2_") as directory:
        client = CassetteChatClient({case_id: cassette})
        client.set_group(case_id)
        dispatcher = FakeDispatcher(
            scenario=case.get("scenario", "normal"), payloads=cassette.get("tool_results")
        )
        active_services = build_application(
            load_settings().model_copy(update={"data_dir": directory}),
            client=client,
            dispatch=dispatcher,
        )
        try:
            _seed_eval_profile(active_services, context, case.get("profile"))
            card, trace, problems = _evaluate_pipeline(case, active_services.create_scope(context))
            problems.extend(assert_semantics(card, case, cassette, dispatcher.calls, client.calls))
            return card, trace, problems
        finally:
            active_services.close()


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
        needed = [
            case
            for case in cases["pipeline_cases"]
            if case["id"] not in cassettes and (not groups or case["group"] in groups)
        ]
        for case in needed:
            cassette = _record_case(case, settings)
            _CASSETTES_DIR.mkdir(parents=True, exist_ok=True)
            (_CASSETTES_DIR / f"{case['id']}.json").write_text(
                json.dumps(cassette, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            cassettes = _load_cassettes()  # 刷新

    selected_cases = [
        case for case in cases["pipeline_cases"] if not groups or case["group"] in groups
    ]
    missing = sorted(case["id"] for case in selected_cases if case["id"] not in cassettes)
    if missing:
        raise RuntimeError(f"缺少用例独占 cassette {missing}——仅显式 --record 才调用模型")

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
                # B3 起管线并行取数（asyncio.TaskGroup）：完成顺序不定但与语义无关，
                # 报告按工具名排序以保证「同样输入 → 同样报告」（L2 可重复性断言依赖它）。
                "tools": sorted(trace),
            }
        )

    tool_rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="lt_evals_tools_") as directory:
        services = build_application(
            load_settings().model_copy(update={"data_dir": directory}),
            client=CassetteChatClient(cassettes),
        )
        try:
            scope = services.create_scope(RequestContext())
            for case in cases["tool_cases"]:
                problems = _check_tool_case(case["name"], case["args"], case["checks"], scope)
                tool_rows.append(
                    {
                        "id": case["id"],
                        "name": case["name"],
                        "pass": not problems,
                        "problems": problems,
                    }
                )
        finally:
            services.close()

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


def _cards_for_l3() -> list[tuple[dict[str, Any], DecisionCard, list[str]]]:
    """回放卡片与语义违规；L3 评分不能掩盖 L2 事实失败。"""
    cases = _load_cases()
    cassettes = _load_cassettes()
    rows: list[tuple[dict[str, Any], DecisionCard, list[str]]] = []
    for case in cases["pipeline_cases"]:
        card, _, problems = _run_pipeline_case(case, cassettes)
        rows.append((case, card, problems))
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

    for case, card, problems in samples:
        local = rubric.score(card)
        for dim, entry in local.items():
            per_dim[dim].append(entry["score"])
        focal = _GROUP_FOCAL.get(case["group"])
        hits = cross.cross_check(card, focal_mm=focal) if focal else []
        findings.extend(hits)
        row = {
            "id": case["id"],
            "group": case["group"],
            "local": local,
            "semantic_pass": not problems,
            "semantic_problems": problems,
            "cross_check": hits,
            "conclusion": card.conclusion[:60],
        }
        rows.append(row)

    if use_llm:
        settings = load_settings()
        if not settings.has_api_key:
            raise RuntimeError("--llm-judge 需要真实 API Key（检查 .env）")
        with tempfile.TemporaryDirectory(prefix="lt_evals_judge_") as directory:
            services = build_application(settings.model_copy(update={"data_dir": directory}))
            try:
                scope = services.create_scope(RequestContext())
                judge = LLMJudge(
                    lambda prompt: scope.runtime.reason(
                        prompt, system="你是严格的评估判官。", temperature=0.2
                    )
                )
                for case, card, problems in samples[:limit]:
                    if problems:
                        continue
                    llm_score = judge.score(card)
                    rows.append(
                        {"id": f"{case['id']}-llm", "group": case["group"], "llm_judge": llm_score}
                    )
                    for dim, entry in llm_score.items():
                        llm_scores[dim].append(entry["score"])
            finally:
                services.close()

    means = _mean(per_dim)
    means_llm = _mean(llm_scores) if use_llm and llm_scores.get(next(iter(per_dim))) else {}
    previous = _latest_l3()
    diff = {
        dim: round(means[dim] - (previous["means"].get(dim, 0) if previous else 0), 2)
        for dim in means
    }
    elapsed = round(time.perf_counter() - started, 2)

    report: dict[str, Any] = {
        "level": "L3",
        "ts": _now_iso(),
        "elapsed_s": elapsed,
        "samples": len(samples),
        "semantic_pass": sum(not problems for _, _, problems in samples),
        "means": means,
        "means_llm": means_llm,
        "diff_vs_last": diff,
        "cross_check_findings": findings,
        "rows": rows,
        "llm_judge_used": use_llm,
        "llm_judge_count": sum("llm_judge" in row for row in rows),
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
        print(
            f"L2 黄金回归：pipeline {report['pipeline_pass']}/{report['pipeline_total']}，"
            f"tool {report['tool_pass']}/{report['tool_total']}，E6 归档 {report['e6_archived']}，"
            f"耗时 {report['elapsed_s']}s，LLM 调用 {report['llm_calls']}"
        )
        for row in report["pipeline_details"]:
            mark = "PASS" if row["pass"] else "FAIL"
            print(
                f"  [{mark}] {row['id']} {row['group']} {row['request'][:24]}"
                f" conf={row['confidence']} evidence={row['evidence']} spots={row['locations']}"
                + (f" 违规：{'；'.join(row['problems'])}" if row["problems"] else "")
            )
        for row in report["tool_details"]:
            mark = "PASS" if row["pass"] else "FAIL"
            print(
                f"  [{mark}] {row['id']} {row['name']}"
                + (f" 违规：{'；'.join(row['problems'])}" if row["problems"] else "")
            )
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
    parser = argparse.ArgumentParser(
        prog="evals.runner", description="LightTrail 评估 runner（L2/L3）"
    )
    parser.add_argument("--level", required=True, choices=["L2", "L3"])
    parser.add_argument(
        "--record", action="store_true", help="L2：真实 Key 录制缺失 cassette（配额克制）"
    )
    parser.add_argument("--group", action="append", help="只跑指定 group（可重复）")
    parser.add_argument(
        "--llm-judge", action="store_true", help="L3：启用真实 LLM 判官（≤limit 条）"
    )
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
