# LightTrail（光迹）· Codex 长期开发目标

> 本文档是 Codex 进行长时间开发时的执行目标与工作协议。它只解决三件事：现在在哪里、接下来做什么、什么情况下必须停下来汇报。
>
> 方案权威：[`docs/architecture-v4-proposal.md`](architecture-v4-proposal.md)（D1–D14）。
> 已完成的架构任务：[`docs/REFACTOR-ROADMAP.md`](REFACTOR-ROADMAP.md)（B0–B7）。
> 后续任务权威：[`docs/POST-REFACTOR-ROADMAP.md`](POST-REFACTOR-ROADMAP.md)（C0–C2）。
> 当前待办：[`TODO.md`](../TODO.md)。
> 归档目录中的文档只作历史记录，不得作为当前设计、任务或状态依据。

## 📍 当前状态

截至 2026-09-17：

```text
当前位置：B0–B7、C0 已完成；等待小北确认后进入 C1
代码：main / B0–B7、C0 均已独立提交；当前以 git status 为准
后端：pytest 366 passed / ruff 0 / smoke 21 项
架构：lint-imports 3 kept / 0 broken
前端：npm run build 通过；六页 + 未知路由 check:ui 7/7 通过
契约：npm run gen:api 后 git diff --exit-code 通过
产品资产：15 个声明式工具、AgentRuntime、四条管线、FastAPI + SSE、六页 SPA、L2/L3 evals
已完成批次：B0、B1、B2、B3、B4、B5、B6、B7、C0
未完成批次：C1、C2（后重构能力迭代，须逐批确认）
```

当前最重要的判断：v4 架构重构已完成；后续功能迭代必须建立在规则优先路由、受控 Planner、用户记忆和全局知识库的真实链路上，禁止把接口预留、测试 Fake 或设计稿误报为真实功能完成。

## 🎯 长期目标

把 LightTrail 建成一个可演示、可测试、可解释、平台中立的摄影拍摄决策引擎：

1. 已知摄影任务走确定性的四条决策管线；开放但可控的任务走受限 Plan-Execute；开放问答才使用 ReAct。
2. 工具通过声明式 `ToolSpec` 接入，由装配根构造注册表；新增工具不复制修改 trace、置信度、提示词和前端协议。
3. `contracts/` 是零依赖契约真源；前后端契约由 OpenAPI 生成 TypeScript，不维护第二份手抄模型。
4. 记忆是按用户隔离的可写数据，知识库是全局只读数据；两者通过明确端口接入上下文，不互相污染。
5. 所有外部数据、模型调用、配额、观测和密钥都位于适配层或端口之后；业务层不绑定具体平台品牌。
6. 每个判断都能说明结论、依据、置信度、数据来源和降级原因；没有数据时诚实降级，不用假数据补齐。
7. 任何一次改动都能通过离线测试、架构门禁和真实最小联调验证，项目始终保持随时可演示。

## 🧭 执行路线

架构重构已完成，历史路径如下：

```text
B0 ✅ 止血护栏
  → B1 ✅ 契约层与配置
  → B2 ✅ 声明式引擎与 AgentRuntime
  → B3 ✅ 适配层与 async-first 并发
  → B4 ✅ OpenAPI 单一真源与前端重接
  → B5 ✅ 记忆命名空间、清理、文档
  → B6 ✅ 知识库链路
  → B7 ✅ 意图路由与受控规划通道
```

已完成 B5：

1. B5-1：把记忆存储迁移到 `data/users/{user_id}/`，本期默认用户可使用 `_local`，完成存量迁移和隔离测试。
2. B5-2：拆分用户语义记忆与领域知识种子，明确各自端口、来源和生命周期。
3. B5-3：统一 `favorite_spots` 键形状，消除双份默认坐标；空档案必须诚实降级；同时显式化上下文预算。
4. B5-4：删除到期 shim、死代码和失实依赖注释，令 `requirements.txt` 与 `pyproject.toml` 一致。
5. B5-5：落盘 ADR-004，重写 `docs/architecture.md` 为当前目标架构，并同步 README、AGENTS、TODO、devlog 和本文件。

已完成 B6：全局只读知识库、FTS5 中文检索、命中按需注入，以及档案机型驱动 NPF 像素间距查询。

已完成 B7：规则优先 + 可审计 LLM 兜底路由、受控 Plan-Execute、超工具集诚实告知、ReAct 护栏和带 RequestContext 的零成本 L2 回放。

C0 已通过闸门：真实 Key 下的银河规划（含 `match_sites`）与参考图反推均跑通，临时档案已还原。后续从 C1 的最小原子任务开始；涉及拍摄历史写入的改动仍须明确用户确认语义。

## 🔍 每次启动的工作协议

### 1. 先确认真实状态

依次读取：

