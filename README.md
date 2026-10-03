# LightTrail · 光迹

面向摄影场景的 AI 拍摄决策引擎（Agent），以 **Web 应用**形态呈现，计划开源（MIT）。

> 不是「查数据」，而是「给决策」——沿「灵感 → 规划 → 决策 → 复盘」的拍摄旅程，综合多源数据 + 个性化记忆，直接回答「该不该出门、几点去、去哪拍、带什么、用什么参数」。

## 核心功能

- **一句话出方案**：模糊意图（「这周拍银河」）→ 综合天文、天气与收藏机位生成建议
- **照片反推方案**：上传参考图 → 反推拍摄条件与器材，生成候选复刻计划
- **机位 × 天象匹配**：按题材朝向与天象窗口匹配已配置坐标的收藏机位
- **多机位赶场调度（实验）**：比较最多四个机位的时间窗与交通成本；起点、离场和坐标系语义仍在收口
- **临场赌注决策**：结合天气、机位和风险给出“去 / 等等 / 放弃”的规则化建议
- **拍摄参数推荐**：曝光三角、星空 500/NPF 法则、长曝光 ND 换算
- **照片智能分析**：本地原图预览 + 多模态构图/曝光/色彩分析 + 可执行处方
- **个性化记忆**：本地保存器材、偏好、常拍点和确认过的拍摄事件
- **可解释性**：决策附依据、来源与置信信息，过程通过 SSE 轨迹面板实时可见

> 当前代码已具备演示骨架；R0 的装配、命名空间、语义评估和新环境门禁已收口，完整拍摄闭环仍在后续阶段。稳定/实验/规划边界及后续任务以 [`docs/AGENT-ROADMAP.md`](docs/AGENT-ROADMAP.md) 为准。

> 收藏机位的本地 API CRUD、坐标系校验及 M1 图形编辑器已接线；D2 已从本轮明确选择生成结构化太阳题材计划，并在用户主动加载后展示已确认机位的地图。计划可导出移除结构化经纬度的 JSON 快照，URL 保留题材、日期和机位 ID，导入只回放冻结行动与证据，不冒充实时重算；用户自填名称/来源仍需分享前核对。S1 全链路已用合成数据完成本地验收，未发送真实收藏机位坐标；费用和许可未独立核实，不应公开部署。无天气等关键事实时计划明确降级。道路路线图层依赖真实路线服务，当前赶场结果中的手工估算不等于道路导航。

## 技术方向

- **Agent 框架**：基于学校大模型开放平台（华东师大开发者平台，OpenAI 兼容 API）
  - Base URL：`https://chat.ecnu.edu.cn/open/api/v1`
  - 主模型：`ecnu-max`（1M 上下文深度推理）/ `ecnu-plus`（工具调用 + 多模态）
- **架构形态**：FastAPI 薄服务层 + SSE 事件流 + SPA 前端（「trace 即 UI」），CLI 双入口保留
  - 代码化管线（确定性编排）+ ReAct 自由对话双模混合
  - 四层记忆（短期/档案/事件/语义）+ 配额感知路由（QuotaLedger）
  - 结构化输出契约（pydantic 自愈重试）+ 三层评估体系（工具 pytest / 黄金用例集 / LLM-as-judge）
- **语言**：Python（近期主用）
- 探索大模型在垂直场景（摄影）中的应用方法
- 架构基线详见 [`docs/architecture.md`](docs/architecture.md)；后续任务唯一真源见 [`docs/AGENT-ROADMAP.md`](docs/AGENT-ROADMAP.md)

## 文档地图

不同文档承担不同职责，不能互相替代：

