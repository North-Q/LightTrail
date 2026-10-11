# LightTrail · 光迹

面向摄影场景的 AI 拍摄决策引擎：把天文、天气、器材和个人经验转化为“是否出门、几点去、去哪、带什么、怎么拍”的行动建议。长期覆盖灵感、规划、决策、复盘，计划开源。

U5最小结果反馈已本地离线验收：D3历史结果下独立记录出门、观察与原因，未观察结果为null；支持修改、撤回及本人两步经验确认。原卡与有效期保留，经验仅本地回看、尚未纳入Agent记忆，首版不上传照片。全量729、相关76、V01–V15、前端构建与真实组件浏览器通过，见[U5验收](docs/audit/2026-10-11/u5-verification.md)。U4现场效用仍无法判读，当前停止，不进入U6。

**当前是本地开发与离线演示版本。** 收藏机位维护、结构化单机位计划、脱敏 JSON 导出与冻结回放已有本地验证；U1、U2-1、U2-2、U2-3、U3 已修复文字数据资格、许可取消、重试预算、基础计算/规格接线与本地权限边界；恢复、计算近似和公网部署仍有边界。尚未通过“今晚火烧云是否值得去”的真实场景验收，多机位赶场为实验能力。

完整状态见 [当前与未来能力](docs/FEATURES.md)，原始证据见 [2026-10-06 审查](docs/AUDIT-2026-10-06.md)。U1 已修复文字决策的数据失败、空值与时效资格，483 项 pytest 与合成页面通过；[离线验收入口](docs/audit/2026-10-06/u1-verification.md) 可在不调用真实 API 的情况下复核。通过不表示天气判断准确或已完成公开部署。

U2-1 全量 pytest **504 项**通过：自定义阶段超时实际接线，SDK 重试关闭，每次模型调用最多三次底层尝试；四个拍摄页面可停止等待并拦截迟到结果。停止等待不能确认服务端线程或供应商请求已取消，详见 [U2-1 离线验收](docs/audit/2026-10-06/u2-1-verification.md)。

U2-2 全量 pytest **548 项**通过：S5M2 生产自动补全近似 NPF，短快门保留数值，分数/小数按单位比较，时间偏移正确；太阳逐事件缺失，月相区分索引和估算。来源、前后数值和复核步骤见 [U2-2 离线验收](docs/audit/2026-10-06/u2-2-verification.md)。

U2-3 全量 pytest **579 项**通过：本机写来源、服务端凭据与照片路径边界，以及发送/保留/删除说明；SSE 延迟 step 与保存异常缺 done 有确定性回归。公开 JS SDK 标识按小北确认保留，服务端密钥隐藏。见 [U2-3 离线验收](docs/audit/2026-10-07/u2-3-verification.md)和[数据边界](docs/DATA-BOUNDARIES.md)。U3 已进一步保存并恢复实际结果版本，见下文。

U3 全量 pytest **591 项**通过：实际卡/消息/管线/SpotPlan 版本落盘，按稳定 ID 跨应用重建恢复；Fake 收到保存历史，重查保留原结果。浏览器关闭后恢复、完整 SPA 与双视口通过；回看不发起模型或数据查询，也不续期。步骤与边界见 [U3 离线验收](docs/audit/2026-10-07/u3-verification.md)。

**U4 唯一真实晨间生成已执行，本人未出门，无独立现场观察，现场效用无法判读**：[真实记录](docs/audit/2026-10-09/u4-real-verification.md)保留原模型1/天气1、原异常及保存/真实D3零外调恢复；[SDK生命周期](docs/audit/2026-10-09/u4-sdk-lifecycle-verification.md)离线修复保留。前批[通勤与器材前提修复](docs/audit/2026-10-09/u4-shooting-constraints-verification.md)通过671项回归（新增31）：明确通勤与布置约束限制主方案/备选，未知三脚架、ND和最大光圈不视为已具备，原卡出发前需手动更新、回看不续期且不会自动查询。原真实卡未改写、未真实重跑；当前规则为保守核验，现场效用未认证。U4未全部完成，本批结束后停止，不进入U5/U6、不提交或发布；输出控制和本应用账本不保证整轮费用硬上限或平台余额。

2026-10-11 [最大光圈声明窄修复](docs/audit/2026-10-11/u4-aperture-assertion-verification.md)：实际临场管线先复现“如果／若14mm最大光圈为f2.8”被误确认，修复后条件、否定及冲突声明保持未知，明确肯定正例保留。新增34项、相关238项回归通过；无真实调用，不扩展通用语义解析。

## 文档入口

