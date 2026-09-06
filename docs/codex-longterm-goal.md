# LightTrail（光迹）· Codex 长期开发目标

## 你的身份与总目标

你是 LightTrail（光迹）项目的全职开发 Agent。项目位于 `D:\Project\LightTrail`，是一个面向摄影场景的 AI 拍摄决策引擎（LLM + 工具调用 + 个性化记忆），计划开源（MIT）。

目标：在无人值守的情况下，按开发路线图顺序、一个任务单元（E1-1、E1-2…）一个任务单元地推进开发，每完成一个单元提交一次 git，一直做到**阶段三（E5）完成**或遇到不可逾越的阻塞为止。

## ⚠️ 重大架构原则：平台中立性（最高优先级，每次动手前先过一遍）

> 本原则来自 2026-09-07 的一次架构事故教训，已固化为 ADR-002。**违反本原则即视为失败。**

**核心认知：模型 API 可以来自任何供应商（ECNU / DeepSeek / OpenAI / 本地模型…），架构与代码绝不假定 API 来自固定提供商。**

ECNU 平台有两个特性：①建议串行调用；②plus/max 双模型能力互补（plus 有工具+视觉，max 有深推理无工具）。**它们只是 ECNU 的默认配置，不是产品需求，更不是架构前提。** 具体检查项：

### 编码时必须遵守
1. **并发策略是配置，不是代码**：LLM 是否串行由 `LLM_SERIAL_LLM`（默认 true，适配 ECNU）控制。新增任何 LLM 调用路径（async 通道、智能工具内部调用、管线 reason）都必须走 `ChatClient` 的并发边界，**禁止**再写模块级锁 / 硬编码串行假设。关闭开关即放开并发，业务代码零改动。
2. **模型路由只认能力声明，不认品牌**：所有跨步骤的模型选择一律通过 `ModelRouter.resolve(needs_tools/needs_vision/needs_deep_reasoning)` + 可注入的能力矩阵，**禁止**在业务代码里写 `if model == "ecnu-max"` 这类供应商判断。模型名只出现在 `config.py` 默认值 / `.env` / 能力矩阵。
3. **配置命名用通用前缀 `LLM_`，ECNU_ 仅作兼容别名**：新增配置项一律 `LLM_*`；`ECNU_*` 只能作别名出现。不要新增 `ECNU_*` 专属配置。
4. **能力互斥是平台特性，不是逻辑定律**：不要假设「深推理模型必然不支持工具」——单模型全能（如同一模型 tools+deep）是合法的，由能力矩阵表达。`ModelRouter` 已支持 `default_model == reason_model` 场景。

### 每次提交前自查（checklist）
- [ ] 新增的 LLM 调用是否经过 `ChatClient` 并发边界 / `ModelRouter`，而不是绕过它们
- [ ] 代码里是否出现 `"ecnu"`（除 config 默认值、注释举例、文档）——应只出现在 `DEFAULT_*`、`.env.example` 注释、ADR/架构文档的历史叙述中
- [ ] 是否新增了 `ECNU_*` 环境变量（不该新增，用 `LLM_*`）
- [ ] 是否在代码/注释里写了「ECNU 要求…所以…」这类把平台特性当理由的表述（应写「串行由配置控制，默认适配 ECNU」）
- [ ] 涉及架构决策时，是否先查 `docs/adr/`（ADR-002 是本原则的权威记录，新决策要追加 ADR）

### 发现遗留问题的处理
若在长期开发中发现仍有平台特性渗透进架构/代码/文档（例如：新的硬编码串行锁、模型品牌判断、`ECNU` 语义渗入业务层、文档把 ECNU 特性当需求），**立即修复**：
1. 代码 → 收敛到并发边界 / 能力矩阵 / 配置项，补测试；
2. 文档 → 改为平台中立表述（可参考 ADR-002 的措辞风格）；
3. 在 commit message 与 `docs/devlog.md` 中注明「平台中立性修复」。
不要以「不影响当前功能」为由跳过——这正是本次事故的教训：串行锁当时也是「不影响功能」，换 API 时成了大坑。

## 项目基线（先读这些，不要凭猜测动手）

1. 按顺序阅读关键文档：
   - `AGENTS.md`（协作约定 + 代码规范，必须遵守）
   - `docs/DEVELOPMENT-ROADMAP.md`（v2.1，任务编号 E1-1…E8-2，你按它干活）
   - `docs/architecture.md`（v2.0 架构设计）
   - `docs/adr/ADR-002-platform-neutrality.md`（平台中立性原则，见上）
   - `docs/PRD-v0.3.md`（产品需求）
   - `TODO.md`（待办 + 工程遗留）
2. 当前代码状态：`src/lighttrail/` 下 config / llm / agent / tools 分层，**12 个工具已注册**（basic / exposure / astronomy / weather / site_match），`tests/` **43 个用例全绿**（含并发策略 + 能力矩阵用例）。
3. Python 环境：项目专用 venv 在 `.venv\Scripts\python.exe`（已装 pytest / astral / openai / ruff）。**新增依赖一律装进 .venv，禁止污染系统 Python 或全局环境**。
4. git：分支 main，最新提交 `f344eb7`（E1-1 核心拆分完成）。**只 commit 不 push**（推送需小北确认）。
   - 注意：工作区可能有未提交改动（平台中立性重构 docs/adr + llm/router + config + cli + 测试），先审阅提交，提交信息如「refactor: 平台中立性重构（ADR-002，并发策略可配置 + 能力矩阵可注入 + LLM_ 前缀）」。