| 文档 | 面向对象 | 作用 |
|---|---|---|
| [`AGENTS.md`](AGENTS.md) | 编码 Agent | 工程约束、代码风格、验证命令和当前会话规则；属于强约束 |
| [`docs/AGENT-ROADMAP.md`](docs/AGENT-ROADMAP.md) | 编码 Agent / 项目维护者 | 当前状态、长期规划、短期可执行任务和批次闸门；**后续开发唯一执行真源** |
| [`docs/PRD-v0.3.md`](docs/PRD-v0.3.md) | 产品与开发 | 产品定位、需求范围和需求编号；不维护实时完成度 |
| [`docs/architecture.md`](docs/architecture.md) | 开发者 | 当前架构、模块边界和已知接线缺口 |
| [`docs/adr/`](docs/adr/) | 开发者 | 已拍板的技术决策及其约束 |
| [`docs/design/FRONTEND-SPEC.md`](docs/design/FRONTEND-SPEC.md) | 前端开发者 | 页面信息架构、设计令牌、组件和交互规范 |
| [`docs/devlog.md`](docs/devlog.md) | 项目维护者 | 已完成工作和问题发现的历史记录，不产生新任务 |
| [`docs/CODE-REVIEW.md`](docs/CODE-REVIEW.md) | 编码 Agent / 审查者 | 代码审查分级标准、六维检查清单、审查流程和回归清单 |
| [`docs/research-chuweimin-photography-site.md`](docs/research-chuweimin-photography-site.md)、其他 `research-*` / `competitive-*` | 产品研究 | 调研证据和启发，不是 Agent 指令；已采纳内容会被转化到路线图 |
| [`docs/REFACTOR-ROADMAP.md`](docs/REFACTOR-ROADMAP.md)、[`docs/architecture-v4-proposal.md`](docs/architecture-v4-proposal.md) | 项目维护者 | B0–B7 重构的历史验收与论证，不再领取任务 |

阅读建议：想了解项目是什么看本 README；想开始编码先读 `AGENTS.md` 和 `docs/AGENT-ROADMAP.md`；想确认当前架构看 `docs/architecture.md`；想理解产品边界看 PRD；想了解为什么做某项设计再查 ADR 或调研报告。

## 目录结构

```
LightTrail/
├── docs/                # Agent 路线图 / PRD / 当前架构 / ADR / 调研证据 / 设计原型
├── src/lighttrail/      # 源码（contracts / runtime / adapters / 领域模块）
│   ├── contracts/       # 零依赖契约与端口
│   ├── adapters/        # LLM / 数据源适配实现
│   ├── application/     # 应用服务与用例编排
│   ├── domain/          # 领域模型与纯规则
│   ├── runtime/         # AgentRuntime / ToolRegistry / ContextBuilder
│   ├── llm/             # ModelRouter 能力矩阵
│   ├── orchestrator/    # 四管线编排 + Intent/DecisionCard 契约（pydantic 自愈）
│   ├── memory/          # 四层记忆：档案 / 事件（SQLite）/ 语义 / Manager
│   ├── infra/           # TraceRecorder / 置信度规则 / QuotaLedger 配额账本
│   ├── api/             # Web 服务层：FastAPI + SSE + SessionManager
│   └── tools/           # 15 个已注册工具（曝光/天文/天气/机位匹配/记忆/照片分析/反推）
├── frontend/            # SPA 前端（Vite + React + TS，6 页决策旅程；契约类型 npm run gen:api 生成，
│                        #   壳层自检 npm run check:ui 六页路由逐一渲染校验）
├── evals/               # 三层评估：黄金用例 cassette + LLM-as-judge
├── scripts/             # 真实联调自检（scripts/live_check.py，真实 Key、配额克制）
├── tests/               # 离线 pytest 用例（Fake 数据源，不触网）
├── data/                # 全局只读知识 + data/users/{user_id}/memory 本地记忆（不入库）
└── README.md
```

## 当前状态

