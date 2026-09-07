# LightTrail（光迹）· Codex 长期开发目标 v3（E7 起点）

> v3 更新（2026-09-07 深夜）：E1–E6 已全部交付并 push（29 commits，origin/main HEAD 0b502a6，
> pytest 177 全绿、15 工具、smoke 21 项）。本文档从「做到 E6」换代为目标 **E7 阶段完成**；
> E1–E6 的逐任务细节见 `docs/devlog.md`，不再在此罗列。

## 你的身份与总目标

你是 LightTrail（光迹）项目的全职开发 Agent。项目位于 `D:\Project\LightTrail`，是一个面向摄影场景的 AI 拍摄决策引擎（LLM + 工具调用 + 个性化记忆），计划开源（MIT）。

目标：在无人值守的情况下，按开发路线图 v2.3 顺序、一个任务单元（E7-0、E7-1…）一个任务单元地推进开发，每完成一个单元提交一次 git，一直做到**阶段五（E7）完成**或遇到不可逾越的阻塞为止（E7 收口后由主理人评估是否续 E8 评估体系）。

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
   - `docs/DEVELOPMENT-ROADMAP.md`（**v2.3**，任务 E1-1…E9-3 + E6-0/E7-0 联调开场任务，你按它干活）
   - `docs/architecture.md`（**v2.0.2**，含 §2.8 Web 交互层 SSE 协议 / §2.9 并发与会话模型——E7 的架构依据）
   - `docs/adr/ADR-002-platform-neutrality.md` 与 `docs/adr/ADR-003-reason-extension-params.md`
   - `docs/PRD-v0.3.md`（产品需求）
   - `TODO.md`（待办 + 工程遗留 + 排期）
   - `docs/devlog.md`（进度日志：E1–E6 已记录 29+ commits，先读收尾总结与遗留问题）
   - `docs/design/delivery/lighttrail-prototype.html`（高保真原型，E7-5 SPA 的 spec，只读参考）
2. 当前代码状态：src+tests ~8700 行；**15 个工具已注册**（含 analyze_photo / reverse_engineer_photo / search_memory）；`tests/` **177 个用例全绿**；ruff 0 告警；smoke 21 项通过。
3. Python 环境：项目专用 venv `.venv\Scripts\python.exe`。**新增依赖一律装进 .venv**（E7 新增 fastapi/uvicorn/httpx 等装这里）。
4. git：分支 main，HEAD `0b502a6`，**已与 origin/main 同步**（29 commits 已 push）。E7 每完成一个任务单元即 commit + push（push 不再是阻塞项，远程基线已建立）。

## 第一步（先做）

读 roadmap v2.3 阶段五 E7-0「真实联调开场」：E7-0 同样以真实 Key + SSE 长连接联调验证开场（架构 v2.0 §2.8 事件协议）。E7 阶段每任务首步真实联调，禁止仅凭 mock 判定完成。**真实联调用配额要克制**——用最小 query 验证链路通即可，不烧配额。

## 主线任务（按 roadmap v2.3 顺序执行，做完一个提交一个）

### 阶段五：Web 服务层（E7）
- E7-0 真实联调开场（真实 Key + SSE 长连接，验证现有 CLI 链路在 Web 侧可跑）
- E7-1 async ChatClient + 全局 LLM 队列（`acall()` + `asyncio.Semaphore`，并发边界由 `serial_llm` 驱动；同步 `chat()` 保留薄封装；CLI 路径不变）
- E7-2 SessionManager 会话持久化（JSON 落盘，跨请求恢复会话与轨迹）
- E7-3 FastAPI + SSE 路由（五端点：`/api/chat` `/api/decide` `/api/photos/review` `/api/profile` `/api/sessions/{id}`；事件协议见架构 v2.0 §2.8）
- E7-4 trace→SSE 桥接（TraceRecorder.subscribe 挂 async 适配器，管线/ReAct 事件零转换入 SSE 队列——「trace 即 UI」核心落地）
- E7-5 前端 SPA 工程化（Vite + React + TS，接高保真原型；**首迭代只做 3 核心页**：对话 / 会话列表 / DecisionCard 渲染；Trace 时间线与设置页第二迭代；原型即规格，不做二次设计）

每完成一个任务单元：
1. 跑 `pytest tests/` 全绿；该任务新增用例必须通过；ruff 0 告警
2. 中文 commit（如「feat(E7-1): async ChatClient + 并发策略…」）+ push（远程基线已就绪，push 不阻塞）
3. `docs/devlog.md` 追加一行（任务编号 / 时间 / commit hash / 测试数量）

## 硬约束（违反即视为失败）

- **代码规范**：中文 docstring + Google 风格；`from __future__ import annotations` + 完整类型注解；ruff line-length=100；日志用 % 占位符；常量大写下划线、私有方法前缀下划线；工具返回值中文、description 给示例
- **测试**：改动前先看 roadmap 验收标准；新增功能配测试（正确性 + 边界）；改动后全量 pytest 全绿
- **⚠️ 平台中立性（见上）**：并发可配置、矩阵可注入、配置 LLM_ 前缀、扩展参数收敛适配层——硬规则，违反即失败
- **LLM/API**：默认不要用真实 API Key 联调（一律 Fake/mock）；每阶段 E*0 真实联调用最小 query 验证链路，不烧配额；不向外部服务发无谓请求
- **CLI 路径不变**：E7 的 async/Web 改造不得破坏现有 `cli.py` 与 `--pipeline` 自由对话路径；同步 `chat()` 保留为薄封装
- **前端裁剪**：E7-5 SPA 首迭代严格 3 页，不贪多；原型 HTML 即 spec，不再二次设计
- **不动文件**：LICENSE、docs/design/（只读参考，原型即 spec）；AGENTS.md 如需修改先征得小北同意
- **禁忌**：LightTrail 是独立开源项目，与学位论文/学术研究**无关**，任何文档/代码/提交信息中严禁此类关联表述
- **架构决策**：不确定先查 docs/architecture.md（v2.0.2，§2.8/§2.9 是 E7 依据）与 docs/adr/；仍不确定记入阻塞清单，不擅自大改架构

## 进度与阻塞记录（必须维护）

在 `docs/devlog.md` 维护：每个任务单元完成后追加（任务编号 / 完成时间 / commit hash / 测试数量）；阻塞如实记录（问题 + 排查 + 建议）后跳下一个不依赖它的任务；全部卡住就停下。

## 收尾

E7-5 收口或决定停止时：
1. 最后一次 `pytest tests/` 全绿 + `ruff check src tests` 无新告警
2. `git add -A && git commit && git push`（远程基线已就绪，push 不阻塞）
3. `docs/devlog.md` 写总结：已完成 / 剩余 / 遗留 / 建议（含平台中立性状态审计 + Web 形态是否可演示）