- `AGENTS.md`
- `docs/architecture-v4-proposal.md`
- `docs/POST-REFACTOR-ROADMAP.md` 的当前批次章节（必要时查 B0–B7 历史记录）
- `TODO.md`
- `docs/devlog.md` 顶部最新记录
- 与当前任务直接相关的源码和测试

随后执行只读检查：

```powershell
git status --short --branch
git log -1 --oneline --decorate
rg --files -g '!docs/archive/**'
```

若文档状态、Git 状态和代码不一致，以当前代码和测试为事实，并在本次任务中修正文档；不得依据归档文档恢复旧路线。

### 2. 选择最小可交付任务

- 一次只做一个路线图原子任务，任务编号必须明确。
- 先读该任务的目标、前置依赖、涉及文件和验收标准。
- 不为“顺手优化”扩大范围；发现非本批问题，记录到 `TODO.md` 或 devlog，不擅自插队。
- 设计和接口存在歧义时，以 v4 方案为准；若会改变已确认的方向，停止并询问小北。

### 3. 实现与验证

- Python 使用 py310+、完整类型注解、中文 Google 风格 docstring、ruff line-length 100。
- 默认使用项目 `.venv`；禁止污染系统 Python，禁止全局安装 npm/pip 依赖。
- 单元测试优先使用 Fake、TestModel、cassette，不因普通开发任务调用真实 LLM。
- 新工具必须有计算/边界测试，并符合声明式注册和 ToolContext 约定。
- 任何契约变更都必须重新生成 OpenAPI TypeScript。
- 任何架构边界变更都必须运行 import-linter 和架构测试。

## 🚪 批次闸门

每个批次内部可以连续完成原子任务；到批次出口必须停下。闸门不是形式检查，而是方向性决策点。

### 通用出口检查

后端：

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\ruff.exe check src tests
.venv\Scripts\lint-imports.exe
```

前端：

```powershell
cd frontend
npm run build
npm run gen:api
git diff --exit-code -- src/api/generated.ts
```

必要时补充：

```powershell
.venv\Scripts\python.exe -m lighttrail.smoke
.venv\Scripts\python.exe scripts/live_check.py
```

真实联调只在明确需要时执行，使用真实 Key 前必须确认预算和数据范围；浏览器自检必须先启动当前前端服务，不能把“服务未启动导致白屏”误判为代码回归。

### 闸门报告必须包含

- 已完成的任务编号和 commit
- 测试、ruff、import-linter、前端 build、契约生成结果
- 真实联调是否执行及结果
- 尚未完成项、遗留问题和新增风险
- 文档是否已同步到当前 HEAD
- 下一批的第一项任务

报告完成后停止，不自动进入下一批。

## 🧱 不可违反的约束

1. `docs/architecture.md` 是当前架构基线；v4 提案与 B0–B7 路线图是历史设计与验收依据，C0–C2 以新路线图为准。
2. 归档文档不具备当前参考价值，不得用于决定任务、接口或完成状态。
3. 不把“接口存在”说成“功能完成”，不把 Fake、示例数据或设计稿说成真实数据。
4. 业务代码不能写死模型品牌判断；模型选择经过 `ModelRouter`，LLM 调用经过 Provider/适配层。
5. 并发是配置，不是 ECNU 业务前提；`LLM_CONCURRENCY` 是唯一配置，设为 1 即串行。
6. `contracts/` 保持零依赖；适配器不能被领域层反向依赖；架构边界必须由自动化门禁保护。
7. 敏感信息不得进入日志、trace、测试输出、生成物或提交。
8. 不修改 `LICENSE` 和 `docs/design/delivery/lighttrail-prototype.html`，它们是项目法律文件和只读设计真源。
9. 不执行 `git reset --hard`、`git checkout --`、递归删除等破坏性操作，除非小北明确要求。
10. commit 可以在任务完成后创建；push、PR、发布、开源、对外宣传、真实外部服务写入等动作必须先获得确认。
11. LightTrail 是独立开源项目；正式文档、代码注释和提交信息不得引入与学位论文或学术研究的关联叙事。

## 📝 文档与提交纪律

每个原子任务完成后：

1. 更新 `docs/devlog.md`，记录日期、任务编号、commit、验证结果和偏差。
2. 若当前状态、下一步或基线变化，更新本文档顶部“当前状态”。
3. 若路线图、架构或产品范围发生变化，只改权威文档，并同步受影响的 README/AGENTS/TODO。
4. commit 信息使用简洁中文，格式示例：`fix(B5-3): 统一档案机位坐标来源`。
5. 不为了凑提交拆出无意义 commit，也不把多个无关任务混入一个 commit。

最终目标不是“把所有 TODO 勾完”，而是让 LightTrail 的架构边界、决策能力、数据来源和验证证据彼此一致，并且任何新接手的 Codex 都能从本文件继续推进而不依赖隐含记忆。
