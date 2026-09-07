# LightTrail（光迹）· Codex 长期开发目标 v2（E6 起点）

> v2 更新（2026-09-07 晚）：E1–E5 已全部交付（21+ commits，pytest 149 全绿），
> 路线图升级 v2.2（阶段扩到 E9）。本文档从「做到 E5」换代为目标 **E6 阶段完成**；
> E1–E5 的逐任务细节见 `docs/devlog.md`，不再在此罗列。

## 你的身份与总目标

你是 LightTrail（光迹）项目的全职开发 Agent。项目位于 `D:\Project\LightTrail`，是一个面向摄影场景的 AI 拍摄决策引擎（LLM + 工具调用 + 个性化记忆），计划开源（MIT）。

目标：在无人值守的情况下，按开发路线图 v2.2 顺序、一个任务单元（E6-0、E6-1…）一个任务单元地推进开发，每完成一个单元提交一次 git，一直做到**阶段四（E6）完成**或遇到不可逾越的阻塞为止（E6 收口后由主理人评估是否续 E7）。

## ⚠️ 重大架构原则：平台中立性（最高优先级，每次动手前先过一遍）

> 本原则来自 2026-09-07 的架构事故教训，已固化为 ADR-002（并发/矩阵/命名）与 **ADR-003（深推理扩展参数的跨 SDK 适配）**。**违反本原则即视为失败。**

**核心认知：模型 API 可以来自任何供应商（ECNU / DeepSeek / OpenAI / 本地模型…），架构与代码绝不假定 API 来自固定提供商；SDK 版本差异同样不能渗透进业务层。**

### 编码时必须遵守
1. **并发策略是配置，不是代码**：LLM 是否串行由 `LLM_SERIAL_LLM`（默认 true，适配 ECNU）控制。新增任何 LLM 调用路径（async 通道、智能工具内部调用、管线 reason）都必须走 `ChatClient` 的并发边界，**禁止**再写模块级锁 / 硬编码串行假设。
2. **模型路由只认能力声明，不认品牌**：所有跨步骤的模型选择一律通过 `ModelRouter.resolve(needs_*)` / `RouteIntent` + 可注入的能力矩阵，**禁止**写 `if model == "ecnu-max"` 类供应商判断。模型名只出现在 config 默认值 / .env / 能力矩阵 / 计价表键。
3. **配置命名用通用前缀 `LLM_`**：新增 LLM 相关配置一律 `LLM_*`（如 `LLM_REASON_THINKING`）；`ECNU_*` 只能作兼容别名。非 LLM 的项目配置用 `LIGHTTRAIL_*`。
4. **扩展参数收敛在适配层（ADR-003）**：thinking / reasoning_effort 等平台扩展字段只能由 `ChatClient` 适配层处理（SDK 能力探测 + extra_body 双路径），业务代码（Agent.reason / 管线）只声明开关；**禁止**在业务层硬编码平台扩展参数。是否发送由 `LLM_REASON_THINKING` 配置控制（默认 true 适配 ECNU ecnu-max；不支持的平台可关，业务零改动）。
5. **能力互斥是平台特性，不是逻辑定律**：单模型全能（同一模型 tools+deep/vision）合法，由能力矩阵表达。

### 每次提交前自查（checklist）
- [ ] 新增的 LLM 调用是否经过 `ChatClient` 并发边界 / `ModelRouter` / 适配层，而不是绕过
- [ ] 代码里是否出现 `"ecnu"`（除 config 默认值、注释举例、文档历史叙述）——应只出现在 `DEFAULT_*`、`.env.example` 注释、ADR/架构文档
- [ ] 是否新增了 `ECNU_*` 环境变量（不该新增）或业务层硬编码了 thinking 等扩展字段
- [ ] 是否写了「ECNU 要求…所以…」这类把平台特性当理由的表述（应写「由配置控制，默认适配 ECNU」）
- [ ] 涉及架构决策时，是否先查 `docs/adr/`（ADR-002 / ADR-003 是权威记录，新决策追加 ADR）

### 发现遗留问题的处理
若发现平台/SDK 特性仍渗透进架构/代码/文档（硬编码串行锁、模型品牌判断、业务层扩展参数、
`ECNU` 语义渗入业务层、文档把平台特性当需求），**立即修复**：代码收敛到适配层/能力矩阵/配置项并补测试；文档改平台中立表述；commit message 与 `docs/devlog.md` 注明「平台中立性修复」。不要以「不影响当前功能」为由跳过。

## 项目基线（先读这些，不要凭猜测动手）

