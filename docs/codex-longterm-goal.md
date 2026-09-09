# LightTrail（光迹）· Codex 长期开发目标 v4（E8 起点）

> v4 更新（2026-09-09）：E1–E7 已全部交付并 push（37 commits，origin/main HEAD 8b58fd5，
> pytest 212 全绿、15 工具、smoke 21 项、前端 SPA 就绪）。本文档从「做到 E7」换代为目标
> **E8 阶段完成**；E1–E7 的逐任务细节见 `docs/devlog.md`，不再在此罗列。

## 你的身份与总目标

你是 LightTrail（光迹）项目的全职开发 Agent。项目位于 `D:\Project\LightTrail`，是一个面向摄影场景的 AI 拍摄决策引擎（LLM + 工具调用 + 个性化记忆），计划开源（MIT）。

目标：在无人值守的情况下，按开发路线图 v2.4 顺序、一个任务单元（E8-1、E8-2…）一个任务单元地推进开发，每完成一个单元提交一次 git，一直做到**阶段六（E8）完成**或遇到不可逾越的阻塞为止（E8 收口后由主理人评估是否续开源发布阶段）。

## ⚠️ 重大架构原则：平台中立性（最高优先级，每次动手前先过一遍）

> **核心认知**：模型 API 可以来自任何供应商（ECNU / DeepSeek / OpenAI / 本地模型…），架构与代码绝不假定 API 来自固定提供商；SDK 版本差异同样不能渗透进业务层。**违反本原则即视为失败。**

> **权威记录在 ADR**，不在本文档展开：
> - [`docs/adr/ADR-002-platform-neutrality.md`](adr/ADR-002-platform-neutrality.md)——并发策略可配置（`LLM_SERIAL_LLM`）、能力矩阵可注入、配置 `LLM_` 前缀去 ECNU 化、移除「深推理与工具互斥」假设
> - [`docs/adr/ADR-003-reason-extension-params.md`](adr/ADR-003-reason-extension-params.md)——thinking/reasoning_effort 扩展参数收敛在 `ChatClient` 适配层（SDK 探测 + extra_body 双路径），业务层只声明开关（`LLM_REASON_THINKING`）
>
> 改 ADR 时本节无需同步（单一事实源在 ADR）；本节只保留自查清单。

### 每次提交前自查（checklist）
- [ ] 新增的 LLM 调用是否经过 `ChatClient` 并发边界 / `ModelRouter` / 适配层，而不是绕过
- [ ] 代码里是否出现 `"ecnu"`（除 config 默认值、注释举例、文档历史叙述）——应只出现在 `DEFAULT_*`、`.env.example` 注释、ADR/架构文档
- [ ] 是否新增了 `ECNU_*` 环境变量（不该新增）或业务层硬编码了 thinking 等扩展字段
- [ ] 是否写了「ECNU 要求…所以…」这类把平台特性当理由的表述（应写「由配置控制，默认适配 ECNU」）
- [ ] 涉及架构决策时，是否先查 `docs/adr/`（ADR-002 / ADR-003 是权威记录，新决策追加 ADR）

### 发现遗留问题的处理
若发现平台/SDK 特性仍渗透进架构/代码/文档（硬编码串行锁、模型品牌判断、业务层扩展参数、`ECNU` 语义渗入业务层、文档把平台特性当需求），**立即修复**：代码收敛到适配层/能力矩阵/配置项并补测试；文档改平台中立表述；commit message 与 `docs/devlog.md` 注明「平台中立性修复」。不要以「不影响当前功能」为由跳过。

## 项目基线（先读这些，不要凭猜测动手）

1. 按顺序阅读关键文档：
   - `AGENTS.md`（协作约定 + 代码规范，必须遵守）
   - `docs/DEVELOPMENT-ROADMAP.md`（**v2.4**，任务 E1-1…E9-3，你按它干活）
   - `docs/architecture.md`（**v2.0.3**，§2.11 三层评估体系是 E8 的架构依据）
   - `docs/adr/ADR-002-platform-neutrality.md` 与 `docs/adr/ADR-003-reason-extension-params.md`
   - `docs/PRD-v0.3.md`（产品需求）
   - `TODO.md`（待办 + 工程遗留 + 排期）
   - `docs/devlog.md`（进度日志：E1–E7 已记录 37+ commits，先读收尾总结与遗留问题）
   - `evals/golden/`（已有 E6-photo-cases.json 8 条——E8-1 的增量基础）
2. 当前代码状态：src+tests ~1.1w 行；**15 个工具已注册**（含 analyze_photo / reverse_engineer_photo / search_memory）；`tests/` **212 个用例全绿**；ruff 0 告警；smoke 21 项通过；`frontend/` SPA 三核心页 build 通过。
3. Python 环境：项目专用 venv `.venv\Scripts\python.exe`。**新增依赖一律装进 .venv**。
4. Node 环境：`frontend/` 独立工程（npm run dev / build），本轮 E8 以 Python 评估框架为主，一般不改前端。
5. git：分支 main，HEAD `8b58fd5`，**已与 origin/main 同步**（37 commits 已 push）。每完成一个任务单元即 commit + push（push 不阻塞）。

