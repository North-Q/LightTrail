"""结构化输出契约（B1-2 自 orchestrator/schemas.py 下沉，E5-2 起沿用）。

设计要点（架构 v4 §2.3 / D4）：
- 跨层共享的 pydantic 模型一律住契约层：工具（photo_analysis 的复盘/反推契约）、
  编排（Intent/DecisionCard）、服务层（FastAPI 请求/响应）都依赖它——消灭
  领域工具不再反向依赖编排层模型模块（R2）；
- 全部管线 LLM 输出走 pydantic 校验（model_validate_json），校验失败由
  parse_with_retry（infra/validation.py）回传模型自愈；
- DecisionCard 的 evidence / confidence 为**必填**——结构性保证 M2（依据/来源/
  置信度）不落空；sources 由 TraceRecorder 的规则化置信度对齐（tool/field/confidence）；
- 字段用英文名做数据契约（LLM 输出稳定），渲染层（cli）再转中文展示。

B4 增量（前端去伪造数据的契约支撑）：
- `verdict`：模型建议受实际证据资格约束，关键失败固定 wait；前端不从文案猜测。
- U1 附资格、缺失原因与时间元数据；ConfidenceDetail 仅为历史兼容，不在页面展示。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class Intent(BaseModel):
    """用户请求的规范化意图（意图理解步骤的强约束 JSON 输出）。

    Attributes:
        subject_type: 题材（星空 / 银河 / 日出 / 日落 / 朝霞 / 晚霞 / 火烧云 / 蓝调 /
            黄金时刻 / 夜景 / 城市风光 等）。
        location: 目标地点（用户给的模糊地点，如「崇明东滩」；未知为空串）。
        time_hint: 时间提示原文（如「这周末」「明天傍晚」「今晚」；未知为空串）。
        mode: 管线模式（inspiration 灵感 / planning 规划 / live 临场 / review 复盘）。
        solar_event: 明确的 sunrise / sunset；晨暮冲突或未明确蓝调为 ambiguous。
        solar_window: blue 或 blue_through_event；旧会话缺失时为空。
    """

    subject_type: str
    location: str = ""
    time_hint: str = ""
    mode: str = ""
    solar_event: str = ""  # sunrise / sunset / ambiguous；旧记录缺字段时保持未知。
    solar_window: str = ""  # blue / blue_through_event；不以题材默认替代晨暮事实。


class RoutingDecision(BaseModel):
    """一次意图路由的可审计判定结果。

    规则命中无需构造本模型；规则未命中时由轻量 LLM 输出，并在编排层依据
    ``confidence`` 与部署阈值决定是否接受。受控 Planner 在 B7-3 接入时将
    扩展该契约的分发目标，不改变已有意图字段。
    """

    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""

    @model_validator(mode="before")
    @classmethod
    def _compat_old_intent(cls, value: Any) -> Any:
        """兼容 B7-2 前录制的纯 Intent cassette，避免离线评估历史失效。"""
        if isinstance(value, dict) and "intent" not in value and "subject_type" in value:
            return {"intent": value, "confidence": 1.0, "reason": "legacy_intent_cassette"}
        return value


class Source(BaseModel):
    """一条决策依据（M2）：哪个工具 / 哪个字段 / 置信度 / 说明。"""

    tool: str = ""
    field: str = ""
    confidence: str = "low"
    note: str = ""


class ParamSuggestion(BaseModel):
    """一条拍摄参数建议（含理由）。"""

    name: str = ""
    value: str = ""
    reason: str = ""


class LocationSuggestion(BaseModel):
    """一个推荐机位（含理由）。"""

    name: str = ""
    reason: str = ""


class ConfidenceDetail(BaseModel):
    """历史置信度明细兼容契约；分数与区间未经校准，不供页面展示。

    由 infra/confidence.confidence_detail() 依据 evidence 的来源级别加权推导；
    契约层仅保留字段以兼容旧归档；U1 已核验的决策卡不再生成本对象。

    Attributes:
        level: 卡片整体等级（high / medium / low），沿用模型给出的等级标注。
        score: 置信度主值（0–100，依据级别加权平均）。
        low: 区间下限（0–100）。
        high: 区间上限（0–100）。
        basis: 推导依据说明（人话，供「依据」小字展示）。
        high_count: 依据中 high 级来源条数。
        medium_count: 依据中 medium 级来源条数。
        low_count: 依据中 low 级来源条数。
    """

    level: str = "low"
    score: int = 0
    low: int = 0
    high: int = 0
    basis: str = ""
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0


class PhotoAnalysisReport(BaseModel):
    """照片分析输出契约（E6-1，PRD D4-01~04）。

    模型输出经本 schema 校验（复用 E5-2 自愈机制）；assessment / prescription 必填——
    结构性保证「点评」与「可执行处方」不落空（D4-04 区别于泛泛点评）。
    """

    scene: str = ""
    subject: str = ""
    composition: str = ""
    exposure: str = ""
    color: str = ""
    assessment: str
    prescription: str
    suggestions: list[ParamSuggestion] = Field(default_factory=list)
    confidence: str = "low"


class PhotoReverseReport(BaseModel):
    """照片反推输出契约（E6-2，PRD D1.2）。

    从参考图识别「场景 / 光向 / 时段 / 机位特征 / 后期风格」并给复刻计划；
    replication_plan 必填——结构性保证「在哪 / 什么时候 / 怎么拍」三要素不落空。
    """

    scene: str = ""
    light_direction: str = ""
    estimated_time: str = ""
    site_features: str = ""
    post_style: str = ""
    replication_plan: str
    suggestions: list[ParamSuggestion] = Field(default_factory=list)
    confidence: str = "low"


class DecisionCard(BaseModel):
    """决策卡片：管线的最终结构化输出（M2 结构性必填 evidence / confidence）。

    Attributes:
        conclusion: 结论（一句话「该不该出门 / 几点去 / 去哪 / 带什么」）。
        evidence: 依据列表（必填；即使为空也要输出显式 []，防 M2 落空）。
        confidence: 整体置信度（high / medium / low；必填）。
        verdict: 三态结论（go 值得去 / wait 再观察 / risk 不建议专程；未知为空串）。
            由模型按提示词给出，前端直接读字段，不做中文正则猜测（B4-3）。
        confidence_detail: 历史兼容明细，未经概率校准；U1 核验卡为 None。
        time_window: 建议时间窗口文案（如「05:10-05:40，黄金 05:20-06:10」）。
        window_start: 建议窗口开始时间（ISO 8601，含时区；未知为空）。
        window_end: 建议窗口结束时间（ISO 8601，含时区；未知为空）。
        locations: 推荐机位列表。
        params: 参数建议列表。
        alternatives: 备选方案文案列表。
        degraded: 降级原因（配额 / 管线降级标注，空串表示未降级）。
        target_date: 本次拍摄目标日期（请求时区中的 YYYY-MM-DD）。
        subject_type: 本次请求的拍摄题材，不由收藏机位元数据覆盖。
        data_status: 证据资格（ready / needs_data / unverified），由采集边界写入。
        missing_data: 关键数据不足的实际原因。
        evaluated_at: 本次评估的请求基准时间（含时区）。
        updated_at: 合格预报中最早的获取时间，不代表供应商发布时间。
        valid_until: 最早的预报到期、新鲜度上限或目标事件时刻。
        tz_offset: 请求目标日期使用的时区。
    """

    conclusion: str
    evidence: list[Source]
    confidence: str
    verdict: str = ""
    confidence_detail: ConfidenceDetail | None = None
    time_window: str = ""
    window_start: str = ""
    window_end: str = ""
    locations: list[LocationSuggestion] = Field(default_factory=list)
    params: list[ParamSuggestion] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    degraded: str = ""
    target_date: str = ""
    subject_type: str = ""

    # 资格与时间由采集边界写入，禁止采用模型自报值。
    data_status: str = "unverified"
    missing_data: list[str] = Field(default_factory=list)
    evaluated_at: str = ""
    updated_at: str = ""
    valid_until: str = ""
    tz_offset: str = ""


__all__ = [
    "ConfidenceDetail",
    "DecisionCard",
    "Intent",
    "LocationSuggestion",
    "ParamSuggestion",
    "PhotoAnalysisReport",
    "PhotoReverseReport",
    "RoutingDecision",
    "Source",
]
