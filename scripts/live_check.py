"""真实联调自检脚本：CLI 管线 + 五个 Web 端点一次过（真实 Key，配额克制）。

用法::

    .venv\\Scripts\\python.exe scripts/live_check.py            # 全量（自动挑 data/photos 里 <10MB 的照片）
    .venv\\Scripts\\python.exe scripts/live_check.py --skip-cli --skip-photo
    .venv\\Scripts\\python.exe scripts/live_check.py --port 8790 --photo data/photos/xxx.JPG

设计要点（把 B4 联调踩过的坑固化成守卫）：
- **端口守卫**：目标端口若已有后端在响应就中止——本机曾有一个 9 月 9 日启动的残留旧后端占用 8765，
  Vite 代理静默把请求喂给旧代码，把「契约没生效」演成假 bug；
- **契约守卫**：起服务后先校验 `/openapi.json` 含 `CardEvent` / `ConfidenceDetail`，旧代码直接判失败；
- **状态可还原**：档案往返用临时值写入，跑完按原样还原（原本不存在就删掉该文件）；
- 全流程真实 LLM 调用（约 6–8 次），**只在批次收口的真实联调窗口跑**，不进 pytest（测试不触网）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
_PHOTOS_DIR = ROOT / "data" / "photos"
_MAX_PHOTO_BYTES = 10 * 1024 * 1024
_REQUIRED_SCHEMAS = ("CardEvent", "ConfidenceDetail")
_REQUIRED_SSE = ("queued", "step", "tool_call", "tool_result", "card", "done")
_VERDICTS = ("go", "wait", "risk", "")


class LiveCheckError(RuntimeError):
    """联调检查失败（脚本级自定义异常，不裸 except）。"""


def _configure_stdio() -> None:
    """让本脚本输出对不可编码字符容错（Windows GBK 控制台下符号会崩，同 cli.py）。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(errors="replace")


def _log(message: str) -> None:
    """打印一行进度（联调脚本面向人看，直接 print）。"""
    print(message, flush=True)


def _probe(url: str, *, timeout: float = 3.0) -> bool:
    """探测 URL 是否可访问（用于端口守卫与就绪等待）。"""
    try:
        return httpx.get(url, timeout=timeout).status_code == 200
    except httpx.HTTPError:
        return False


def _wait_ready(base: str, *, tries: int = 90) -> bool:
    """等待后端就绪（最长约 90 秒）。"""
    for _ in range(tries):
        if _probe(f"{base}/api/profile"):
            return True
        time.sleep(1.0)
    return False


def _pick_photo() -> Path:
    """挑一张可上传的照片（data/photos 里 ≤10MB 的 jpg/png）。

    Returns:
        照片路径。

    Raises:
        LiveCheckError: 没有可用照片。
    """
    candidates = [
        path
        for path in sorted(_PHOTOS_DIR.glob("*"))
        if path.suffix.lower() in {".jpg", ".jpeg", ".png"} and path.stat().st_size <= _MAX_PHOTO_BYTES
    ]
    if not candidates:
        raise LiveCheckError(f"{_PHOTOS_DIR} 下没有 ≤10MB 的 jpg/png 照片（可用 --photo 指定）")
    return candidates[0]


def _iter_sse(client: httpx.Client, path: str, **kwargs: Any) -> list[dict[str, Any]]:
    """请求 SSE 端点并解析事件（httpx 流式读取，帧格式与前端一致）。

    Args:
        client: httpx 客户端（base_url 已指向后端）。
        path: 端点路径。
        **kwargs: 透传给 httpx（json= / files= / data=）。

    Returns:
        事件字典列表。

    Raises:
        LiveCheckError: HTTP 非 200。
    """
    events: list[dict[str, Any]] = []
    with client.stream("POST", path, **kwargs) as response:
        if response.status_code != 200:
            raise LiveCheckError(f"{path} HTTP {response.status_code}：{response.read()[:200]!r}")
        current = ""
        data_lines: list[str] = []
        for line in response.iter_lines():
            if line.startswith("event:"):
                current = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].strip())
            elif not line.strip() and current:
                events.append({"type": current, **json.loads("\n".join(data_lines))})
                current = ""
                data_lines = []
    return events


