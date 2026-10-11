"""U5本地结果反馈契约：结构资格、版本与撤回状态，不解释自由文本。"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Reason = Annotated[str, Field(min_length=1, max_length=500)]
ExperienceKind = Literal["planning_preference", "observation_note"]


class FeedbackModel(BaseModel):
    """拒绝多余字段的独立契约基类。"""

    model_config = ConfigDict(extra="forbid")


class ObservationScope(FeedbackModel):
    """本人观察范围；不推断是否覆盖计划。"""

    place_matches_plan: Literal["yes", "no", "unknown"]
    window_covers_plan: Literal["yes", "no", "unknown"]
    description: str = Field(min_length=1, max_length=300)


class RawFeedback(FeedbackModel):
    """出门、观察、结果与原因独立的原始反馈。"""

    departure_status: Literal["went", "not_went", "unknown"]
    observation_status: Literal["observed", "not_observed", "unknown"]
    observation_result: Literal["seen", "not_seen", "uncertain"] | None = None
    observation_scope: ObservationScope | None = None
    departure_reason: Reason | None = None
    observation_reason: Reason | None = None
    result_reason: Reason | None = None
    note: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _check_observation(self) -> RawFeedback:
        """校验观察资格，不把未观察转换为未看到。

        Returns:
            已校验的反馈。
        Raises:
            ValueError: 观察状态与结果、范围或原因冲突。
        """
        if self.observation_status == "observed":
            if self.observation_result is None or self.observation_scope is None:
                raise ValueError("已观察必须填写观察结果及范围")
        elif any(
            value is not None
            for value in (self.observation_result, self.observation_scope, self.result_reason)
        ):
            raise ValueError("未观察或观察状态未知时，结果、范围及结果原因必须为null")
        return self


class FeedbackMutation(FeedbackModel):
    """比较并更新与最近操作去重的请求头字段。"""

    expected_revision: int = Field(ge=0, strict=True)
    mutation_id: UUID


class SaveFeedback(FeedbackMutation):
    """完整替换当前原始反馈，不支持照片引用。"""

    feedback: RawFeedback
    photo_refs: list[str] = Field(default_factory=list, max_length=0)


class SaveExperience(FeedbackMutation):
    """本人手写经验草稿，不能连带确认。"""

    kind: ExperienceKind
    content: Reason


class ConfirmExperience(FeedbackMutation):
    """对当前草稿及当前反馈版本的独立本人确认。"""

    experience_id: UUID
    source_feedback_revision: int = Field(ge=1, strict=True)
    confirmed_by_user: Literal[True]


class LocalExperience(FeedbackModel):
    """本地经验记录；撤回后正文清空，不写入Agent记忆。"""

    experience_id: UUID
    kind: ExperienceKind
    content: Reason | None
    status: Literal["draft", "confirmed", "needs_review", "withdrawn"]
    source_feedback_revision: int = Field(ge=1)
    confirmed_at: AwareDatetime | None = None
    withdrawn_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _check_state(self) -> LocalExperience:
        """拒绝残留的撤回正文或失效确认时间。

        Returns:
            结构有效的经验。
        Raises:
            ValueError: 状态与字段不一致。
        """
        if self.status == "withdrawn":
            if (
                self.content is not None
                or self.confirmed_at is not None
                or self.withdrawn_at is None
            ):
                raise ValueError("撤回经验必须清空正文和确认时间，并记录撤回时间")
        elif self.content is None or self.withdrawn_at is not None:
            raise ValueError("未撤回经验必须有正文且没有撤回时间")
        if (self.status == "confirmed") != (self.confirmed_at is not None):
            raise ValueError("只有已确认经验可以保留确认时间")
        return self


class FeedbackTarget(FeedbackModel):
    """稳定结果引用与固定v1规范指纹。"""

    session_id: str
    result_id: str
    source_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class LastMutation(FeedbackModel):
    """仅保存最近一次操作摘要，不保存历史正文。"""

    id: UUID
    request_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class FeedbackSnapshot(FeedbackModel):
    """可公开反馈快照，不包含内部请求摘要。"""

    schema_version: Literal[1] = 1
    feedback_id: UUID
    user_id: str
    target: FeedbackTarget
    status: Literal["active", "withdrawn"]
    revision: int = Field(ge=1)
    feedback_revision: int = Field(ge=1)
    feedback: RawFeedback | None
    experience: LocalExperience | None = None
    photo_refs: list[str] = Field(default_factory=list, max_length=0)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    withdrawn_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _check_record(self) -> FeedbackSnapshot:
        """拒绝墓碑残留正文和跨反馈版本的有效确认。

        Returns:
            当前结构一致的记录。
        Raises:
            ValueError: 字段状态不一致。
        """
        if self.status == "withdrawn":
            if (
                self.feedback is not None
                or self.experience is not None
                or self.withdrawn_at is None
            ):
                raise ValueError("撤回反馈必须清空反馈与经验，并记录撤回时间")
        elif self.feedback is None or self.withdrawn_at is not None:
            raise ValueError("有效反馈必须有正文且没有撤回时间")
        if self.feedback_revision > self.revision:
            raise ValueError("反馈版本不能超过文档版本")
        if self.experience is not None:
            if self.experience.source_feedback_revision > self.feedback_revision:
                raise ValueError("经验不能引用未来反馈版本")
            if self.experience.status == "confirmed":
                if self.experience.source_feedback_revision != self.feedback_revision:
                    raise ValueError("已确认经验必须绑定当前反馈版本")
                if self.experience.kind == "observation_note" and (
                    self.feedback is None or self.feedback.observation_status != "observed"
                ):
                    raise ValueError("观察经验必须有本人观察")
        return self


class FeedbackRecord(FeedbackSnapshot):
    """单个用户结果的原子反馈文档，内部另存最近操作摘要。"""

    last_mutation: LastMutation


class FeedbackView(FeedbackModel):
    """API只读状态；有效性派生，不在恢复中修改文件。"""

    saved: bool = True
    state: Literal["none", "active", "withdrawn"]
    source_state: Literal["unchanged", "changed", "missing"]
    revision: int
    record: FeedbackSnapshot | None
    last_mutation_id: UUID | None = None
    experience_effective: bool = False
    memory_written: Literal[False] = False