- [x] 项目初始化
- [x] Agent 骨架（多轮对话 + 工具调用最小链路）
- [x] 需求文档 v0.3（[docs/PRD-v0.3.md](docs/PRD-v0.3.md)，含主动提醒能力族 + v0.4 范围修订）
- [x] 决策主线工具层：**15 个工具已注册**（参数推荐 / 天文查询 / 天气 / 火烧云评分 / 机位×天象匹配 / 记忆检索 / 照片分析 / 反推）
- [x] UI 高保真原型（[docs/design/delivery/lighttrail-prototype.html](docs/design/delivery/lighttrail-prototype.html)，6 页 SPA）
- [x] 地基拆分与可观测性（E1+E2：ContextBuilder 五层组装 / TraceRecorder / 置信度规则表）
- [x] 记忆层与配额感知（E3+E4：四层记忆 / ModelRouter 能力矩阵 / QuotaLedger / reason 深推理通道）
- [x] 决策编排（E5：四管线端到端闭环 + 一句话出方案 + 追问回落 ReAct；ADR-002 平台中立性落地）
- [x] 多模态与差异化骨架（E6：照片分析 / 反推 / 独立复盘卡 / 语义记忆提炼；完整 CaptureEvent 闭环留待 E1）
- [x] Web 服务层（E7：FastAPI/SSE + trace→SSE 桥接 + SPA 六页旅程）
- [x] 评估体系（E8：L2 黄金用例 cassette 回放零成本 + L3 LLM-as-judge「工具即裁判」）
- [x] **B5 架构重建收口**：用户记忆命名空间、领域知识种子、真实机位坐标、到期 shim 清理、ADR-004 与架构文档同步
- [x] **B6/B7 架构重建收口**：全局知识库与 FTS5 检索、机型规格案例、规则优先路由、受控 Planner 与 ReAct 护栏
- [x] **C0 演示链路补齐**：照片反推入口、题材感知规划、配额降级与结构化时间窗已完成真实最小联调
- [x] **R0 真实性收口**：统一生产装配、用户命名空间、日期/地点语义和语义评估；全新环境门禁及最小真实 LLM 联调通过
- [ ] **S1–E1 产品闭环**：结构化 SpotPlan、DecisionCard v2、CaptureEvent 拍摄—复盘闭环
- [ ] 开源发布（核心拍摄闭环完成后）
- [ ] 主动提醒服务（延后，先验证 CaptureEvent 数据闭环）

后续开发只按 [docs/AGENT-ROADMAP.md](docs/AGENT-ROADMAP.md) 推进。已完成的 B0–B7 验收记录见 [docs/REFACTOR-ROADMAP.md](docs/REFACTOR-ROADMAP.md)，旧路线图位于 `docs/archive/`。

## 当前用户数据与账户边界

目前是本地单用户演示，没有注册、登录或身份认证。Web 请求统一使用 `_local` 用户标识；`user_id` 命名空间已预留，但这不构成可对外开放的多用户系统。默认数据目录为项目下的 `data/`，也可用 `LIGHTTRAIL_DATA_DIR` 配置：档案、事件和语义经验分别保存在 `users/_local/memory/profile.json`、`events.db`、`semantic.json`；会话历史保存在 `sessions/{session_id}.json`。浏览器 `localStorage` 只保存会话索引、当前会话 ID 和最近决策卡。

照片上传后用于复盘的服务器临时文件会在分析结束时删除；画面会发送给配置的多模态模型服务进行分析。模型请求还可能携带本轮注入的档案摘要与检索到的记忆；天气工具向 Open-Meteo 发送查询坐标。当前事件记录需用户在 D4 明确点击确认，尚未将计划、执行、照片与复盘关联成统一 CaptureEvent。精确坐标、照片引用和数据导出规则仍按路线图 S1/E1/P1 的隐私决策推进。

## 本地安装与离线验收（PowerShell）

Python 3.10+ 和 Node.js/npm 就绪后，在项目根目录执行。Python 依赖以 `pyproject.toml` 为准，`requirements.txt` 仅保留运行时兼容清单；所有包安装在项目虚拟环境，不写入系统 Python。

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
$npmCache = Join-Path (Get-Location) ".npm-cache"
npm --prefix frontend ci --cache $npmCache
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m lighttrail.smoke
.\.venv\Scripts\lint-imports.exe
.\.venv\Scripts\python.exe -m evals.runner --level L2
npm --prefix frontend run gen:api
npm --prefix frontend run build
```

这些命令无需 LLM Key；L2 使用独立的**合成离线夹具**，不代表实时天气或真实模型质量。检查六页 UI 时，先在一个终端运行 `npm --prefix frontend run dev -- --host 127.0.0.1`，再在另一终端运行 `npm --prefix frontend run check:ui`，检查完关闭开发服务器。真实模型调用才需要在本地 `.env` 配置 `LLM_API_KEY`；`.env` 与用户数据目录不会入库。