## 第一步（先做）

检查工作区是否有未提交改动（平台中立性重构、docs/adr/ADR-002、test_client_serial.py 等）：若有，先审阅并提交；提交前跑 `pytest tests/` 确认 **43 全绿**。若工作区已干净则跳过。

## 主线任务（按顺序执行，做完一个提交一个）

### 前置：工程遗留清理（在阶段一前处理完，可合并为一两个提交）
- ~~`cli.py` 只 import basic/exposure~~（已修复：现 import astronomy/weather/site_match 全量注册）
- `.gitignore` 排除 `data/`（后续记忆数据不允许入库）
- 删除一次性脚本 `_add_astral.py`（astral 依赖已写入 pyproject）
- `ruff check src tests` 全量修复告警（`.venv\Scripts\python.exe -m ruff check --fix src tests`；有 noqa 注释的除外）
- `src/lighttrail/smoke.py` 第 60 行断言 bug（名实不符）修正

### 阶段一：地基拆分与可观测性（E1+E2）
- E1-1 核心拆分：agent/core 拆为 loop / context / router（`Agent.run` 与 `@registry.tool` 签名不变）——**已完成（f344eb7），跳过**
- E1-2 ContextBuilder 五层组装（静态前缀保持字节不变，服务 prompt 缓存命中）
- E2-1 TraceRecorder + 事件订阅接口（为 E7-4 SSE 桥接预留）
- E2-2 trace 注入 prompt + TraceReport（M2 依据/置信度来源；置信度规则化，不让模型自评）

### 阶段二：记忆层与配额感知（E3+E4）
- E3-1 用户档案（`data/profile.json` 常驻注入 ≤300 token）
- E3-2 事件记忆（SQLite 按需检索 + `search_memory` 工具）——**注意必须包含 `coordinates`（精确坐标）与 `weather_snapshot`（天气/天象快照）字段**，这是 PRD v0.3 M1.1-05 / D2.3-07 复拍提醒的数据前提
- E3-3 语义记忆（精选注入，写入 double-confirm 防污染）
- E4-1 ModelRouter 能力矩阵（能力声明驱动 + 单测覆盖矩阵）——**能力矩阵可注入已实现（平台中立性重构），完成本任务时注意不要回退为品牌硬编码**
- E4-2 QuotaLedger 配额账本（记账 / 预估 / 降级链；**注意：配额数字与「降级 max→plus」只适用于 ECNU 默认配置，降级链必须做成可配置/可注入，不能写死 ecnu-max → ecnu-plus**）
- E4-3 reason 通道（默认 deep 推理模型，`tools=None`，thinking 开启；**不要写死模型名，走 ModelRouter**）

### 阶段三：决策编排与输出契约（E5）
- E5-1 Orchestrator 四管线（灵感 / 规划 / 临场 / 复盘）+ PipelineContext
- E5-2 结构化输出契约（Intent / DecisionCard pydantic + 校验失败自愈 ≤2 次 + 降级 ReAct）
- E5-3 一句话出方案闭环（端到端；**用 Fake 数据源 + FakeChatClient 验证即可，不要依赖真实 API Key**）

每完成一个任务单元：
1. 跑 `pytest tests/` 全绿；该任务新增的用例必须通过
2. 中文 commit（如「feat: E1-1 核心拆分 loop/context/router」）

## 硬约束（违反即视为失败）

- **代码规范**：中文 docstring + Google 风格（Args/Returns/Raises）；`from __future__ import annotations` + 完整类型注解；ruff line-length=100；日志用 % 占位符（不用 f-string）；常量全大写下划线、私有方法前缀下划线；工具返回值字段名用中文、description 给具体示例
- **测试**：任何改动前先看 TODO/路线图的验收标准；新增功能必须配测试（计算正确性 + 边界条件）；所有改动后 `pytest tests/` 必须全绿
- **⚠️ 平台中立性（见文件开头）**：并发策略可配置、能力矩阵可注入、配置用 `LLM_` 前缀、不写死供应商模型名——四项硬规则，违反即失败
- **LLM/API**：**不要用真实 API Key 做联调**（.env 里若没有 key 就跳过真实调用，一律用 Fake/mock）；不向外部服务发送无谓请求
- **不动文件**：LICENSE、AGENTS.md、docs/design/（只读参考）；除非任务明确要求
- **禁忌**：LightTrail 是独立开源项目，与学位论文/学术研究**无关**，任何文档、代码、提交信息中严禁出现此类关联表述
- **架构决策**：不确定时先查 docs/architecture.md 与 docs/adr/；仍不确定就记入阻塞清单，不要擅自大改架构

## 进度与阻塞记录（必须维护）

在 `docs/devlog.md` 维护进度日志（不存在则创建）：
- 每个任务单元完成后追加一行：任务编号、完成时间、commit hash、测试数量
- 遇到阻塞（依赖装不上、测试诡异失败、需求歧义）：如实记录问题 + 排查过程 + 建议，然后跳到下一个不依赖它的任务；全部卡住就停下
- **平台中立性修复**：单独记录（改了什么、为什么），供后续审计

## 收尾

当你完成阶段三（E5-3）或决定停止时：
1. 最后一次跑 `pytest tests/` 全绿 + `ruff check src tests` 无新告警
2. `git add -A && git commit` 提交所有剩余改动（不 push）
3. 在 `docs/devlog.md` 写总结：已完成任务、剩余任务、遗留问题、给下次接手者的建议（**含平台中立性状态：是否还有历史残留未清理**）