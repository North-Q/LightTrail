"""U5合成契约、生产API与单文件原子生命周期回归。"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from lighttrail.api.app import ApiDeps, create_app
from lighttrail.api.routes import get_request_context
from lighttrail.api.session import SavedResult, SessionManager
from lighttrail.application.feedback import FeedbackService
from lighttrail.composition import build_application
from lighttrail.config import Settings
from lighttrail.contracts.context import RequestContext
from lighttrail.contracts.feedback import RawFeedback, SaveFeedback
from lighttrail.infra.feedback import FeedbackError, FeedbackStore

RAW = {
    "departure_status": "not_went",
    "observation_status": "not_observed",
    "observation_result": None,
    "observation_scope": None,
    "note": "合成：本人未出门，无独立现场观察，现场效用无法判读。",
}


class RejectModel:
    """任何模型调用都是本批失败，不返回伪造建议。"""

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        """拒绝同步外调。"""
        raise AssertionError("反馈不得调用模型")

    async def acall(self, *args: Any, **kwargs: Any) -> Any:
        """拒绝异步外调。"""
        raise AssertionError("反馈不得调用模型")


def offline_app(root: Path) -> tuple[Any, Any, str]:
    """用真实装配与合成已落盘结果建立验收入口。

    Args:
        root: 临时数据根。
    Returns:
        应用、服务及反馈URL。
    """
    services = build_application(Settings(_env_file=None, data_dir=str(root)), client=RejectModel())
    sessions = SessionManager(root)
    session = sessions.create(user_id="owner")
    result = SavedResult(
        session_id=session.session_id,
        result_id=uuid4().hex,
        version=1,
        kind="decision",
        created_at="2026-10-11T08:00:00+08:00",
        text="合成原卡；有效期保持原样",
        trace={"valid_until": "2026-10-10T03:36:28+08:00"},
    )
    session.results = [result.model_dump(mode="json")]
    sessions.save(session)
    app = create_app(ApiDeps(services, sessions))
    app.dependency_overrides[get_request_context] = lambda: RequestContext(user_id="owner")
    return app, services, f"/api/sessions/{session.session_id}/results/{result.result_id}/feedback"


def mutation(revision: int, **values: Any) -> dict[str, Any]:
    """生成一次合成本人操作。

    Args:
        revision: 当前文档版本。
        values: 操作字段。
    Returns:
        新操作请求。
    """
    return {"expected_revision": revision, "mutation_id": uuid4().hex, **values}


@pytest.mark.parametrize("departure", ["went", "not_went", "unknown"])
@pytest.mark.parametrize("observation", ["unknown", "not_observed"])
def test_unobserved_result_must_be_null(departure: str, observation: str) -> None:
    """V01/V02：未观察在任何出门状态下都没有结果。"""
    assert (
        RawFeedback(departure_status=departure, observation_status=observation).observation_result
        is None
    )
    for invalid in (
        {"observation_result": "not_seen"},
        {"result_reason": "没看到"},
        {
            "observation_scope": {
                "place_matches_plan": "yes",
                "window_covers_plan": "yes",
                "description": "合成",
            }
        },
    ):
        with pytest.raises(ValidationError):
            RawFeedback(departure_status=departure, observation_status=observation, **invalid)


@pytest.mark.parametrize("result", ["seen", "not_seen", "uncertain"])
def test_observed_elsewhere_requires_scope(result: str) -> None:
    """V03/V04：没出门仍可在别处独立观察，但必须记录范围。"""
    with pytest.raises(ValidationError):
        RawFeedback(
            departure_status="not_went", observation_status="observed", observation_result=result
        )
    feedback = RawFeedback(
        departure_status="not_went",
        observation_status="observed",
        observation_result=result,
        observation_scope={
            "place_matches_plan": "no",
            "window_covers_plan": "unknown",
            "description": "合成：在家观察",
        },
    )
    assert feedback.observation_scope.place_matches_plan == "no"


async def test_complete_lifecycle_and_reconfirmation(tmp_path: Path) -> None:
    """V01/V05/V06/V07/V14：改反馈失效、两步重确认、撤回不留旧经验。"""
    app, services, url = offline_app(tmp_path)
    original = {p: p.read_bytes() for p in (tmp_path / "sessions").glob("*.json")}
    async with AsyncClient(transport=ASGITransport(app), base_url="http://localhost") as client:
        empty = await client.get(url)
        assert empty.json()["state"] == "none" and empty.headers["cache-control"] == "no-store"
        saved = (await client.put(url, json=mutation(0, feedback=RAW))).json()
        assert saved["record"]["feedback"]["observation_result"] is None
        assert "last_mutation" not in saved["record"]
        assert saved["last_mutation_id"] is not None
        assert saved["record"]["experience"] is None and saved["memory_written"] is False
        draft = (
            await client.put(
                url + "/experience",
                json=mutation(1, kind="planning_preference", content="合成本人偏好"),
            )
        ).json()
        exp = draft["record"]["experience"]
        confirmed = (
            await client.post(
                url + "/experience/confirm",
                json=mutation(
                    2,
                    experience_id=exp["experience_id"],
                    source_feedback_revision=1,
                    confirmed_by_user=True,
                ),
            )
        ).json()
        assert confirmed["experience_effective"]
        edited = (
            await client.put(
                url, json=mutation(3, feedback={**RAW, "departure_reason": "合成原因"})
            )
        ).json()
        assert edited["record"]["experience"]["status"] == "needs_review"
        assert edited["record"]["experience"]["confirmed_at"] is None
        assert edited["record"]["experience"]["source_feedback_revision"] == 1
        assert not edited["experience_effective"]
        assert (
            await client.post(
                url + "/experience/confirm",
                json=mutation(
                    4,
                    experience_id=exp["experience_id"],
                    source_feedback_revision=2,
                    confirmed_by_user=True,
                ),
            )
        ).status_code == 409
        draft = (
            await client.put(
                url + "/experience",
                json=mutation(4, kind="planning_preference", content=exp["content"]),
            )
        ).json()
        assert draft["record"]["experience"]["source_feedback_revision"] == 2
        confirmed = await client.post(
            url + "/experience/confirm",
            json=mutation(
                5,
                experience_id=exp["experience_id"],
                source_feedback_revision=2,
                confirmed_by_user=True,
            ),
        )
        assert confirmed.json()["experience_effective"]
        withdrawn = (await client.post(url + "/experience/withdraw", json=mutation(6))).json()
        assert withdrawn["record"]["feedback"] == edited["record"]["feedback"]
        assert withdrawn["record"]["experience"]["content"] is None
        assert withdrawn["record"]["experience"]["confirmed_at"] is None
        assert withdrawn["record"]["experience"]["withdrawn_at"] is not None
        edited = (await client.put(url, json=mutation(7, feedback=RAW))).json()
        assert edited["record"]["experience"]["status"] == "withdrawn"
        new_draft = (
            await client.put(
                url + "/experience",
                json=mutation(8, kind="planning_preference", content="合成新候选"),
            )
        ).json()
        assert new_draft["record"]["experience"]["experience_id"] != exp["experience_id"]
        tombstone = (await client.post(url + "/withdraw", json=mutation(9))).json()
        assert tombstone["record"]["feedback"] is None and tombstone["record"]["experience"] is None
        assert tombstone["record"]["feedback_revision"] == 4
        assert (await client.put(url, json=mutation(9, feedback=RAW))).status_code == 409
        refill = (await client.put(url, json=mutation(10, feedback=RAW))).json()
        assert refill["record"]["experience"] is None and refill["record"]["withdrawn_at"] is None
        assert refill["record"]["feedback_id"] == saved["record"]["feedback_id"]
    assert {p: p.read_bytes() for p in original} == original
    assert not list((tmp_path / "users").glob("*/memory/semantic.json"))
    services.close()


@pytest.mark.parametrize(
    "action", ["save", "experience", "confirm", "withdraw", "withdraw_experience"]
)
async def test_failed_replace_preserves_old_record(
    tmp_path: Path, monkeypatch: Any, action: str
) -> None:
    """V10：各阶段替换失败仍保留旧文档，明确saved=false。"""
    app, services, url = offline_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://localhost") as client:
        await client.put(url, json=mutation(0, feedback=RAW))
        draft = (
            await client.put(
                url + "/experience",
                json=mutation(1, kind="planning_preference", content="合成候选"),
            )
        ).json()
        path = next((tmp_path / "users").rglob("result-feedback/*/*.json"))
        previous = path.read_bytes()

        def fail(*args: Any) -> None:
            """模拟无法替换文件。"""
            raise OSError("合成磁盘失败")

        monkeypatch.setattr(services.feedback.store, "_replace", fail)
        suffix = {
            "save": "",
            "experience": "/experience",
            "confirm": "/experience/confirm",
            "withdraw": "/withdraw",
            "withdraw_experience": "/experience/withdraw",
        }[action]
        values = {
            "save": {"feedback": RAW},
            "experience": {"kind": "planning_preference", "content": "新候选"},
            "confirm": {
                "experience_id": draft["record"]["experience"]["experience_id"],
                "source_feedback_revision": 1,
                "confirmed_by_user": True,
            },
        }.get(action, {})
        response = await client.request(
            "PUT" if action in ("save", "experience") else "POST",
            url + suffix,
            json=mutation(2, **values),
        )
        assert response.status_code == 500 and response.json()["detail"]["saved"] is False
        assert path.read_bytes() == previous and not list(path.parent.glob("*.tmp"))
    services.close()


async def test_first_write_failure_and_photo_rejection(tmp_path: Path, monkeypatch: Any) -> None:
    """V10/V15：首存写入失败无反馈；照片、身份额外字段拒绝。"""
    app, services, url = offline_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://localhost") as client:
        for values in (
            {"photo_refs": ["photo-id"]},
            {"user_id": "other"},
            {"feedback": {**RAW, "observation_result": "not_seen"}},
        ):
            assert (
                await client.put(url, json=mutation(0, feedback=RAW) | values)
            ).status_code == 422

        def fail(*args: Any, **kwargs: Any) -> None:
            """模拟写临时文件之前失败。"""
            raise OSError("合成磁盘错误")

        monkeypatch.setattr(Path, "mkdir", fail)
        response = await client.put(url, json=mutation(0, feedback=RAW))
        assert response.status_code == 500
        assert (await client.get(url)).json()["state"] == "none"
    services.close()


async def test_cross_user_rejected_before_feedback_access(tmp_path: Path, monkeypatch: Any) -> None:
    """V08：他人读改撤回确认均404，仓库没有读取或写入机会。"""
    app, services, url = offline_app(tmp_path)
    app.dependency_overrides[get_request_context] = lambda: RequestContext(user_id="other")

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        """跨用户路径不得触及仓库。"""
        raise AssertionError("跨用户触及仓库")

    monkeypatch.setattr(services.feedback.store, "read", forbidden)
    monkeypatch.setattr(services.feedback.store, "transact", forbidden)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://localhost") as client:
        for method, suffix, body in [
            ("GET", "", None),
            ("PUT", "", mutation(0, feedback=RAW)),
            ("POST", "/withdraw", mutation(0)),
            ("POST", "/experience/withdraw", mutation(0)),
            ("PUT", "/experience", mutation(0, kind="planning_preference", content="合成")),
            (
                "POST",
                "/experience/confirm",
                mutation(
                    0, experience_id=uuid4().hex, source_feedback_revision=1, confirmed_by_user=True
                ),
            ),
        ]:
            assert (await client.request(method, url + suffix, json=body)).status_code == 404
    assert not list(tmp_path.glob("users/other/result-feedback/**/*.json"))
    services.close()


def test_concurrent_cas_and_latest_mutation_dedup(tmp_path: Path) -> None:
    """V11：两个同版本编辑只胜一个；最新操作重放不增版。"""
    service = FeedbackService(FeedbackStore(tmp_path))
    sid, rid = uuid4().hex, uuid4().hex
    source = {"session_id": sid, "result_id": rid, "text": "合成"}
    body = SaveFeedback.model_validate(mutation(0, feedback=RAW))
    saved = service.mutate("owner", sid, rid, "save", body, lambda: source)
    assert service.mutate("owner", sid, rid, "save", body, lambda: source) == saved
    changed = body.model_copy(
        update={"feedback": RawFeedback.model_validate({**RAW, "note": "不同内容"})}
    )
    with pytest.raises(FeedbackError, match="同一操作"):
        service.mutate("owner", sid, rid, "save", changed, lambda: source)

    def edit(index: int) -> int:
        """并发提交同一个旧版本。"""
        try:
            return service.mutate(
                "owner",
                sid,
                rid,
                "save",
                SaveFeedback.model_validate(mutation(1, feedback={**RAW, "note": str(index)})),
                lambda: source,
            ).revision
        except FeedbackError as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(edit, range(2))) == [2, 409]
    with pytest.raises(FeedbackError):
        service.mutate("owner", sid, rid, "save", body, lambda: source)


async def test_legacy_changed_missing_corrupt_and_readonly_restart(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """V09/V12：legacy变源拒绝、损坏不清空、重建及只读不落盘。"""
    app, services, url = offline_app(tmp_path)
    sessions = app.state.deps.sessions
    sid = url.split("/")[3]
    path = tmp_path / "sessions" / f"{sid}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["results"] = []
    data["history"] = [
        {"role": "user", "content": "合成计划"},
        {"role": "assistant", "content": "合成原卡"},
    ]
    path.write_text(json.dumps(data), encoding="utf-8")
    url = f"/api/sessions/{sid}/results/legacy/feedback"

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        """反馈读取不得进入缓存或工具。"""
        raise AssertionError("只读恢复写入或缓存访问")

    monkeypatch.setattr(sessions, "get", forbidden)
    monkeypatch.setattr(sessions, "_write_session", forbidden)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://localhost") as client:
        assert (await client.get(url)).json()["state"] == "none"
        assert not list(tmp_path.glob("users/*/result-feedback"))
        await client.put(url, json=mutation(0, feedback=RAW))
        feedback_path = next(tmp_path.glob("users/*/result-feedback/*/*.json"))
        before = feedback_path.read_bytes()
        restarted = create_app(ApiDeps(services, SessionManager(tmp_path)))
        restarted.dependency_overrides[get_request_context] = lambda: RequestContext(
            user_id="owner"
        )
        async with AsyncClient(
            transport=ASGITransport(restarted), base_url="http://localhost"
        ) as fresh:
            assert (await fresh.get(url)).json()["revision"] == 1
        assert feedback_path.read_bytes() == before
        data["history"][-1]["content"] = "合成原结果改变"
        path.write_text(json.dumps(data), encoding="utf-8")
        assert (await client.get(url)).json()["source_state"] == "changed"
        assert (await client.put(url, json=mutation(1, feedback=RAW))).status_code == 409
        data["history"] = []
        path.write_text(json.dumps(data), encoding="utf-8")
        assert (await client.get(url)).json()["source_state"] == "missing"
        assert (await client.post(url + "/withdraw", json=mutation(1))).json()[
            "state"
        ] == "withdrawn"
        tombstone = feedback_path.read_bytes()
        for corrupt in (b"{", json.dumps({**json.loads(tombstone), "schema_version": 2}).encode()):
            feedback_path.write_bytes(corrupt)
            assert (await client.get(url)).status_code == 503
            assert (await client.put(url, json=mutation(2, feedback=RAW))).status_code == 503
            assert feedback_path.read_bytes() == corrupt
    services.close()


async def test_observation_experience_needs_observation_and_explicit_confirmation(
    tmp_path: Path,
) -> None:
    """V14：草稿独立，未观察不能确认观察经验。"""
    app, services, url = offline_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://localhost") as client:
        await client.put(url, json=mutation(0, feedback=RAW))
        draft = (
            await client.put(
                url + "/experience",
                json=mutation(1, kind="observation_note", content="合成观察笔记"),
            )
        ).json()
        body = mutation(
            2,
            experience_id=draft["record"]["experience"]["experience_id"],
            source_feedback_revision=1,
            confirmed_by_user=True,
        )
        assert (await client.post(url + "/experience/confirm", json=body)).status_code == 422
        assert (
            await client.post(
                url + "/experience/confirm", json={**body, "confirmed_by_user": False}
            )
        ).status_code == 422
    services.close()


async def test_memory_files_and_injections_unchanged(tmp_path: Path, monkeypatch: Any) -> None:
    """V14：确认及撤回不调用记忆写端口，注入与已有文件均不变。"""
    app, services, url = offline_app(tmp_path)
    context = RequestContext(user_id="owner")
    before_injections = services.memory.build_injections(context, "合成经验")
    directory = tmp_path / "users" / "owner" / "memory"
    before = {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()}

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        """不允许反馈隐含记忆写授权。"""
        raise AssertionError("U5接入了记忆写端口")

    for name in ("add_event", "semantic_propose", "semantic_confirm", "sediment_semantics"):
        monkeypatch.setattr(services.memory, name, forbidden)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://localhost") as client:
        await client.put(url, json=mutation(0, feedback=RAW))
        draft = (
            await client.put(
                url + "/experience",
                json=mutation(1, kind="planning_preference", content="合成经验"),
            )
        ).json()
        confirmed = await client.post(
            url + "/experience/confirm",
            json=mutation(
                2,
                experience_id=draft["record"]["experience"]["experience_id"],
                source_feedback_revision=1,
                confirmed_by_user=True,
            ),
        )
        assert confirmed.json()["memory_written"] is False
        await client.post(url + "/withdraw", json=mutation(3))
    assert services.memory.build_injections(context, "合成经验") == before_injections
    assert {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()} == before
    services.close()


@pytest.mark.parametrize("withdrawn", [False, True])
async def test_new_python_process_restores_only_get(tmp_path: Path, withdrawn: bool) -> None:
    """V06/V09：独立Python进程只读恢复原卡及反馈或墓碑。"""
    app, services, url = offline_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://localhost") as client:
        await client.put(url, json=mutation(0, feedback=RAW))
        if withdrawn:
            await client.post(url + "/withdraw", json=mutation(1))
        expected = (await client.get(url)).json()
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    services.close()
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "LIGHTTRAIL_DATA_DIR": str(tmp_path),
        "LLM_API_KEY": "offline-u5-child",
        "ECNU_API_KEY": "offline-u5-child",
    }
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-X", "utf8", str(Path(__file__).with_name("u5_restore_probe.py")), url],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    recovered = json.loads(result.stdout)
    assert recovered["feedback"] == expected
    assert recovered["external_attempts"] == 0 and recovered["model_calls"] == 0
    assert {p: p.read_bytes() for p in before} == before


async def test_no_record_withdraw_and_unsaved_cache_have_no_writes(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """V09/V12：空撤回不建文件；只读不触发未保存缓存会话落盘。"""
    app, services, url = offline_app(tmp_path)
    manager = SessionManager(tmp_path, max_sessions=1)
    unsaved = manager.create(user_id="owner")
    app.state.deps.sessions = manager

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        """任何缓存访问或淘汰落盘均失败。"""
        raise AssertionError("反馈触发缓存副作用")

    monkeypatch.setattr(manager, "get", forbidden)
    monkeypatch.setattr(manager, "_persist_evicted", forbidden)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://localhost") as client:
        assert (await client.get(url)).json()["state"] == "none"
        assert (await client.post(url + "/withdraw", json=mutation(0))).json()["state"] == "none"
        assert (await client.post(url + "/experience/withdraw", json=mutation(0))).json()[
            "state"
        ] == "none"
    assert not (tmp_path / "sessions" / f"{unsaved.session_id}.json").exists()
    assert not list(tmp_path.glob("users/*/result-feedback"))
    services.close()
