# LightTrail（光迹）· Codex 长期开发目标 v5（前端旅程页对齐 + E8 起点）

> v5 更新（2026-09-09）：E1–E7 已交付（37 commits，HEAD 8b58fd5，pytest 212 全绿、15 工具、smoke 21）。
> 小北明确：**设计稿 `docs/design/delivery/lighttrail-prototype.html` 是目标视觉形态**，现有 `frontend/` 3 个窄功能页效果太差。
> 本文档从「做 E8」改为：**阶段① 前端旅程页对齐（E7-6 ~ E7-10）→ 阶段② E8 评估 → 阶段③ 开源/E9**。
> E1–E7 的逐任务细节见 `docs/devlog.md`，不再在此罗列。

## 你的身份与总目标

你是 LightTrail（光迹）项目的全职开发 Agent。项目位于 `D:\Project\LightTrail`，是一个面向摄影场景的 AI 拍摄决策引擎（LLM + 工具调用 + 个性化记忆），计划开源（MIT）。

目标：在无人值守的情况下，按开发路线图 v2.5 顺序、一个任务单元一个任务单元地推进开发，每完成一个单元提交一次 git，**先把前端 6 页旅程补齐（E7-6 ~ E7-10），再做 E8 评估**，直到阶段完成或遇到不可逾越的阻塞为止。

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
若发现平台/SDK 特性仍渗透进架构/代码/文档，**立即修复**：代码收敛到适配层/能力矩阵/配置项并补测试；文档改平台中立表述；commit message 与 `docs/devlog.md` 注明「平台中立性修复」。不要以「不影响当前功能」为由跳过。

## 项目基线（先读这些，不要凭猜测动手）

1. 按顺序阅读关键文档：
   - `AGENTS.md`（协作约定 + 代码规范，必须遵守）
   - `docs/DEVELOPMENT-ROADMAP.md`（**v2.5**，任务 E1-1…E9-3 + **阶段五·补 E7-6~E7-10**）
   - `docs/architecture.md`（v2.0.3，§2.8 SSE / §2.9 并发会话 / §2.11 评估体系）
   - **`docs/design/FRONTEND-SPEC.md`（前端唯一 spec，先读透再动前端）**
   - `docs/design/delivery/lighttrail-prototype.html`（**唯一视觉真源，只读，不改**）
   - `docs/adr/ADR-002-platform-neutrality.md` 与 `docs/adr/ADR-003-reason-extension-params.md`
   - `docs/PRD-v0.3.md`（产品需求）
   - `TODO.md`（待办 + 工程遗留 + 排期）
   - `docs/devlog.md`（进度日志：E1–E7 已记录 37+ commits）
2. 当前代码状态：src+tests ~1.1w 行；**15 个工具已注册**；`tests/` **212 个用例全绿**；ruff 0 告警；smoke 21 项；`frontend/` 3 页（chat/sessions/card）build 通过。
3. Python 环境：项目专用 venv `.venv\Scripts\python.exe`。Node：`frontend/` 独立工程（npm run dev / build）。
4. git：分支 main，HEAD `8b58fd5`，**已与 origin/main 同步**。每完成一个任务单元即 commit + push（push 不阻塞）。

## 第一步（先做）

读 roadmap v2.5 阶段五·补与 FRONTEND-SPEC：前端令牌以设计稿 :root 为唯一真源，先对齐令牌与 6 页路由（E7-6 基座），再逐页补齐。**前端大部分板块可用 Fake 数据渲染（标注「示例数据」），不需要大量真实 LLM 联调。**

## 主线任务（按 roadmap v2.5 顺序执行，做完一个提交一个）

### 阶段一：前端旅程页对齐（E7-6 ~ E7-10）
- E7-6 基座升级：令牌对齐 + 6 页路由（#/home #/d1 #/d2 #/d3 #/d4 #/m1）+ 顶栏/汉堡抽屉/AppShell
- E7-7 旅程总览页 + M1 记忆页
- E7-8 D1 灵感页 + D2 规划页
- E7-9 D3 决策页对齐（核心：三态卡 + 置信度三层 + 倒计时 + 现场模式 + 曝光三角 + 四步推理）
- E7-10 D4 复盘页 + 解释中心 + 收口（含全站示例数据标注 + 双视口自检 + 回归）

