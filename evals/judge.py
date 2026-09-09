"""L3 质量评估（E8-2）：rubric 打分 + 工具交叉校验（「工具即裁判」）。

- LocalRubric：确定性 4 维打分（决策合理性/依据完整性/个性化程度/不确定性坦白），
  默认零成本；LLMJudge 可选用 ecnu-max 打分（受限条数，配额克制）；
- ToolCrossCheck：用纯计算工具反向校验卡片参数建议（如快门违反 500 法则即抓出），
  这是「工具即裁判」的确定性兜底——不依赖 LLM 稳定性。
"""

from __future__ import annotations

import re
from typing import Any

from lighttrail.orchestrator.schemas import DecisionCard

RUBRIC_DIMS = ("决策合理性", "依据完整性", "个性化程度", "不确定性坦白")

# 个性化信号：卡片文本里提到档案/记忆/器材/常去机位
_PERSONAL_MARKS = ("档案", "记忆", "器材", "常去", "你的", "相机", "镜头", "偏好")
# 决策合理性信号
_REASON_MARKS = ("时间", "机位", "地点", "参数", "快门", "ISO", "光圈", "窗口", "到达")


class JudgeError(Exception):
    """L3 评估参数非法。"""


class LocalRubric:
    """确定性 rubric 打分（零 LLM 成本；有据可依的启发式，非模型主观评分）。"""

    def score(self, card: DecisionCard) -> dict[str, dict[str, Any]]:
        """对一张卡片打 4 维分。

        Args:
            card: 决策卡片。

        Returns:
            {维度: {"score": 1-5, "reason": 说明}}。
        """
        return {
            "决策合理性": self._reasonableness(card),
            "依据完整性": self._evidence(card),
            "个性化程度": self._personalization(card),
            "不确定性坦白": self._uncertainty(card),
        }

    def _reasonableness(self, card: DecisionCard) -> dict[str, Any]:
        text = card.conclusion
        score = 2
        if len(text) >= 20:
            score += 1
        hits = sum(1 for mark in _REASON_MARKS if mark in text)
        score += min(2, hits)
        return {"score": min(5, score), "reason": "结论含" if hits else "", "evidence": []}

    def _evidence(self, card: DecisionCard) -> dict[str, Any]:
        count = len(card.evidence)
        if count == 0:
            return {"score": 1, "reason": "evidence 为空（M2 缺失）"}
        if count == 1:
            return {"score": 3, "reason": "仅 1 条依据，建议补充来源"}
        if count == 2:
            return {"score": 4, "reason": "2 条依据"}
        return {"score": 5, "reason": f"{count} 条依据较完整"}

    def _personalization(self, card: DecisionCard) -> dict[str, Any]:
        haystack = card.conclusion + " ".join(evidence.note or "" for evidence in card.evidence)
        names = " ".join(param.name for param in card.params)
        if any(mark in haystack for mark in _PERSONAL_MARKS) or any(
            mark in names for mark in ("档案", "记忆", "器材")
        ):
            return {"score": 5, "reason": "引用个性化记忆/器材"}
        return {"score": 2, "reason": "未见个性化记忆引用"}

    def _uncertainty(self, card: DecisionCard) -> dict[str, Any]:
        score = 5 if card.confidence in {"low", "medium"} else 3
        if card.degraded:
            score = min(5, score + 1)
        had_low = any(evidence.confidence in {"low", "medium"} for evidence in card.evidence)
        if had_low and card.confidence == "high":
            score = min(5, score + 1)
        return {"score": score, "reason": "置信度透明度" if card.confidence != "high" else "置信度较高"}


