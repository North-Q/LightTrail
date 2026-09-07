"""结构化输出契约（E5-2）：Intent / DecisionCard 等 pydantic 模型。

设计要点（架构 v2.0 §2.10，E5 落地）：
- 全部管线 LLM 输出走 pydantic 校验（model_validate_json），校验失败由
  parse_with_retry（infra/validation.py）回传模型自愈；模型同时可作 FastAPI
  请求/响应模型（E7 白拿 OpenAPI）；
- DecisionCard 的 evidence / confidence 为**必填**——结构性保证 M2（依据/来源/
  置信度）不落空；sources 由 TraceRecorder 的规则化置信度对齐（tool/field/confidence）；
- 字段用英文名做数据契约（LLM 输出稳定），渲染层（cli）再转中文展示。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Intent(BaseModel):
    """用户请求的规范化意图（意图理解步骤的强约束 JSON 输出）。

    Attributes:
        subject_type: 题材（星空 / 银河 / 日出 / 日落 / 朝霞 / 晚霞 / 火烧云 / 蓝调 /
            黄金时刻 / 夜景 / 城市风光 等）。
        location: 目标地点（用户给的模糊地点，如「崇明东滩」；未知为空串）。
        time_hint: 时间提示原文（如「这周末」「明天傍晚」「今晚」；未知为空串）。
        mode: 管线模式（inspiration 灵感 / planning 规划 / live 临场 / review 复盘）。
    """

    subject_type: str
    location: str = ""
    time_hint: str = ""
    mode: str = ""


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


class DecisionCard(BaseModel):
    """决策卡片：管线的最终结构化输出（M2 结构性必填 evidence / confidence）。

    Attributes:
        conclusion: 结论（一句话「该不该出门 / 几点去 / 去哪 / 带什么」）。
        evidence: 依据列表（必填；即使为空也要输出显式 []，防 M2 落空）。
        confidence: 整体置信度（high / medium / low；必填）。
        time_window: 建议时间窗口文案（如「05:10-05:40，黄金 05:20-06:10」）。
        locations: 推荐机位列表。
        params: 参数建议列表。
        alternatives: 备选方案文案列表。
        degraded: 降级原因（配额 / 管线降级标注，空串表示未降级）。
    """

    conclusion: str
    evidence: list[Source]
    confidence: str
    time_window: str = ""
    locations: list[LocationSuggestion] = Field(default_factory=list)
    params: list[ParamSuggestion] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    degraded: str = ""