## 第一步（先做）

读 roadmap v2.4 阶段六 E8：**E8 的 LLM-as-judge 涉及真实 LLM 调用**——评估以「最小样本 + 配额克制」为原则（架构 v2.0 §2.11 预算门禁 ~500 credits/次）；L2 黄金用例集 cassette 回放零 LLM 成本。先搭 runner 骨架与 L2 回放，LLM-as-judge（E8-2）用最小黄金子集验证。

## 主线任务（按 roadmap v2.4 顺序执行，做完一个提交一个）

### 阶段六：评估体系（E8）
- E8-1 黄金用例集与管线回归（L2）：`evals/runner.py run_l2()`——固定 Intent + Fake 数据源（注入，不改管线代码），LLM 综合步骤 cassette 录制/回放（放 `evals/cassettes/`，首次真实录制后零 LLM 成本回放）；断言 DecisionCard schema 合法性与关键字段（结论方向/机位数/置信度区间/evidence 非空——M2 结构性断言）；集中补齐至 ~30 条（三题材 × 四主线 + 边界：极昼/缺天气 Key/档案为空；已有 E6-photo-cases.json 8 条并入）
- E8-2 LLM-as-judge 质量评估（L3）：`evals/judge.py`——rubric 打分（决策合理性/依据完整性/个性化程度/不确定性坦白，1–5 分）+「工具即裁判」交叉校验（`cross_check`：模型参数建议 vs `star_shutter_rule` 等纯计算工具反向验证）；QuotaLedger 预算门禁；结果存档 `evals/results/` 形成质量曲线
- E8-3 阶段收口：README/roadmap/devlog/TODO 同步 + E8 总结 + 平台中立性审计

> 注：E8-1 的「每阶段增量交付」策略已落地（E6-5 已含 ≥8 条照片用例），本轮集中补齐至 ~30 条即可；roadmap 若未列 E8-3 收口任务，按 E8-1/E8-2 验收标准完成后自行收口（文档同步 + devlog 总结）。

每完成一个任务单元：
1. 跑 `pytest tests/` 全绿；该任务新增用例必须通过；ruff 0 告警
2. 中文 commit（如「feat(E8-1): 黄金用例集与 L2 管线回归…」）+ push（push 不阻塞）
3. `docs/devlog.md` 追加一行（任务编号 / 时间 / commit hash / 测试数量）

## 硬约束（违反即视为失败）

- **代码规范**：中文 docstring + Google 风格；`from __future__ import annotations` + 完整类型注解；ruff line-length=100；日志用 % 占位符；常量大写下划线、私有方法前缀下划线；工具返回值中文、description 给示例
- **测试**：改动前先看 roadmap 验收标准；新增功能配测试（正确性 + 边界）；改动后全量 pytest 全绿
- **⚠️ 平台中立性（见上）**：并发可配置、矩阵可注入、配置 LLM_ 前缀、扩展参数收敛适配层——硬规则，违反即失败
- **LLM/API**：默认不要用真实 API Key 联调（一律 Fake/mock）；E8-2 LLM-as-judge 真实调用只跑**最小黄金子集**（≤10 条）且走 QuotaLedger 预算门禁，不烧配额；cassette 回放路径零 LLM 成本
- **CLI/Web 路径不变**：评估框架是**增量旁路**，不得破坏现有 CLI `--pipeline` 与 Web API 路径；Fake 数据源经依赖注入，不改管线代码
- **不动文件**：LICENSE、docs/design/（只读参考）；AGENTS.md 如需修改先征得小北同意；`frontend/` 本轮一般不改（E8 是后端评估）
- **禁忌**：LightTrail 是独立开源项目，与学位论文/学术研究**无关**，任何文档/代码/提交信息中严禁此类关联表述
- **架构决策**：不确定先查 docs/architecture.md（v2.0.3，§2.11 三层评估是 E8 依据）与 docs/adr/；仍不确定记入阻塞清单，不擅自大改架构

## 进度与阻塞记录（必须维护）

在 `docs/devlog.md` 维护：每个任务单元完成后追加（任务编号 / 完成时间 / commit hash / 测试数量）；阻塞如实记录（问题 + 排查 + 建议）后跳下一个不依赖它的任务；全部卡住就停下。

## 收尾

E8 收口或决定停止时：
1. 最后一次 `pytest tests/` 全绿 + `ruff check src tests` 无新告警
2. `git add -A && git commit && git push`（push 不阻塞）
3. `docs/devlog.md` 写总结：已完成 / 剩余 / 遗留 / 建议（含平台中立性状态审计 + 评估质量曲线摘要）
