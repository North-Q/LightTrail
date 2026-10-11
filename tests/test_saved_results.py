"""U3 实际结果、重建应用、历史上下文和用户边界的离线回归。"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from evals.fake_data import FakeDispatcher
from lighttrail.api.app import ApiDeps, create_app
from lighttrail.api.routes import get_request_context
from lighttrail.api.session import SessionManager
from lighttrail.composition import build_application
from lighttrail.config import Settings
from lighttrail.contracts.context import RequestContext
from lighttrail.contracts.models import DecisionCard
from lighttrail.infra.redact import register_secrets

NOW = datetime(2026, 10, 6, 8, tzinfo=timezone.utc)


class RecordingModel:
    """记录真正收到的消息，并给出刻意过于乐观的综合结果。"""

    def __init__(self) -> None:
        """建立独立调用账本。"""
        self.messages: list[list[dict[str, Any]]] = []

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        """提供可解析的意图或综合输出，不访问供应商。"""
        self.messages.append(messages)
        content = (
            json.dumps(
                {
                    "intent": {"subject_type": "火烧云", "time_hint": "今晚", "mode": "live"},
                    "confidence": 0.9,
                    "reason": "离线续问",
                },
                ensure_ascii=False,
            )
            if "意图解析器" in str(messages)
            else json.dumps(
                {
                    "conclusion": "合成原方案，不可重新生成替代恢复。",
                    "verdict": "go",
                    "confidence": "high",
                    "evidence": [],
                },
                ensure_ascii=False,
            )
        )
        return {"role": "assistant", "content": content}

    async def acall(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        """共用计数的异步替身。"""
        return self.chat(messages, **kwargs)

    def queue_position(self) -> int:
        """离线请求无需排队。"""
        return 0


def offline_app(
    root: Path, *, user: str = "local", failed: bool = False
) -> tuple[Any, Any, RecordingModel, list[str]]:
    """从生产装配重建应用，所有数据采集都经可计数替身。"""
    model = RecordingModel()
    calls: list[str] = []
    fake = FakeDispatcher(now=datetime(2026, 10, 6, 7, tzinfo=timezone.utc))

    def dispatch(name: str, args: str) -> str:
        """保存采集账本，恢复时必须保持零调用。"""
        calls.append(name)
        return (
            json.dumps({"error": "合成数据不可用"}, ensure_ascii=False)
            if failed
            else fake(name, args)
        )

    services = build_application(
        Settings(_env_file=None, data_dir=str(root), reason_thinking=False),
        client=model,
        dispatch=dispatch,
    )
    app = create_app(ApiDeps(services, SessionManager(root)))
    context = RequestContext(user_id=user, requested_at=NOW)
    app.dependency_overrides[get_request_context] = lambda: context
    return app, services, model, calls


def frames(response: Any) -> list[dict[str, Any]]:
    """解析完整 SSE，保留终态和保存状态。"""
    return [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]


async def seed(client: AsyncClient) -> dict[str, Any]:
    """通过正常入口创建合成收藏。"""
    response = await client.post(
        "/api/favorite-spots",
        json={
            "name": "合成西岸",
            "latitude": 0,
            "longitude": 0,
            "coordinate_system": "WGS84",
            "tz_offset": "+08:00",
            "source": "离线合成",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize("failed", [False, True])
async def test_restart_restore_followup_and_versions(tmp_path: Path, failed: bool) -> None:
    """重建应用只读原快照；旧证据不续期，续问收到原历史且旧版本不丢失。"""
    app, services, _, _ = offline_app(tmp_path, failed=failed)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://localhost") as client:
        spot = await seed(client)
        response = await client.post("/api/decide", json={"request": "今晚火烧云是否值得去"})
        events = frames(response)
        done = events[-1]
        assert done["saved"] is True, response.text
        sid, rid = done["session_id"], done["result_id"]
        original = (await client.get(f"/api/sessions/{sid}/results/{rid}")).json()
        assert DecisionCard.model_validate(original["card"]) == DecisionCard.model_validate(
            next(item["card"] for item in events if item["type"] == "card")
        )
        assert original["pipeline"]["data"]["目标机位"] == "合成西岸"
        updated = await client.put(
            f"/api/favorite-spots/{spot['id']}",
            json={
                "name": "已修改的合成机位",
                "latitude": 1,
                "longitude": 1,
                "coordinate_system": "WGS84",
                "tz_offset": "+08:00",
                "source": "离线合成修改",
            },
        )
        assert updated.status_code == 200
        assert (await client.get(f"/api/sessions/{sid}/results/{rid}")).json() == original
        await client.delete(f"/api/favorite-spots/{spot['id']}")
    services.close()
    app, services, model, calls = offline_app(tmp_path, failed=failed)
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://localhost") as client:
            restored = await client.get(f"/api/sessions/{sid}/results/{rid}")
            assert restored.json() == original
            assert calls == [] and model.messages == []
            listed = (await client.get("/api/sessions")).json()
            assert any(item["session_id"] == sid for item in listed)
            response = await client.post(
                "/api/decide",
                json={
                    "request": "那我需要带什么？",
                    "session_id": sid,
                    "result_id": rid,
                },
            )
            new_done = frames(response)[-1]
            assert new_done["saved"] and new_done["result_id"] != rid
            received = json.dumps(model.messages, ensure_ascii=False)
            assert "今晚火烧云是否值得去" in received
            actual_prompts = "\n".join(
                str(message.get("content", "")) for call in model.messages for message in call
            )
            assert (
                json.dumps({"role": "assistant", "content": original["text"]}, ensure_ascii=False)
                in actual_prompts
            )
            assert (await client.get(f"/api/sessions/{sid}/results/{rid}")).json() == original
            newer = (
                await client.get(f"/api/sessions/{sid}/results/{new_done['result_id']}")
            ).json()
            assert newer["version"] == 2 and newer["parent_result_id"] == rid
            response = await client.post(
                "/api/decide",
                json={
                    "request": "今晚火烧云是否值得去",
                    "session_id": sid,
                    "result_id": rid,
                    "requery": True,
                },
            )
            assert frames(response)[-1]["result_id"] not in {rid, new_done["result_id"]}
            assert len((await client.get(f"/api/sessions/{sid}")).json()["session"]["results"]) == 3
            assert (await client.get(f"/api/sessions/{sid}/results/{rid}")).json() == original
    finally:
        services.close()


async def test_failed_save_has_no_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """保存异常结束等待，但明确未保存、不投递成功卡片。"""
    app, services, _, _ = offline_app(tmp_path)

    def fail(*args: Any) -> None:
        """模拟只读磁盘。"""
        raise OSError("合成磁盘不可写")

    monkeypatch.setattr(app.state.deps.sessions, "save", fail)
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://localhost") as client:
            await seed(client)
            events = frames(
                await client.post("/api/decide", json={"request": "今晚火烧云是否值得去"})
            )
            assert events[-1]["type"] == "done" and events[-1]["saved"] is False
            assert not events[-1].get("result_id")
            assert any(item["type"] == "error" for item in events)
            assert not any(item["type"] == "card" for item in events)
    finally:
        services.close()


async def test_user_boundary_and_legacy(tmp_path: Path) -> None:
    """旧会话缺字段可读，稳定旧版本无新时间；另一个用户不可列出或读取。"""
    directory = tmp_path / "sessions"
    directory.mkdir()
    raw = {
        "session_id": "legacy-id",
        "user_id": "alice",
        "updated_at": "2020-01-01T00:00:00Z",
        "history": [{"role": "user", "content": "旧请求"}],
        "workspace": {"last_card": {"conclusion": "旧原卡", "confidence": "low", "evidence": []}},
    }
    (directory / "legacy-id.json").write_text(json.dumps(raw), encoding="utf-8")
    app, services, model, calls = offline_app(tmp_path, user="alice")
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://localhost") as client:
            detail = (await client.get("/api/sessions/legacy-id")).json()["session"]
            legacy = detail["results"][0]
            assert legacy["result_id"] == "legacy" and legacy["created_at"] == raw["updated_at"]
            assert legacy["card"]["evaluated_at"] == ""
            assert (await client.get("/api/sessions/legacy-id/results/legacy")).status_code == 200
            assert not model.messages and not calls
    finally:
        services.close()
    app, services, _, _ = offline_app(tmp_path, user="bob")
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://localhost") as client:
            assert (await client.get("/api/sessions")).json() == []
            assert (await client.get("/api/sessions/legacy-id")).status_code == 404
            assert (await client.get("/api/sessions/legacy-id/results/legacy")).status_code == 404
            assert (
                await client.post(
                    "/api/decide", json={"request": "今晚火烧云", "session_id": "legacy-id"}
                )
            ).status_code == 404
    finally:
        services.close()


async def test_saved_plan_restart_and_followup(tmp_path: Path) -> None:
    """保存生产 SpotPlan；删除收藏后仍只读恢复原计划并携带历史续问。"""
    app, services, _, _ = offline_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://localhost") as client:
        spot = await seed(client)
        response = await client.post(
            "/api/saved-spot-plans",
            json={
                "request": {
                    "subject_type": "日落",
                    "target_date": "2026-10-06",
                    "tz_offset": "+08:00",
                    "spot": {"name": spot["name"], "site_id": spot["id"]},
                }
            },
        )
        assert response.status_code == 200, response.text
        original = response.json()
        assert original["plan"]["selected_date"] == "2026-10-06"
        assert original["plan"]["site"]["name"] == "合成西岸"
        await client.delete(f"/api/favorite-spots/{spot['id']}")
    services.close()
    app, services, model, calls = offline_app(tmp_path)
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://localhost") as client:
            path = f"/api/sessions/{original['session_id']}/results/{original['result_id']}"
            assert (await client.get(path)).json() == original
            assert not model.messages and not calls
            response = await client.post(
                "/api/decide",
                json={
                    "request": "那晚应该带什么？",
                    "session_id": original["session_id"],
                    "result_id": original["result_id"],
                },
            )
            assert frames(response)[-1]["saved"]
            received = "\n".join(str(m.get("content", "")) for call in model.messages for m in call)
            assert (
                "2026-10-06" in received and "合成西岸" in received and "shoot_window" in received
            )
            assert (await client.get(path)).json() == original
    finally:
        services.close()


async def test_plan_save_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """计划保存失败返回明确错误，不返回伪保存成功。"""
    app, services, _, _ = offline_app(tmp_path)

    def fail(*args: Any) -> None:
        """模拟写入失败。"""
        raise OSError("合成磁盘已满")

    monkeypatch.setattr(app.state.deps.sessions, "save", fail)
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://localhost") as client:
            spot = await seed(client)
            response = await client.post(
                "/api/saved-spot-plans",
                json={
                    "request": {
                        "subject_type": "日落",
                        "target_date": "2026-10-06",
                        "tz_offset": "+08:00",
                        "spot": {"name": spot["name"], "site_id": spot["id"]},
                    }
                },
            )
            assert response.status_code == 500 and "保存失败" in response.text
            assert (await client.get("/api/sessions")).json() == []
    finally:
        services.close()


def test_access_log_format_preserves_private_key_guard() -> None:
    """真实 Uvicorn 日志格式器可解析参数，假 Key 在原记录与输出中均已隐藏。"""
    from uvicorn.logging import AccessFormatter

    key = "u3-private-log-key-synthetic"
    register_secrets(key)
    logger = logging.getLogger("uvicorn.access")
    record = logger.makeRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:1", "GET", "/api/sessions?credential=" + key, "1.1", 404),
        None,
    )
    assert len(record.args) == 5
    formatted = AccessFormatter(
        '%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False
    ).format(record)
    assert key not in formatted and key not in str(record.__dict__)
    assert "404" in formatted and "/api/sessions" in formatted


async def test_next_day_restore_and_requery(tmp_path: Path) -> None:
    """跨到次日只读仍为原日期；显式重查才使用新目标日期，旧结果不续期。"""
    app, services, _, _ = offline_app(tmp_path)
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://localhost") as client:
            await seed(client)
            done = frames(
                await client.post("/api/decide", json={"request": "今晚火烧云是否值得去"})
            )[-1]
            path = f"/api/sessions/{done['session_id']}/results/{done['result_id']}"
            original = (await client.get(path)).json()
    finally:
        services.close()
    app, services, model, calls = offline_app(tmp_path)
    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user_id="local", requested_at=NOW + timedelta(days=1)
    )
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://localhost") as client:
            assert (await client.get(path)).json() == original
            assert not model.messages and not calls
            done2 = frames(
                await client.post(
                    "/api/decide",
                    json={
                        "request": "今晚火烧云是否值得去",
                        "session_id": done["session_id"],
                        "result_id": done["result_id"],
                        "requery": True,
                    },
                )
            )[-1]
            newer = (
                await client.get(f"/api/sessions/{done['session_id']}/results/{done2['result_id']}")
            ).json()
            assert newer["card"]["target_date"] == "2026-10-07"
            assert original["card"]["target_date"] == "2026-10-06"
            assert (await client.get(path)).json() == original
    finally:
        services.close()


def test_concurrent_versions_are_not_overwritten(tmp_path: Path) -> None:
    """同一会话的并发保存保留每个完成结果，恢复快照没有别名污染。"""
    manager = SessionManager(tmp_path)
    session = manager.create(user_id="local")
    snapshots = [manager.get(session.session_id) for _ in range(8)]

    def save(index: int) -> str:
        """独立请求保存各自实际文本。"""
        result = manager.save_result(
            snapshots[index], kind="chat", request={"message": str(index)}, text=str(index)
        )
        return result.result_id

    with ThreadPoolExecutor(max_workers=4) as executor:
        ids = list(executor.map(save, range(8)))
    restored = SessionManager(tmp_path).get(session.session_id)
    assert len(set(ids)) == 8 and len(restored.results) == 8
    assert {item["version"] for item in restored.results} == set(range(1, 9))
    assert {item["text"] for item in restored.results} == {str(i) for i in range(8)}


async def test_unknown_version_rejected_before_call(tmp_path: Path) -> None:
    """不存在或来自别的会话的版本不静默续问最新历史，不产生模型调用。"""
    app, services, model, calls = offline_app(tmp_path)
    try:
        session = app.state.deps.sessions.create(user_id="local")
        app.state.deps.sessions.save(session)
        async with AsyncClient(transport=ASGITransport(app), base_url="http://localhost") as client:
            response = await client.post(
                "/api/decide",
                json={
                    "request": "今晚火烧云",
                    "session_id": session.session_id,
                    "result_id": "missing-version",
                },
            )
            assert response.status_code == 404 and not model.messages and not calls
    finally:
        services.close()


def test_legacy_save_cannot_erase_versions(tmp_path: Path) -> None:
    """尚未写版本的旧入口持有过时会话副本，也不能抹掉已保存结果。"""
    manager = SessionManager(tmp_path)
    session = manager.create(user_id="local")
    stale = manager.get(session.session_id)
    original = manager.save_result(
        session, kind="chat", request={"message": "原请求"}, text="原结果"
    )
    stale.history = [{"role": "assistant", "content": "后来的旧入口结果"}]
    manager.save(stale)
    results = SessionManager(tmp_path).get(session.session_id).saved_results()
    assert any(
        item["result_id"] == original.result_id and item["text"] == "原结果" for item in results
    )


async def test_decision_history_wiring_control(tmp_path: Path) -> None:
    """旧 API 的无历史装配对照丢失背景，显式加载后真正进入模型消息。"""
    import asyncio

    app, services, model, _ = offline_app(tmp_path)
    marker = "合成历史唯一标识-已经决定携带三脚架"
    session = app.state.deps.sessions.create(user_id="local")
    session.history = [{"role": "assistant", "content": marker}]
    app.state.deps.sessions.save(session)
    context = RequestContext(user_id="local", session_id=session.session_id, requested_at=NOW)
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://localhost") as client:
            await seed(client)
        scope = services.create_scope(context)
        await asyncio.to_thread(scope.orchestrator.plan, "今晚火烧云是否值得去")
        assert marker not in json.dumps(model.messages, ensure_ascii=False)
        scope = services.create_scope(context)
        scope.orchestrator.load_history(app.state.deps.sessions.get(session.session_id).history)
        model.messages.clear()
        await asyncio.to_thread(scope.orchestrator.plan, "今晚火烧云是否值得去")
        assert marker in json.dumps(model.messages, ensure_ascii=False)
    finally:
        services.close()
