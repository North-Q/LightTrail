"""LightTrail 评估体系（E8）：L2 黄金用例集回归（零成本回放）+ L3 质量评估。

- `python -m evals.runner --level L2`：固定 Intent + Fake 数据源 + cassette 回放，
  分钟级零 LLM 成本，产出 evals/results/ 报告；
- `python -m evals.runner --level L2 --record`：真实 Key 录制（最小样本，配额克制）；
- `python -m evals.runner --level L3 [--llm-judge --limit N]`：rubric 打分 + 工具交叉校验。
"""