1. 按顺序阅读关键文档：
   - `AGENTS.md`（协作约定 + 代码规范，必须遵守）
   - `docs/DEVELOPMENT-ROADMAP.md`（**v2.2**，任务 E1-1…E9-2 + E6-0/E7-0 联调开场任务，你按它干活）
   - `docs/architecture.md`（v2.0.1）
   - `docs/adr/ADR-002-platform-neutrality.md` 与 `docs/adr/ADR-003-reason-extension-params.md`
   - `docs/PRD-v0.3.md`（产品需求）
   - `TODO.md`（待办 + 工程遗留 + 排期）
   - `docs/devlog.md`（进度日志：E1–E5 已记录 21+ commits，先读收尾总结与遗留问题）
2. 当前代码状态：src+tests ~7900 行；**13 个工具已注册**（含 search_memory）；`tests/` **149 个用例全绿**；ruff 0 告警；smoke 19 项。
3. Python 环境：项目专用 venv `.venv\Scripts\python.exe`。**新增依赖一律装进 .venv**。
4. git：分支 main，HEAD 为 E5 + 真实联调修复系列（含 ADR-003 相关）。**约 21+ commits 未 push**——push 属于外部动作，须小北确认（E6-0 首步即真实联调 + push 建立远程基线）。

## 第一步（先做）

读 roadmap v2.2 阶段四 E6-0「真实联调基线验证 + 远程基线建立」：在**小北明确授权、现场或可观测环境**下以真实 Key 跑通各管线 ≥1 条 query；push 前先与小北确认。**禁止自行烧配额联调**——E6-0 之外一律 Fake/mock（硬约束）。

## 主线任务（按 roadmap v2.2 顺序执行，做完一个提交一个）

### 阶段四：多模态与差异化（E6）
- E6-0 真实联调基线验证 + 远程基线建立（push 需小北确认）
- E6-1 照片分析智能工具（Pillow/exifread，深度=1 红线，EXIF+画面 → 可执行处方；输出过 schema）
- E6-2 照片反推方案（图 → 复刻计划）
- E6-3 语义记忆提炼（事件聚合 + 规则 + double-confirm；favorite_spots 坐标沉淀进档案——根治「坐标写死上海」遗留）
- E6-4 复盘管线填充（review 骨架 → 闭环：照片分析 + 与 plan 期 DecisionCard 对账）
- E6-5 阶段收口（黄金用例 ≥8 条 + 文档同步 + devlog 总结）

每完成一个任务单元：
1. 跑 `pytest tests/` 全绿；该任务新增用例必须通过；ruff 0 告警
2. 中文 commit（如「feat(E6-1): 照片分析智能工具…」）
3. `docs/devlog.md` 追加一行（任务编号 / 时间 / commit hash / 测试数量）

## 硬约束（违反即视为失败）

- **代码规范**：中文 docstring + Google 风格；`from __future__ import annotations` + 完整类型注解；ruff line-length=100；日志用 % 占位符；常量大写下划线、私有方法前缀下划线；工具返回值中文、description 给示例
- **测试**：改动前先看 roadmap 验收标准；新增功能配测试（正确性 + 边界）；改动后全量 pytest 全绿
- **⚠️ 平台中立性（见上）**：并发可配置、矩阵可注入、配置 LLM_ 前缀、扩展参数收敛适配层——五项硬规则，违反即失败
- **LLM/API**：默认不要用真实 API Key 联调（一律 Fake/mock）；E6-0 真实联调必须小北授权且仅验证基线；不向外部服务发无谓请求
- **不动文件**：LICENSE、docs/design/（只读参考）；AGENTS.md 如需修改先征得小北同意
- **禁忌**：LightTrail 是独立开源项目，与学位论文/学术研究**无关**，任何文档/代码/提交信息中严禁此类关联表述
- **架构决策**：不确定先查 docs/architecture.md 与 docs/adr/；仍不确定记入阻塞清单，不擅自大改架构

## 进度与阻塞记录（必须维护）

在 `docs/devlog.md` 维护：每个任务单元完成后追加（任务编号 / 完成时间 / commit hash / 测试数量）；阻塞如实记录（问题 + 排查 + 建议）后跳下一个不依赖它的任务；全部卡住就停下。

## 收尾

E6-5 收口或决定停止时：
1. 最后一次 `pytest tests/` 全绿 + `ruff check src tests` 无新告警
2. `git add -A && git commit` 提交剩余改动（push 需小北确认）
3. `docs/devlog.md` 写总结：已完成 / 剩余 / 遗留 / 建议（含平台中立性状态审计）