# ------ 各项检查 ------
def check_cli(question: str) -> list[str]:
    """CLI `--pipeline` 真实跑一次，返回违规清单。"""
    _log("=== ① CLI --pipeline ===")
    # 子进程强制 UTF-8 输出：GBK 控制台下管道字节是 cp936，按 utf-8 解码会认不出中文
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [PY, "-m", "lighttrail.cli", "--pipeline", question],
        capture_output=True,
        cwd=str(ROOT),
        timeout=600,
        check=False,
        env=env,
    )
    stdout = proc.stdout.decode("utf-8", "replace")
    problems: list[str] = []
    if proc.returncode != 0:
        problems.append(f"CLI 返回码 {proc.returncode}")
    if "拍摄方案" not in stdout:
        problems.append("CLI 输出里没有决策卡（缺「拍摄方案」）")
    _log(f"  rc={proc.returncode} 输出长度={len(stdout)}")
    return problems


def check_decide(client: httpx.Client, question: str) -> list[str]:
    """`/api/decide` 事件序列 + 卡片契约不变量（B4-3/B4-4），返回违规清单。"""
    _log("=== ② /api/decide ===")
    events = _iter_sse(client, "/api/decide", json={"request": question, "session_id": ""})
    kinds = [event["type"] for event in events]
    _log(f"  事件序列：{kinds}")
    problems: list[str] = []
    for required in _REQUIRED_SSE:
        if required not in kinds:
            problems.append(f"缺 {required} 事件")
    results = [event for event in events if event["type"] == "tool_result"]
    with_data = [event for event in results if isinstance(event.get("data"), dict) and event["data"]]
    if not results:
        problems.append("没有 tool_result 事件")
    elif not with_data:
        problems.append("tool_result 全部没有结构化 data（前端无法按字段渲染）")
    else:
        sample = with_data[0]
        _log(f"  data 样例：{sample['name']} {json.dumps(sample['data'], ensure_ascii=False)[:120]}")
    cards = [event for event in events if event["type"] == "card"]
    if not cards:
        problems.append("没有 card 事件")
        return problems
    card = cards[0]["card"]
    detail = card.get("confidence_detail")
    _log(f"  conclusion：{str(card.get('conclusion'))[:80]}")
    _log(f"  verdict={card.get('verdict')!r} detail={json.dumps(detail, ensure_ascii=False) if detail else None}")
    if not isinstance(detail, dict):
        problems.append("卡片缺 confidence_detail（B4-3 起必填）")
    else:
        score, low, high = detail.get("score"), detail.get("low"), detail.get("high")
        if not isinstance(score, int) or not 0 <= low <= score <= high <= 100:  # type: ignore[operator]
            problems.append(f"置信度区间非法：{low}/{score}/{high}")
        counted = detail.get("high_count", 0) + detail.get("medium_count", 0) + detail.get("low_count", 0)
        if counted != len(card.get("evidence") or []):
            problems.append(f"置信度计数 {counted} 与 evidence {len(card.get('evidence') or [])} 条不一致")
    if card.get("verdict") not in _VERDICTS:
        problems.append(f"verdict 越界：{card.get('verdict')!r}")
    return problems


def check_chat(client: httpx.Client) -> list[str]:
    """`/api/chat` 自由对话：token + done，返回违规清单。"""
    _log("=== ③ /api/chat ===")
    events = _iter_sse(client, "/api/chat", json={"message": "现在几点？", "session_id": ""})
    kinds = [event["type"] for event in events]
    _log(f"  事件序列：{kinds}")
    problems: list[str] = []
    if "token" not in kinds or "done" not in kinds:
        problems.append("缺 token / done 事件")
    if "error" in kinds:
        problems.append(f"出现 error 事件：{[e.get('message') for e in events if e['type'] == 'error']}")
    return problems


def check_photo(client: httpx.Client, photo: Path) -> list[str]:
    """`/api/photos/review` 真实照片复盘（D4），返回违规清单。"""
    _log(f"=== ④ /api/photos/review（{photo.name}）===")
    started = time.perf_counter()
    events = _iter_sse(
        client,
        "/api/photos/review",
        files={"file": (photo.name, photo.read_bytes(), "image/jpeg")},
        data={"focus": "", "plan_reference": "", "session_id": ""},
    )
    elapsed = round(time.perf_counter() - started, 1)
    kinds = [event["type"] for event in events]
    _log(f"  事件序列：{kinds}（{elapsed}s）")
    problems: list[str] = []
    if "error" in kinds:
        messages = [event.get("message") for event in events if event["type"] == "error"]
        problems.append(f"复盘失败：{messages}")
    cards = [event for event in events if event["type"] == "card"]
    if not cards:
        problems.append("没有 card 事件")
        return problems
    card = cards[0]["card"]
    _log(f"  conclusion：{str(card.get('conclusion'))[:100]}")
    if card.get("confidence_detail") is None:
        problems.append("复盘卡缺 confidence_detail（模板方法应统一补全）")
    return problems


