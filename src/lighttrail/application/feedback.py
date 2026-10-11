"""U5反馈及本人经验裁决，不调用工具、模型、照片或记忆服务。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from lighttrail.contracts.feedback import (
    ConfirmExperience,
    FeedbackMutation,
    FeedbackRecord,
    FeedbackTarget,
    FeedbackView,
    LastMutation,
    LocalExperience,
    SaveExperience,
    SaveFeedback,
)
from lighttrail.infra.feedback import FeedbackError, FeedbackStore, json_digest, source_fingerprint

FeedbackAction = Literal["save", "withdraw", "experience", "confirm", "withdraw_experience"]


class FeedbackService:
    """独立本地反馈用例；持久化依赖显式注入。"""

    def __init__(self, store: FeedbackStore) -> None:
        """保存仓库依赖。

        Args:
            store: 应用级共享仓库。
        """
        self.store = store

    def read(
        self, user_id: str, session_id: str, result_id: str, source: dict[str, Any] | None
    ) -> FeedbackView:
        """只读恢复，源缺失且没有反馈时返回404。

        Args:
            user_id: 已核验的请求用户。
            session_id: 已核验归属的会话。
            result_id: 目标结果。
            source: 当前已落盘结果，缺失为None。
        Returns:
            当前反馈及派生资格。
        """
        record = self.store.read(user_id, session_id, result_id)
        return self._view(record, source)

    def mutate(
        self,
        user_id: str,
        session_id: str,
        result_id: str,
        action: FeedbackAction,
        body: FeedbackMutation,
        read_source: Callable[[], dict[str, Any] | None],
    ) -> FeedbackView:
        """执行一个原子操作，源校验与版本裁决在事务中完成。

        Args:
            user_id: 已核验用户。
            session_id: 已核验归属的会话。
            result_id: 结果引用。
            action: 明确操作类型。
            body: 已校验请求。
            read_source: 不触发缓存落盘的归属校验及读取。
        Returns:
            已保存状态，记忆写入恒为False。
        """
        # 去重返回也要重新校验源和归属，但旧操作不能改变最新文档。
        source = read_source()
        digest = json_digest({"action": action, "body": body.model_dump(mode="json")})

        def change(previous: FeedbackRecord | None) -> FeedbackRecord | None:
            """裁决锁内最新记录，不修改传入对象。"""
            nonlocal source
            source = read_source()
            view = self._view(previous, source)
            if (
                action not in ("withdraw", "withdraw_experience")
                and view.source_state != "unchanged"
            ):
                raise FeedbackError(409, "原结果已改变或缺失，仅可回看或撤回")
            now = datetime.now(timezone.utc)
            if action == "save":
                assert isinstance(body, SaveFeedback)
                fingerprint = source_fingerprint(source)  # type: ignore[arg-type]
                if previous is None:
                    return FeedbackRecord(
                        feedback_id=uuid4(),
                        user_id=user_id,
                        target=FeedbackTarget(
                            session_id=session_id,
                            result_id=result_id,
                            source_fingerprint=fingerprint,
                        ),
                        status="active",
                        revision=1,
                        feedback_revision=1,
                        feedback=body.feedback,
                        created_at=now,
                        updated_at=now,
                        last_mutation=LastMutation(id=body.mutation_id, request_digest=digest),
                    )
                updates: dict[str, Any] = {
                    "status": "active",
                    "feedback": body.feedback,
                    "feedback_revision": previous.feedback_revision + 1,
                    "withdrawn_at": None,
                }
                experience = previous.experience
                if experience is not None and experience.status != "withdrawn":
                    updates["experience"] = experience.model_copy(
                        update={
                            "status": "needs_review",
                            "confirmed_at": None,
                        }
                    )
            elif action == "withdraw":
                if previous is None or previous.status == "withdrawn":
                    return previous
                updates = {
                    "status": "withdrawn",
                    "feedback": None,
                    "experience": None,
                    "photo_refs": [],
                    "withdrawn_at": now,
                    "feedback_revision": previous.feedback_revision + 1,
                }
            else:
                if previous is None or previous.status != "active":
                    if action == "withdraw_experience":
                        return previous
                    raise FeedbackError(409, "请先保存当前原始反馈")
                experience = previous.experience
                if action == "experience":
                    assert isinstance(body, SaveExperience)
                    new_id = (
                        uuid4()
                        if experience is None or experience.status == "withdrawn"
                        else experience.experience_id
                    )
                    updates = {
                        "experience": LocalExperience(
                            experience_id=new_id,
                            kind=body.kind,
                            content=body.content,
                            status="draft",
                            source_feedback_revision=previous.feedback_revision,
                        )
                    }
                elif action == "confirm":
                    assert isinstance(body, ConfirmExperience)
                    if (
                        experience is None
                        or experience.status != "draft"
                        or experience.experience_id != body.experience_id
                        or experience.source_feedback_revision != previous.feedback_revision
                        or body.source_feedback_revision != previous.feedback_revision
                    ):
                        raise FeedbackError(
                            409, "仅能确认绑定当前反馈版本的草稿，请重新核对并保存候选"
                        )
                    if (
                        experience.kind == "observation_note"
                        and previous.feedback is not None
                        and previous.feedback.observation_status != "observed"
                    ):
                        raise FeedbackError(422, "本次没有本人观察，不能确认观察经验")
                    updates = {
                        "experience": experience.model_copy(
                            update={
                                "status": "confirmed",
                                "confirmed_at": now,
                            }
                        )
                    }
                else:
                    if experience is None or experience.status == "withdrawn":
                        return previous
                    updates = {
                        "experience": experience.model_copy(
                            update={
                                "status": "withdrawn",
                                "content": None,
                                "confirmed_at": None,
                                "withdrawn_at": now,
                            }
                        )
                    }
            assert previous is not None
            updates.update(
                revision=previous.revision + 1,
                updated_at=now,
                last_mutation=LastMutation(id=body.mutation_id, request_digest=digest),
            )
            return previous.model_copy(update=updates)

        record = self.store.transact(
            user_id,
            session_id,
            result_id,
            expected_revision=body.expected_revision,
            mutation_id=body.mutation_id,
            request_digest=digest,
            change=change,
        )
        return self._view(record, source)

    def _view(self, record: FeedbackRecord | None, source: dict[str, Any] | None) -> FeedbackView:
        """从当前源和记录派生有效性，不写盘。

        Args:
            record: 当前反馈。
            source: 当前结果。
        Returns:
            API状态。
        Raises:
            FeedbackError: 结果与反馈均不存在。
        """
        if source is None and record is None:
            raise FeedbackError(404, "结果不存在")
        source_state = (
            "missing"
            if source is None
            else "changed"
            if record and record.target.source_fingerprint != source_fingerprint(source)
            else "unchanged"
        )
        effective = bool(
            record
            and record.status == "active"
            and record.experience
            and record.experience.status == "confirmed"
            and record.experience.source_feedback_revision == record.feedback_revision
            and source_state == "unchanged"
        )
        return FeedbackView(
            state=record.status if record else "none",
            source_state=source_state,
            revision=record.revision if record else 0,
            record=record,
            last_mutation_id=record.last_mutation.id if record else None,
            experience_effective=effective,
        )