class ToolCrossCheck:
    """「工具即裁判」：纯计算工具反向校验卡片参数（确定性，离线）。"""

    def cross_check(
        self,
        card: DecisionCard,
        *,
        focal_mm: float | None = None,
        crop_factor: float = 1.0,
    ) -> list[dict[str, Any]]:
        """校验卡片参数建议是否符合物理/经验法则。

        Args:
            card: 决策卡片。
            focal_mm: 等效焦距（毫米）；提供时启用 500 法则校验。
            crop_factor: 等效焦距换算系数（APS-C=1.5，全画幅=1.0）。

        Returns:
            违规清单（空列表 = 无违规）；每项 {rule, param, value, bound, detail}。
        """
        findings: list[dict[str, Any]] = []
        for param in card.params:
            seconds = self._shutter_seconds(param.name, param.value)
            if seconds is None or focal_mm is None or focal_mm <= 0:
                continue
            bound = 500.0 / (focal_mm * crop_factor)
            if seconds > bound + 1e-6:
                findings.append(
                    {
                        "rule": "500法则",
                        "param": f"{param.name}={param.value}",
                        "value_s": seconds,
                        "bound_s": round(bound, 1),
                        "detail": f"快门 {seconds:.1f}s 超过 500 法则上限（{focal_mm}mm 等效 → {bound:.1f}s）",
                    }
                )
        return findings

    @staticmethod
    def parse_shutter_seconds(value: str) -> float | None:
        """解析快门文案为秒数（支持 '30s' / '1/125' / '1/2s' / '0.5s'）。"""
        text = str(value).strip()
        match = re.match(r"^(\d+(?:\.\d+)?)\s*s$", text)
        if match:
            return float(match.group(1))
        match = re.match(r"^1/(\d+(?:\.\d+)?)\s*s?$", text)
        if match:
            return 1.0 / float(match.group(1))
        match = re.match(r"^(\d+(?:\.\d+)?)$", text)
        if match:
            return float(match.group(1))
        return None

    def _shutter_seconds(self, name: str, value: str) -> float | None:
        """仅对快门类参数做解析（名称含「快门/曝光」）。"""
        if "快门" not in name and "曝光" not in name:
            return None
        return self.parse_shutter_seconds(value)


class LLMJudge:
    """LLM 判官（可选）：按 rubric 由深推理模型打分（受限条数，配额克制）。

    Args:
        judge_fn: 打分函数（prompt 文本 → 文本）；可与 ModelRouter 解耦。
        model_label: 模型名标注（平台中立，仅作报告展示）。
    """

    def __init__(self, judge_fn: Any, *, model_label: str = "deep-judge") -> None:
        self._judge_fn = judge_fn
        self._model_label = model_label

    def score(self, card: DecisionCard) -> dict[str, dict[str, Any]]:
        """调用 LLM 打分（返回 1-5 级 JSON 或降级为本地 rubric）。"""
        prompt = _judge_prompt(card)
        try:
            raw = self._judge_fn(prompt)
            return _parse_judge_output(raw)
        except Exception:  # noqa: BLE001 - judge 不稳定时降级本地打分
            return LocalRubric().score(card)


def _judge_prompt(card: DecisionCard) -> str:
    """构造 rubric 打分 prompt（要求 JSON 输出 4 维 1-5 分 + 一句理由）。"""
    return (
        "你是 LightTrail 评估判官。按四条 rubic（决策合理性 / 依据完整性 / 个性化程度 / "
        "不确定性坦白）对以下决策卡分别打 1-5 分，只输出 JSON："
        '{"决策合理性": {"score": 1, "reason": "..."}, "依据完整性": {...}, '
        '"个性化程度": {...}, "不确定性坦白": {...}}\n\n决策卡：\n'
        f"{card.model_dump_json(indent=2)}"
    )


def _parse_judge_output(raw: str) -> dict[str, dict[str, Any]]:
    """解析 judge 返回 JSON（首块尝试；失败抛错由上层降级）。"""
    import json

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise JudgeError("judge 输出无 JSON")
    data = json.loads(match.group(0))
    parsed: dict[str, dict[str, Any]] = {}
    for dim in RUBRIC_DIMS:
        entry = data.get(dim) or {}
        score = int(entry.get("score", 3))
        parsed[dim] = {"score": max(1, min(5, score)), "reason": str(entry.get("reason", ""))}
    return parsed


__all__ = ["RUBRIC_DIMS", "JudgeError", "LLMJudge", "LocalRubric", "ToolCrossCheck"]