def check_profile(client: httpx.Client, data_dir: Path) -> list[str]:
    """`/api/profile` GET/PUT 往返（跑完还原原档案），返回违规清单。"""
    _log("=== ⑤ /api/profile 往返 ===")
    profile_file = data_dir / "profile.json"
    original = profile_file.read_text(encoding="utf-8") if profile_file.exists() else None
    problems: list[str] = []
    try:
        before = client.get("/api/profile").json()
        updated = client.put("/api/profile", json={"camera_body": "联调临时机身", "skill_level": "联调"}).json()
        after = client.get("/api/profile").json()
        if updated.get("camera_body") != "联调临时机身" or after.get("camera_body") != "联调临时机身":
            problems.append(f"档案写入未生效：{updated.get('camera_body')!r} / {after.get('camera_body')!r}")
        if before.get("lenses") != after.get("lenses"):
            problems.append("档案往返把 lens 列表弄丢了")
        _log(f"  PUT 生效：camera_body={after.get('camera_body')!r}")
    finally:
        if original is not None:
            profile_file.write_text(original, encoding="utf-8")
        elif profile_file.exists():
            profile_file.unlink()
        _log(f"  档案已还原（{'原文件内容' if original is not None else '原本不存在 → 已删除'}）")
    return problems


def main(argv: list[str] | None = None) -> int:
    """入口：守卫 → 起后端 → 逐项检查 → 汇总。

    Args:
        argv: 命令行参数（缺省取 sys.argv[1:]）。

    Returns:
        进程退出码（0 = 全部通过）。
    """
    parser = argparse.ArgumentParser(prog="live_check", description="LightTrail 真实联调自检")
    parser.add_argument("--port", default="8790", help="后端端口（默认 8790，避开 Vite 代理的 8765）")
    parser.add_argument("--photo", default="", help="复盘用照片路径（缺省自动挑 data/photos 里 ≤10MB 的）")
    parser.add_argument("--question", default="今晚上海火烧云值得冲吗？", help="决策/管线问题")
    parser.add_argument("--skip-cli", action="store_true", help="跳过 CLI 检查（省配额）")
    parser.add_argument("--skip-photo", action="store_true", help="跳过照片复盘（省配额）")
    _configure_stdio()
    args = parser.parse_args(argv)

    base = f"http://127.0.0.1:{args.port}"
    if _probe(f"{base}/api/profile"):
        _log(f"中止：{base} 已有后端在跑（版本未知，可能是残留旧进程）——请先结束它或换 --port。")
        return 3
    if not Path(PY).exists():
        _log(f"中止：找不到项目 venv 解释器 {PY}")
        return 4

    log_path = Path(tempfile.gettempdir()) / f"lt_live_check_{args.port}.log"
    with log_path.open("wb") as handle:
        server = subprocess.Popen(
            [PY, "-m", "uvicorn", "lighttrail.api.app:app", "--port", args.port, "--log-level", "warning"],
            cwd=str(ROOT),
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
    problems: list[str] = []
    try:
        if not _wait_ready(base):
            _log(f"后端未就绪，日志尾部：{log_path.read_text(encoding='utf-8', errors='replace')[-500:]}")
            return 1
        document = httpx.get(f"{base}/openapi.json", timeout=30).json()
        schemas = set(document["components"]["schemas"])
        missing = [name for name in _REQUIRED_SCHEMAS if name not in schemas]
        if missing:
            _log(f"中止：后端契约缺 {missing}（旧代码？）")
            return 5
        _log(f"后端契约守卫通过（{', '.join(_REQUIRED_SCHEMAS)} 均在）｜ 日志：{log_path}")

        with httpx.Client(base_url=base, timeout=600) as client:
            if not args.skip_cli:
                problems += check_cli(args.question)
            problems += check_decide(client, args.question)
            problems += check_chat(client)
            if not args.skip_photo:
                photo = Path(args.photo) if args.photo else _pick_photo()
                if not photo.is_absolute():
                    photo = ROOT / photo
                problems += check_photo(client, photo)
            problems += check_profile(client, ROOT / "data")
    finally:
        server.terminate()
        try:
            server.wait(timeout=20)
        except subprocess.TimeoutExpired:
            server.kill()

    _log("---")
    if problems:
        _log("联调失败项：")
        for problem in problems:
            _log(f"  [FAIL] {problem}")
        return 1
    _log("真实联调全部通过 [OK]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())