| 文档 | 作用 |
|---|---|
| [需求](docs/PRD-v0.3.md) | 用户目标、范围与保留的需求编号 |
| [能力](docs/FEATURES.md) | 当前实现、验证边界、未来能力 |
| [路线图](docs/AGENT-ROADMAP.md) | 唯一执行真源；U5最小回流本地离线完成，U4现场效用无法判读；当前停止，不进入U6 |
| [AGENTS](AGENTS.md) | 工程与协作约束 |
| [架构](docs/architecture.md)、[ADR](docs/adr/) | 当前结构与技术决策记录 |
| [前端规范](docs/design/FRONTEND-SPEC.md) | 视觉与交互验收 |
| [审查标准](docs/archive/CODE-REVIEW.md)、[devlog](docs/devlog.md) | 方法与历史验证记录 |

研究和 [MCP/多智能体提案](docs/MCP-AND-MULTIAGENT-PROPOSAL.md) 是证据与建议，不是已批准任务。历史重构文档及 archive 不用于领取新任务。

## 本地安装与离线检查

使用源码 checkout、项目虚拟环境与项目 Node 依赖。当前建议 **Python 3.11+**：pyproject 仍声明 >=3.10，但生产 TaskGroup 需要 3.11，待修复统一。独立 wheel 与全新环境安装尚未在本次验证，不承诺复制构建产物即可部署。

PowerShell，项目根目录：

```powershell
py -3.11 -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"
npm --prefix frontend ci --cache .npm-cache
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe -m ruff check .
./.venv/Scripts/python.exe -m ruff format --check --extend-exclude docs/archive .
./.venv/Scripts/python.exe -m lighttrail.smoke
./.venv/Scripts/lint-imports.exe
./.venv/Scripts/python.exe -m evals.runner --level L2
npm --prefix frontend run gen:api
npm --prefix frontend run build
```

归档按字节保留旧稿，包含已知不符合 Ruff 的 Markdown Python 代码围栏，所以有效文件格式检查明确排除 docs/archive。离线检查无需真实 Key；L2 是合成夹具，0 LLM。不要全局安装依赖。

## 启动与使用

真实文本/照片分析前，根据 [.env.example](.env.example) 配置本地 .env；不要覆盖已有配置或把 Key 提交入库。模型经过 ModelRouter，默认配置面向 ECNU OpenAI 兼容接口；能力及计价仍需匹配。LLM_CONCURRENCY 默认 4，学校平台实际联调建议配置为 1；真实调用需要明确授权。

两个 PowerShell 终端：

```powershell
# 终端一：API（仅回环地址）
./.venv/Scripts/python.exe -m uvicorn lighttrail.api.app:app --host 127.0.0.1 --port 8000
# 终端二：前端（开发代理连接 API）
npm --prefix frontend run dev -- --host 127.0.0.1
```

按 Vite 输出地址打开：M1 填器材与收藏机位 → D2 选题材/日期/机位生成计划 → 按需导出/导入冻结 JSON。D1/D3 的建议目前需要人工核对关键数据，D4 发送照片前阅读下方数据流。六页路由及前端检查见 [frontend README](frontend/README.md)。CLI 入口为 python -m lighttrail.cli，自由对话和 --pipeline 模式保留。

## 数据、身份与服务边界

默认数据目录 data/，可用 LIGHTTRAIL_DATA_DIR 配置。用户档案/事件/语义位于 users/_local/memory/，会话保存在 sessions/；浏览器保存部分 ID 和最近卡片，**尚不能可靠恢复所有结果**。

本地存储不等于不外传：模型请求可能包含档案与相关事件；照片分析发送图像和相关 EXIF；天气查询和主动加载地图会发送位置。服务端上传临时照片在分析清理阶段删除，不代表接收方不保留数据。默认分享 JSON 移除结构化经纬度，名称/来源等自填文本需自行核对。

当前默认 _local、无公网身份认证；Host/实际客户端必须在本机，写请求仅允许明确本地来源，本机无 Origin 脚本保留。CORS 不代替写校验；规则、照片单文件授权及实际删除目录见 [数据边界](docs/DATA-BOUNDARIES.md)。仅用于受控本机开发；托管多用户、第三方费用/许可、个人数据联调与公开发布未获本次授权。Vite 开发代理与 build 成功不等于生产部署完成。

## 工程结构

Python 核心位于 src/lighttrail/：contracts 定义契约，domain/tools 提供规则，application/orchestrator 编排用例，runtime 承载 Agent，adapters 连接服务，memory/infra 提供存储与观测，composition 是装配根。frontend 为 React/Vite，tests 与 evals 为离线回归及语义验证。详细依赖和已知缺口见架构文档。