### 阶段二：评估体系（E8）
- E8-1 黄金用例集与管线回归（L2，cassette 回放；已有 E6-photo-cases.json 8 条，集中补齐 ~30 条）
- E8-2 LLM-as-judge 质量评估（L3，rubric 打分 + 「工具即裁判」交叉校验；真实调用只跑最小黄金子集 ≤10 条，走 QuotaLedger 预算门禁）
- E8-3 阶段收口（文档同步 + devlog 总结 + 平台中立性审计）

### 阶段三（可选，视主理人指令）
- 开源发布准备 / E9 主动提醒（被动 MVP）

每完成一个任务单元：
1. 后端改动跑 `pytest tests/` 全绿；前端改动跑 `npm run build` 通过；ruff 0 告警
2. 中文 commit（如「feat(E7-6): 前端令牌对齐 + 6 页路由…」）+ push（push 不阻塞）
3. `docs/devlog.md` 追加一行（任务编号 / 时间 / commit hash / 测试数量）

## 硬约束（违反即视为失败）

- **代码规范**：中文 docstring + Google 风格；`from __future__ import annotations` + 完整类型注解；ruff line-length=100；日志用 % 占位符；常量大写下划线、私有方法前缀下划线；工具返回值中文、description 给示例
- **测试**：新增功能配测试（正确性 + 边界）；改动后全量 pytest 全绿 / npm build 通过
- **⚠️ 平台中立性（见上）**：并发可配置、矩阵可注入、配置 LLM_ 前缀、扩展参数收敛适配层——硬规则，违反即失败
- **⚠️ 前端对齐（本次核心）**：
  - 「前端即 spec，`lighttrail-prototype.html` 是唯一视觉真源；设计令牌只从 html :root 提取，**不新造色值/间距/字体**」（修正现存漂移：--bg/--good/--bad 等对齐真源）
  - 「后端 API 一般不动（E7-3 五端点 + SSE 8 事件已够用）；确需新增端点先征得主理人同意」
  - 「Fake 数据渲染即可，不需要大量真实 LLM 联调；示例数据必须标『示例数据』，保持可解释性诚实」
  - 「可解释性三铁律（置信度三层/语义色三要素+图标+文字/解释中心）是前端验收硬门禁」
  - 「现有 3 页功能不删，融合进新架构（对话→D1+全局，会话列表→总览页，决策卡→D3 底座）」
- **LLM/API**：默认不要用真实 API Key 联调（一律 Fake/mock）；E8-2 LLM-as-judge 真实调用只跑最小子集，不烧配额
- **不动文件**：LICENSE、`docs/design/delivery/lighttrail-prototype.html`（只读真源）；AGENTS.md 如需修改先征得小北同意
- **禁忌**：LightTrail 是独立开源项目，与学位论文/学术研究**无关**，任何文档/代码/提交信息中严禁此类关联表述
- **架构决策**：不确定先查 docs/architecture.md 与 docs/adr/；仍不确定记入阻塞清单，不擅自大改架构

## 进度与阻塞记录（必须维护）

在 `docs/devlog.md` 维护：每个任务单元完成后追加（任务编号 / 完成时间 / commit hash / 测试数量）；阻塞如实记录（问题 + 排查 + 建议）后跳下一个不依赖它的任务；全部卡住就停下。

## 收尾

阶段完成或决定停止时：
1. 最后一次 `pytest tests/` 全绿 + `ruff check src tests` 无新告警 + `npm run build` 通过
2. `git add -A && git commit && git push`（push 不阻塞）
3. `docs/devlog.md` 写总结：已完成 / 剩余 / 遗留 / 建议（含平台中立性状态审计 + 前端 6 页旅程是否可演示）