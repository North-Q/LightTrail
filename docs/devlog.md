# LightTrail 开发日志

> 按任务单元记录进度与阻塞，供后续接手者审计。格式：任务编号 / 时间 / commit hash / 测试数量。

## 2026-09-07


### 收尾：TODO 状态同步 + devlog 总结 — 已提交

- TODO.md：阶段一/二/三全部勾选完成（E6/E7/E8 待办保留）。
- docs/devlog.md：整理条目顺序（E1→E2→E3→E4→E5）并追加收尾总结（commit hash 清单 /
  剩余任务 / 遗留问题 / 平台中立性审计）。
- 最终验证：pytest 145 全绿；ruff 0 告警；smoke 19 项通过；仅 commit 未 push。


### 平台中立性重构（ADR-002）— 已提交

- **改动**：
  - `llm/client.py`：模块级 `_SERIAL_LOCK` 改为实例级、可配置的 `serial_llm` 开关，锁只包住单次 API 往返，重试在锁外；
  - `llm/router.py`：新增可注入 `capability_matrix`，路由只认能力声明（tools/vision/deep），移除「深推理与工具互斥」的平台假设（支持单模型全能），非法能力名/无法满足时抛 `RouterError`；
  - `config.py`：环境变量通用前缀 `LLM_*`（LLM_API_KEY / LLM_BASE_URL / LLM_MODEL / LLM_MODEL_REASON / LLM_SERIAL_LLM），保留 `ECNU_*` 兼容别名，`LLM_` 优先；
  - `cli.py` / `agent/loop.py`：适配串行开关注入，不感知并发策略；
  - 文档：新增 ADR-002；architecture / DEVELOPMENT-ROADMAP / PRD / DESIGN-OVERVIEW / AGENTS.md / .env.example 改为平台中立表述；
  - 测试：新增 `tests/test_client_serial.py`（串行/并发峰值断言 + 重试解耦），`tests/test_router.py` 扩展自定义矩阵与单模型全能用例。
- **遗留修复（本次一并处理）**：文档（AGENTS.md / architecture / DEVELOPMENT-ROADMAP / PRD）中残留的 `LIGHTTRAIL_SERIAL_LLM` 与 ADR-002 的 `LLM_SERIAL_LLM` 命名不一致（会导致按文档配置失效），已统一为 `LLM_SERIAL_LLM`；config.py / client.py / test_router.py / .env.example 补齐末尾换行。
- **测试数量**：43 全绿（含新增并发策略 + 能力矩阵用例）。

### 前置工程遗留清理 — 已提交

- **smoke.py 断言修正（名实不符）**：schema 计数断言 `== 2` 过期为 12 工具，改为 `>= 2`；导入改为全量注册（astronomy/basic/exposure/site_match/weather）；补充星空/天气/机位匹配工具族成员校验。离线冒烟 19 项检查全通过。
- **核对并确认已完成的遗留项**：`cli.py` 全量注册 12 工具、`.gitignore` 已排除 `data/`、`_add_astral.py` 已删除（从未入库）、ruff 无告警。
- **TODO.md**：工程遗留章节更新为已清理状态。
- **测试数量**：pytest 43 全绿；ruff 0 告警；smoke 19 项通过。

### E1-2 ContextBuilder 五层分段组装 — 已提交

- **context.py**：`ContextBuilder` 落地架构 v2.0 §2.7 五层结构（① 角色与使命 → ② 行为准则 → ③ 可用工具 → ④ 用户档案+语义记忆 → ⑤ 会话轨迹摘要），按变化频率升序编排；
  - 原 `DEFAULT_SYSTEM_PROMPT` 拆为 `DEFAULT_ROLE_PROMPT` / `DEFAULT_CONDUCT_PROMPT` 两段静态层（兼容导出保留）；
  - ③ 工具层半静态缓存：按注册表工具名集合自动失效重建，单条说明超 160 字符截断；
  - ④⑤ 动态层经注入器（Callable）接入，未配置时整段省略；
  - 每层独立 token 预算（超限截断标注）与版本号（`layer:<层名>@<版本>`）；版本注释头只含静态层版本，保证静态前缀（头+①②③）字节不变、服务 prompt 缓存命中；
  - 主入口 `to_openai_messages(history)`；旧 `build()` 保留为兼容薄封装。
- **loop.py / core.py 接线**：`ReActLoop.run` 默认走分层组装（system_prompt 参数改为兼容项）；`Agent` 注入 registry 与静态层常量，`system_prompt` 参数语义改为覆盖第①层。
- **测试**：新增 `tests/test_context.py` 14 用例（分层顺序 / 静态前缀字节稳定 / 工具层缓存刷新 / 预算截断 / 版本号 / Agent 集成）。pytest 57 全绿；ruff 0 告警；smoke 19 项通过。

### E2-1 TraceRecorder + 事件订阅接口 — 已提交

- **新增 `infra/trace.py`**：TraceRecorder 被动记录三类事件（LLM 调用 / 工具调用 / 管线步骤），
  对外提供订阅接口（`subscribe`，E7-4 SSE 桥接预留）、`to_prompt_section()`（精简轨迹注入文本，
  ①② 序号 + 结果摘要）、`to_report() -> TraceReport`（LLM 调用概览 + 工具调用链 + 来源标注 sources + 置信度）；
  - 置信度规则化初版（不让模型自评）：天文/纯计算 → high、天气等网络数据 → medium、未知 → low（E2-2 细化为按字段/时效）；
  - 参数/结果截断（200/300 字符）、data_source 自动提取（数据来源/来源/data_source 键）、事件订阅同步派发（异常不影响主链路）、线程安全（锁内快照）；
  - `NullTrace` 关闭态零开销，Agent/注册表缺省使用，行为与未接入一致。
- **接线**：`ToolRegistry.__init__(recorder=...)` + `dispatch(..., recorder=...)` 写入 tool 事件（含耗时）；
  `ReActLoop` 每轮 chat 记录 llm 事件、工具分发传 recorder；`Agent(recorder=...)` 透传。
- **测试**：新增 `tests/test_trace.py` 12 用例。pytest 69 全绿；ruff 0 告警；smoke 19 项通过。

### E2-2 trace 注入 prompt 第⑤层 + TraceReport — 已提交

- **轨迹注入**：`Agent` 把 recorder 接入 ContextBuilder 第⑤层（trace_provider），
  每轮组装的 system 自动携带已发生轨迹（LLM 事件不占序列号，只对工具/步骤计数）——验证第二轮 system 含轨迹文本。
- **run_with_trace()**：新方法返回 `(文本, 本轮 TraceReport)`；报告按 `cursor()` 切片，
  只含本次调用事件；旧 `run()` 保持纯文本兼容（内部委托 run_with_trace）。
- **置信度规则表**：独立 `infra/confidence.py`（E2-2 第三步）——确定性（天文/计算）→ high、
  天气预报按时效（覆盖当日 → high，跨天 → medium，无条目 → medium）、启发式组合
  （火烧云评分/机位匹配）→ medium、未知工具按数据来源 → medium/low。TraceRecorder 改为调用规则表。
- **sources 三元组**：`[{tool, field, confidence}]`——SourceRef/ToolCallRef 增加 `field`，
  按工具主字段映射（sun_times→太阳时刻、weather_forecast→每日预报…），未知工具取结果首个业务键。
- **测试**：test_trace.py 扩展（注入验证 / run_with_trace 切片 / field 标注）+ 新增
  tests/test_confidence.py 6 用例。pytest 80 全绿；ruff 0 告警；smoke 19 项通过。

### E3-1 用户档案记忆（常驻注入）— 已提交

- **新增 memory 包**：`UserProfile`（load / save / update / to_prompt_section ≤300 字）、
  `MemoryManager` 门面（`build_injections(intent) -> list[MemoryBlock]`，E3-1 先只有 profile 块）、
  `data/profile.example.json` 模板（camera_body / lenses / preferences / common_locations / skill_level）。
- **ContextBuilder 第④层接入**：`Agent(memory=...)` 经 profile_provider 注入档案段；无档案时注入空段（行为不变）。
- **config**：`Settings.data_dir`（`LIGHTTRAIL_DATA_DIR`，默认 data/），cli 接 MemoryManager。
- **测试**：新增 tests/test_memory.py 8 用例（读写闭环 / 无档案空注入 / 300 字截断 / 未知字段隔离 / Agent 集成）。
  pytest 88 全绿；ruff 0 告警；smoke 19 项通过。

### E3-2 事件记忆（SQLite 按需检索 + search_memory 工具）— 已提交

- **新增 memory/events.py**：`EventStore`（data/events.db）——add_event / search_events
  （关键词+地点 LIKE、题材精确，参数化防注入，时间倒序）/ occurrence_counts 地点聚合 /
  to_prompt_section 注入文本；事件字段含 **coordinates（精确坐标）与 weather_snapshot
  （天气/天象快照：云量/火烧云评分/月相…）**，是 D2.3-07 复拍提醒的数据前提。
- **MemoryManager**：`retrieve_events(intent)` 地点/题材规则命中才检索 top-k（不常驻），
  `build_injections` 在档案块后追加 events 块；`add_event` 门面；Agent.run 携带意图文本注入。
- **新增 tools/memory_tool.py**：注册 `search_memory`（第 13 个工具）——中文结构字段
  （坐标/快照/器材），ReAct 主动检索入口；默认存储走 settings.data_dir，测试可注入替换。
- **测试**：新增 tests/test_events.py 12 用例（增查闭环 / 注入安全 / 聚合 / 注入文本 /
  意图检索 / 工具返回）。pytest 100 全绿；ruff 0 告警；smoke 19 项通过。


---

### E3-3 语义记忆（精选注入 + double-confirm 写入）— 已提交

- **新增 memory/semantic.py**：`SemanticStore`（data/semantic.json）——`match(intent)`
  按关键词规则命中注入最多 2 条；写入走 **double-confirm**（staged_add 候选池 → confirm/reject
  显式裁决，未确认不计入命中、不落盘；重复确认/空内容防护），防污染；
- **MemoryManager**：`build_injections` 注入顺序 profile → semantic → events；
  门面方法 semantic_propose / semantic_confirm / semantic_reject（E6-3 自动提炼挂载点）；
- **模板**：data/semantic.example.json（结论型经验样例，E6-3 提炼结果格式对齐）。
- **测试**：test_memory.py 新增 6 个语义用例（命中/空库/双确认/否决/注入上限/块顺序）。
  pytest 106 全绿；ruff 0 告警；smoke 19 项通过。

### E4-1 ModelRouter 能力矩阵（收尾）— 已提交

- **能力矩阵可注入已在前序平台中立性重构（ADR-002）中落地，本任务不重做**：
  - 新增 `RouteIntent` 意图声明枚举（DEFAULT/TOOLS/VISION/DEEP_REASONING），
    调用方按能力声明路由，不点名模型品牌；
  - `ReActLoop` 对话路径接路由：`model` 显式指定优先，否则
    `RouteIntent.TOOLS.resolve(router)` → 默认工具模型（ecnu-plus）——验收「loop 实测走 plus」通过；
  - `Agent` 把 router 透传给 loop。
- **平台中立性自查**：未回退品牌硬编码；能力声明、矩阵注入、LLM_ 前缀均沿用 ADR-002；
  单模型全能（tools+deep 同模型）由矩阵表达，不预设互斥。
- **测试**：test_router.py 补 RouteIntent 2 用例 + test_agent.py 补 loop 路由 1 用例。
  pytest 109 全绿；ruff 0 告警。

### E4-2 QuotaLedger 配额账本 — 已提交

- **新增 infra/quota.py**：`QuotaLedger`——`record(model, in, out, cached_in)` 按计价表折算 credits、
  `estimate(CallPlan)` 管线成本预估、`check(estimate)` 三窗口放行判定（5h / 日 / 30 天滚动）、
  `usage()` 水位快照、`degrade(intent, router)` 降级建议文本（随 TraceReport.degradation 输出）。
- **平台中立性（ADR-002）**：计价表（`Pricing`）与降级链（`degrade_map`，按**能力**表达如 deep→tools+thinking）
  全部可注入，默认值对齐 ECNU 官方计价；目标模型名由 ModelRouter 矩阵解析，**不硬编码 ecnu-max → ecnu-plus**。
- **接线**：`ChatClient(quota=)` 成功响应后按 usage 自动记账（含缓存命中价）；
  `Settings.quota_warn_threshold`（LIGHTTRAIL_QUOTA_WARN_THRESHOLD，默认 0.9）；cli 注入账本。
- **测试**：新增 tests/test_quota.py 13 用例（记账/预估/窗口/放行/降级可注入/client 集成/参数校验）。
  pytest 122 全绿；ruff 0 告警；smoke 19 项通过。

### E4-3 reason 通道（深推理，thinking 开启）— 已提交

- **Agent.reason 增强**：`reason(prompt, *, system, model, reasoning_effort, temperature=0.3)`——
  模型走 `RouteIntent.DEEP_REASONING` 路由（默认矩阵 → 深推理模型，**不写死模型名**）、
  `tools=None`、`thinking={"type": "enabled"}`、`reasoning_effort` 透传（平台不支持时不计入 payload）；
- **ChatClient**：`chat()` 新增 `thinking` / `reasoning_effort` 可选透传参数（None 不携带，
  保持平台中立）；`_to_message_dict` 提取 reasoning_content/thinking 摘要（≤2000 字符）；
- **推理可见（M2-04）**：reason 调用记 LLM 事件，thinking 摘要以 reason_thinking 步骤入 TraceReport；
- **测试**：test_reason.py 重写为 5 用例（深推理路由/无工具/thinking/effort 透传/thinking 入报告）。
  pytest 126 全绿；ruff 0 告警；smoke 19 项通过。

### E5-1 Orchestrator 四管线 + PipelineContext — 已提交

- **orchestrator 包**：`context.py`（PipelineContext：intent/data/scores/card）、
  `pipelines.py`（四管线：灵感 / 规划 / 临场 / 复盘骨架 + PipelineEnv 运行环境 +
  代码化评分 + 题材→采集步骤映射）、`orchestrator.py`（意图理解默认模型强约束 JSON →
  管线调度 → 失败降级 ReAct；dispatch 可注入 Fake 数据源）。
- **数据采集直调**（非 ReAct 轮次），每步落 TraceRecorder；评分确定性规则（不让模型自评）；
  综合走 reason 深推理通道（RouteIntent，不写死模型名）。
- **cli --pipeline**：「一句话出方案」入口（编排层与 CLI 打通）。
- **测试**：tests/test_orchestrator.py 9 用例（管线注册/模式映射/灵感管线走通落 trace/
  评分/复盘骨架/plan 端到端/降级 ReAct/卡片上下文携带/渲染完整性）。Fake 数据源 + FakeChatClient，不触网。
  pytest 141 全绿；ruff 0 告警。

### E5-2 结构化输出契约（pydantic + 自愈校验）— 已提交

> **提交顺序说明**：E5-2 的 schemas（Intent/DecisionCard）被 E5-1 四条管线
> 作为前置契约依赖，故契约层先行落地提交，随后提交 E5-1 编排（见下一条）。

- **orchestrator/schemas.py**：`Intent`（subject_type/location/time_hint/mode）、
  `DecisionCard`（conclusion/evidence/confidence 必填——结构性保证 M2 不落空；
  time_window/locations/params/alternatives/degraded 可选）+ `Source`/`ParamSuggestion`/
  `LocationSuggestion`；可作 E7 FastAPI 请求/响应模型（白拿 OpenAPI）。
- **infra/validation.py**：`parse_with_retry(schema, chat, prompt, max_retries=2)`——
  LLM 输出 → 剥 markdown 围栏/截取 JSON → pydantic 校验 → 失败把错误回传模型自愈
  （≤2 次）→ 仍失败抛 `SchemaError`（管线捕获降级 ReAct）。
- **测试**：新增 tests/test_validation.py 6 用例（一次通过/围栏剥离/自愈重试/耗尽抛错/
  必要字段强制）。

### E5-3 一句话出方案闭环（端到端）— 已提交

- **端到端串联**：`Orchestrator.plan(user_request)`——一句话 → 意图（默认模型强约束 JSON）
  → 数据采集（直调工具）→ 代码评分 → reason 深推理综合 → DecisionCard → 中文渲染；
  追问「参数激进一点」经 `_fallback` 转入 ReAct 自由对话，且把最近卡片结论作为
  背景上下文一并带入（追问可在此基础上调整）。
- **cli --pipeline**：「python -m lighttrail.cli --pipeline 这周末想去拍银河」输出结构化
  方案（有 Key 时真实执行；无 Key 提示配置）。
- **黄金场景验证（E8 黄金用例集雏形）**：tests/test_pipeline_e2e.py——银河灵感、
  火烧云临场、黄金用例可解析性、CLI 入口（Fake 编排器）4 用例，全部 Fake 数据源 +
  FakeChatClient，不触网、不依赖真实 API Key。
- **测试数量**：pytest 145 全绿；ruff 0 告警；smoke 19 项通过。

### 真实联调修复（reason thinking 配置化 + Open-Meteo 400 + 评分键对齐）

### ecnu-max think 联调修复（extra_body 兼容 + 默认开启）

### CLI 管线进度输出（trace 订阅 → stderr 进度）— 已提交

> 真实 `--pipeline` 联调时「一条 200 后长时间无输出」——意图解析成功但后续
> 天气采集 / ecnu-max 思考模式均无可视反馈。复用 E2-1 预留的 recorder.subscribe，
> CLI 把 trace 事件打印为 stderr 进度（[意图]/[采集]/[工具]/[综合]/[管线]），
> 不污染 stdout 结果，等待期可见可诊断（定位卡在采集还是深推理）。

— 已提交

> **背景**：上一条修复把 reason thinking 默认关闭，但 ECNU 官方文档明确 ecnu-max
> **支持** `thinking={"type": "enabled"}` + `reasoning_effort`（参数名并无问题）。
> 实测 TypeError 的真正根因是 **openai SDK 版本**：3.1.0 的 `Completions.create()`
> 签名没有 thinking 命名参数 → 未知 kwarg 直接拒绝。

- **ChatClient 运行时探测 SDK 能力**：`_NATIVE_REASON_PARAMS` 检查 create() 是否原生支持
  thinking/reasoning_effort——原生支持走命名参数（规范路径）；否则（如 openai 3.1）经
  **`extra_body`** 携带扩展参数（OpenAI 兼容网关从请求体读取该字段，与命名参数等价）。
  普通调用无扩展参数时零污染。这补全了 ADR-002「并发/能力适配收敛在适配层」的一环。
- **默认开启**：`LLM_REASON_THINKING` 默认 **true**（对齐 ECNU 官方文档，与「默认串行适配
  ECNU」同一叙事）；不支持思考的平台在 .env 关闭即可，业务代码零改动。
- **测试**：test_client_serial.py 新增 3 用例——extra_body 路径 / 原生命名参数路径 /
  无参数零污染（monkeypatch 探测标志强制两条路径）。pytest 149 全绿；ruff 0 告警。
- **验证方式**：未用真实 Key 联调（遵守约定）；extra_body 为 openai SDK 标准通道 + ECNU
  网关按文档读取 thinking 字段，组合可信，待小北本机重跑确认。

— 已提交

> **背景**：小北在真实 API 环境执行 `--pipeline` 与自由对话，暴露三处问题。其中
> thinking 硬编码属于**平台中立性修复**（E4-3 把 ECNU 不支持的扩展参数写死在了 reason 通道）。

- **【平台中立性修复】reason 通道 thinking 配置化**：`Agent.reason` 原先总是发送
  `thinking={"type": "enabled"}` 与 `reasoning_effort`，真实 OpenAI 兼容接口（ECNU 实测）不支持 →
  SDK `TypeError` → 管线必挂。现改为 `Settings.reason_thinking`（`LLM_REASON_THINKING`，默认 false）
  控制，关闭时 reason 与普通调用同构（不带扩展参数），通用接口开箱即用；支持的平台在 .env 开启。
- **Open-Meteo 400 修复**：上游 hourly 变量 `cloud_cover_medium` 已失效（实测 400 且报变量损坏），
  从 weather_forecast / sunset_glow_score 两处请求中移除；`_fetch_json` 对 4xx 不再盲目重试 2 次，
  立即抛 `WeatherError` 并携带服务端 reason（模型/用户可见可读原因）。
- **评分键对齐**：pipeline 代码化评分读取 `"评分"`，真实工具返回键为 `"评分（0-100）"`（月相为
  `月相名称`/`月光影响建议`）→ 评分从不进入 ctx.scores。改为按真实键提取并前缀匹配；测试假数据
  同步为真实键名防回归。
- **测试**：test_reason.py 改为「默认不带扩展参数 + 配置开启时携带」；test_orchestrator / e2e 假数据
  键名与真实工具对齐。pytest 146 全绿；ruff 0 告警。

## 收尾总结（2026-09-07，阶段三完成）

### 已完成任务（commit hash 清单）

| 任务 | commit | 测试 |
|---|---|---|
| 平台中立性重构（ADR-002） | a7924c7 | 43 |
| 前置工程遗留清理 | 01d2d07 | 43 |
| E1-2 ContextBuilder 五层组装 | 910f521 | 57 |
| E2-1 TraceRecorder + 订阅 | e331535 | 69 |
| E2-2 trace 注入 + TraceReport | a7af5ec | 80 |
| E3-1 用户档案 | f0948f2 | 88 |
| E3-2 事件记忆（SQLite + search_memory） | 3045ec6 | 100 |
| E3-3 语义记忆（double-confirm） | a32a874 | 106 |
| E4-1 ModelRouter 能力矩阵收尾 | 490c369 | 109 |
| E4-2 QuotaLedger 配额账本 | 5b5fd5f | 122 |
| E4-3 reason 深推理通道 | c705749 | 126 |
| E5-2 结构化输出契约 | 26ca6e3 | 141 |
| E5-1 Orchestrator 四管线 | ae6b38c | 141 |
| E5-3 一句话出方案闭环 | 27035d1 | 145 |

**最终状态**：pytest **145 全绿**（相对基线 43 新增 102 用例）、ruff 0 告警、smoke 19 项通过；
仅 commit 未 push（推送需小北确认）。

### 剩余任务（下一接手者）

- **E6（多模态与差异化）**：E6-1 照片分析智能工具（Pillow/exifread、深度=1 红线、schema 输出）、
  E6-2 照片反推方案（reverse_engineer_photo）、E6-3 语义记忆自动提炼（semantic.extract_from_events）。
- **E7（Web 服务层）**：E7-1 async ChatClient（并发边界由 serial_llm 驱动）、E7-2 SessionManager、
  E7-3 FastAPI+SSE、E7-4 trace→SSE 桥接（TraceRecorder.subscribe 已预留）、E7-5 SPA。
- **E8（评估体系）**：黄金用例集雏形已在 tests/test_pipeline_e2e.py（GOLDEN_CASES），扩展为 evals/。

### 遗留问题 / 注意事项

1. **tests/ 目录 ACL**：本沙箱会话下 tests/ 无 CodexSandboxUsers 写权限，测试文件更新需
   「temp 生成 → escalated copy」；`pytest tmp_path` 亦不可用（pytest-of-North 目录不可扫描），
   新测试请用 tempfile.mkdtemp 自建房（见 test_memory.py 的 memory_dir fixture）。
2. **TODO.md 同受目录 ACL 限制**，更新需走「temp → escalated copy」流程。
3. **管线数据采集**：E5 管线默认坐标写死上海（31.23,121.47），档案常去机位尚无精确坐标字段；
   E6/E7 阶段可在记忆层补「机位坐标」字段后按档案精确定位。意图 time_hint 的「周末」近似为明日。
4. **QuotaLedger 默认计价表**：对齐 ECNU 官方计价，换 API 须注入新 pricing（勿改默认表）。
5. **review 管线为骨架**：输出骨架卡片，E6-1 照片分析接入后填充。

### 平台中立性状态（ADR-002 审计）

- **已收敛**：并发策略可配置（LLM_SERIAL_LLM）、能力矩阵可注入（ModelRouter capability_matrix）、
  配置 LLM_ 前缀、reason/意图解析全部走 RouteIntent（不写死模型品牌）；E5 编排层零 ECNU 品牌判断。
- **残留（历史默认值，合规）**：`ecnu-plus`/`ecnu-max` 仅出现在 config.py DEFAULT_*、quota.py
  默认计价表键、.env.example 注释、docs/adr 历史叙述中——符合 ADR-002「模型名只出现在 config 默认值/.env/能力矩阵」。
- **本次会话新增修复**：文档中 `LIGHTTRAIL_SERIAL_LLM` 命名残留统一为 `LLM_SERIAL_LLM`（a7924c7）。

## 2026-09-07（晚）· 全面检查与文档同步 — 软件开发团队

> 齐活林（主理人）组织全面盘点 Codex 本轮 E1–E5 交付（20 commits），架构师高见远出后续路线，主理人执行文档同步。

- **验证**：pytest 149 全绿 / ruff 0 告警 / smoke 19 项 / 13 工具注册实测 / 工作区干净（20 commits 待 push）。
- **后续路线（v2.2）**：E6 多模态（含新增 E6-0 真实联调基线 / E6-4 复盘管线填充 / E6-5 收口）→ E7 Web（E7-0 联调开场，SPA 3+3 页两迭代）→ E8 评估（黄金用例改各阶段增量交付）→ 开源发布 → **新增阶段八 E9 主动提醒被动 MVP**（复拍提醒 + 就近推荐；定时推送延后）。
- **文档同步**：README（状态勾选/目录结构/13 工具/149 测试）、AGENTS.md（当前阶段/目录/串行策略 ADR-002 化）、DEVELOPMENT-ROADMAP v2.2（§1.1 现状/PRD 矩阵全勾/E9 阶段）、architecture v2.0.1（基线 + ADR-002 补注）、TODO 同步、PRD-v0.2.md 重命名为 PRD-v0.3.md（全局引用替换）。
- **遗留处置排期**（交叉引用 §收尾总结遗留问题）：push 待小北确认（并入 E6-0）；坐标写死上海 → E6-3 favorite_spots 根治；review 骨架 → E6-4 填充；.env.example 补配置 → 开源发布 J-1。

## 2026-09-07（深夜）· 文档补漏核查 — Codex 开发会话

> 应小北要求，在 df63d9e 全面同步之后再次核查 docs 未归档文档，发现并修复如下遗漏：

- **docs/codex-longterm-goal.md 换代 v2（E6 起点）**：原文停留在 E5 前基线（目标「做到 E5」、
  roadmap v2.1 / 12 工具 / 43 测试 / f344eb7 / 「平台中立性重构待审阅提交」待办）。重写为
  E6-0~E6-5 主线目标，基线刷新（13 工具 / 149 测试 / roadmap v2.2 / 21+ commits 未 push 待确认）；
  平台中立原则保留并增补第 5 条（扩展参数收敛适配层，见 ADR-003）。
- **新增 ADR-003-reason-extension-params.md**：本次真实联调确立的架构决策正式入库——
  thinking/reasoning_effort 扩展参数 = 配置化（LLM_REASON_THINKING 默认 true，适配 ECNU）
  + SDK 能力探测（原生命名参数 vs extra_body）双路径，业务层只声明开关（符合 ADR-002
  「适配收敛适配层」的延续）。
- **AGENTS.md 一致性修正**：df63d9e 中 E6 子任务顺序写反（E6-3 复盘填充 / E6-4 语义提炼），
  已对齐 roadmap v2.2（E6-3 语义记忆提炼 → E6-4 复盘管线填充）。
- **Roadmap 小残留**：§3.4 任务编号范围 E8-2 → E9-2（并注明 E9 为产品化增量）；J-1 README
  目标「现状 12 工具」过时改为随进度核对（当前 13）。
- **TODO 头**：任务编号范围精确为 E1-1…E9-2（E9-3 定时推送为可选待议，未入路线图）。
- 未动（无需更新）：architecture v2.0.1（df63d9e 已同步基线；E9 无新架构演进）、
  PRD-v0.3（重命名已完成）、devlog 历史条目。

## 2026-09-07（深夜）· E6 阶段启动（长期目标 v2）

> 依据 docs/codex-longterm-goal.md v2（E6 起点）推进阶段四。

### E6-0 真实联调基线验证 + 远程基线 — ✅ 已完成（2026-09-07 深夜，commit 1076e81）

### E6-0 真实联调基线验证（授权后执行）— 已提交修复

> 小北授权（2026-09-07 深夜）后执行 devlog E6-0 清单。**结果：四管线真实 query 全部跑通，
> push 成功（66cb492..1076e81，28 commits），origin/main 与本地一致（rev-parse 相等）。**
> 附带记录：授权前就绪核查、授权后执行清单见下方引用块。

- **四管线真实 query 全部跑通**（ECNU 真实 Key + Open-Meteo）：
  - 灵感「这周末想去拍银河」→ 9/12 首选 / 9/13 云量大否，机位（天荒坪/太子尖/南汇嘴）+ 参数（14-24mm f/2.8 20-25s ISO1600-3200）✓（reason ~3.5min，thinking 摘要入报告）；
  - 规划「周末两天三机位对比」→ 9/12 崇明日落+夜景双保险，逐时云量依据 ✓；
  - 临场「今晚火烧云值得冲吗」→ 评分 43，建议不专程，若在附近守黄金时刻 ✓；
  - 复盘（UI 稿 d2.png 走多模态链路）→ 识别画面计划并给处方 ✓；TraceReport 落点正常。
- **真实链路验证结论**：thinking extra_body 兼容生效（reason_thinking 步骤出现）、
  Open-Meteo 修复生效、评分键对齐生效；未发现结构性架构问题。
- **真实联调修复 1 项**：exifread 对无 EXIF 文件抛 ExifNotFound → 原代码打 warning 噪音；
  改为静默返回空 dict（截图/无 EXIF 是常态），其它读取异常仍 warning。
- **遗留（待小北）**：真实摄影照片 ≥3 张的视觉处方实测——本机暂无摄影样张，已用 UI 稿
  验证链路；请放图到 data/photos/ 后补测（analyze_photo/reverse_engineer_photo）。



> **授权前就绪核查（2026-09-07 深夜，goal continuation）**：
> - git remote 已配置：`origin = https://github.com/North-Q/LightTrail.git`，upstream=origin/main；
> - f344eb7 之后 **26 个 commit 未推送**（E5/E6 全量 + 文档）；
> - .env 已含 API Key（LLM_ 或 ECNU_ 别名），真实联调开箱即可用；
> - data/events.db 已存在（联调会写入事件记忆，无害）。
>
> **授权后执行清单（E6-0）**：
> 1. 灵感/规划/临场/复盘四管线各 ≥1 条真实 query（CLI `--pipeline` 与 `Orchestrator.review`，
>    需 1 张真实照片走 analyze_photo）；
> 2. 真实照片 ≥3 张验证视觉调用 + 视觉单价计入 QuotaLedger 预估；
> 3. TraceReport/进度输出留档（stderr 已输出 [意图]/[采集]/[综合] 阶段）；
> 4. 发现的问题按「真实联调修复」流程修（commit + devlog 标注）；
> 5. `git push` 建远程基线（26 commits），推后核对 origin/main 与本地一致。

### E6-1 照片分析智能工具

### E6-2 照片反推方案（D1.2 图 → 复刻计划）

### E6-3 语义记忆提炼（事件聚合 + double-confirm + favorite_spots 沉淀）

### E6-4 复盘管线填充（review 闭环）— 已提交

- **pipelines.py ReviewPipeline 闭环**：照片（image_path）→ env.photo_analyze（默认绑
  E6-1 analyze_photo，测试可注 Fake）→ EXIF + 评价 + 处方 → **与历史计划对账**（EXIF
  光圈/快门/ISO 与计划 params 宽松数值对比，产出差异行，键名统一避免 E5 评分键错位教训）→
  reason 深推理 → 复盘 DecisionCard；缺图返回 review_missing_image 骨架卡（不触 LLM）。
- **Orchestrator.review / run_review**：`review(image_path, focus, plan_reference)`——计划
  引用支持 DecisionCard JSON（取其 params）；失败自动降级自由对话。
- **测试**：test_pipeline_e2e.py 新增 3 个复盘用例（闭环/对账差异进 prompt/缺图骨架）+
  test_orchestrator 骨架测试更新为缺图语义。pytest 176 全绿；ruff 0 告警；smoke 19 项。

— 已提交

- **events.py**：事件表新增 `outcome`（success/fail）列 + 轻量迁移（旧库自动 ALTER），
  成功率统计的数据前提；add_event/search/to_record 全链路支持。
- **semantic.py**：`extract_from_events(events, min_samples=3, min_success_rate=0.7)`——
  按题材聚合成功率，命中规则产出 `SemanticCandidate`（候选）；`SemanticStore.contains`
  防重复沉淀。
- **manager.py**：
  - `sediment_semantics()`：全量事件 → 规则提炼 → staged_add 进待确认队列（未确认不注入，
    须 semantic_confirm 才写入 semantic.json 参与第④层注入——double-confirm 防污染）；
  - `sediment_favorite_spots(min_count=2)`：高频带坐标机位沉淀进档案 `favorite_spots`
    （新字段，含 名称/纬度/经度/题材）——为 E9-2 就近推荐与管线按档案定位根治「坐标写死上海」。
- **profile.py**：`favorite_spots` 字段（_FIELDS/展示/存取整链），注入段输出「常去机位：名(lat,lng)」。
- **测试**：tests/test_semantic.py 8 用例（outcome/迁移/规则阈值/double-confirm 生效链/防重/
  favorite 沉淀与去重/无坐标跳过）。pytest 173 全绿；ruff 0 告警；smoke 19 项。

— 已提交

- **tools/photo_analysis.py 增 `reverse_engineer_photo`**（第 15 个工具）：从参考图反推
  场景/光向/推断时段/机位特征/后期风格 + 复刻计划；输出走 `PhotoReverseReport` schema
  （schemas.py 新增契约，replication_plan 必填保证「在哪/什么时候/怎么拍」三要素）；
  复用 E6-1 的编码/EXIF/器材/VISION 路由/自愈机制。
- **Orchestrator.reverse_plan(image_path, note, equipment)**：参考图 → 多模态反推 →
  候选日采集（未来 3 天取云量最低日 + sun_times/moon_phase + 档案候选机位）→
  reason 深推理综合 → 复刻计划 DecisionCard → 渲染；失败自动降级自由对话。
- **测试**：tests/test_reverse_plan.py 5 用例（工具消息含 image_url/note/VISION 路由、
  自愈、注册、Orchestrator 端到端、失败降级）。pytest 165 全绿；ruff 0 告警；smoke 19 项。

 — 已提交

- **tools/photo_analysis.py**：注册 `analyze_photo`（第 14 个工具）——EXIF + 画面多模态 →
  构图/曝光/色彩评价 + **结合器材的可执行处方**（D4-04）；
  - 深度=1 红线：内部不调用 registry.dispatch（测试断言调用形式不存在）；
  - 走同一并发边界与配额账本（模块级 ChatClient = settings 串行 + QuotaLedger；set_client 可注入 Fake）；
  - 多模态模型按 `RouteIntent.VISION` 路由（默认矩阵 → plus）；tools=None；
  - 图片控 token：最长边 ≤1024 缩放 + JPEG q88 + base64 data URL（体积 <500KB 验证）；
  - EXIF 经 exifread 提取，tag 尾部完整匹配避免 Model/LensModel 误中；无 EXIF 空 dict 不阻塞；
  - 输出走 **PhotoAnalysisReport schema**（schemas.py 新增契约，E5-2 自愈机制复用，
    首轮外 ≤2 次纠错重试，仍失败抛 PhotoError）；
  - 器材来源：显式 equipment 参数优先，缺省读 data/profile.json 档案。
- **依赖**：Pillow>=10.0、exifread>=3.0（pyproject + requirements 声明，装进项目 .venv）。
- **测试**：tests/test_photo_analysis.py 11 用例（编码/EXIF/多模态消息含 image_url 与 VISION 路由/
  自愈重试/连续失败/器材档案与显式覆盖/注册/红线）。pytest 160 全绿；ruff 0 告警；smoke 19 项。
- **待授权窗口**：真实照片 ≥3 张实测 + 视觉调用单价入 QuotaLedger 预估（并入 E6-0 真实联调）。



- **状态**：未开始。前置为「真实 Key 联调 + push 20+ commits 建远程基线」，均属需小北确认的外部动作。
- **记录**：按长期目标阻塞规则如实记录，跳到下一个不依赖它的任务（E6-1 代码侧 Fake/mock 可先行；
  E6-1 的「真实照片 ≥3 张实测」验收项并入 E6-0 授权窗口执行）。
- **建议**：小北授权后按 roadmap v2.2 E6-0 验收执行（四管线真实 query + trace 留档 + push）。

### E6-5 阶段收口（黄金用例增量 + 文档同步）— 已提交

- **黄金用例增量（E8-1 策略落地）**：`evals/golden/E6-photo-cases.json`——8 条照片分析/
  反推/复盘结构化用例（request + expect 关键词），结构校验测试护航（≥8、id 唯一、字段完整）。
- **smoke 扩展**：照片分析/反推工具注册检查（19 → 21 项）。
- **文档同步**：README（15 工具/176 测试/E6 勾选）、TODO（E6 勾选 + E6-0 阻塞标注）、
  roadmap v2.3（现状/阶段表/规模）、AGENTS（下一步指向 E7，E6-0 授权前置说明）。
- **回归**：pytest 176 全绿；ruff 0 告警；smoke 21 项通过。

### E6 阶段总结（2026-09-07 深夜）

- **交付**：E6-1 照片分析（analyze_photo，深度=1 红线）→ E6-2 反推（reverse_engineer_photo +
  Orchestrator.reverse_plan）→ E6-3 语义提炼（outcome 字段+迁移、sediment_*、favorite_spots
  沉淀根治坐标写死数据前提）→ E6-4 复盘闭环（照片分析 + 计划对账差异报告）→ E6-5 收口。
- **测试**：149 → **176**（+27）；工具 13 → **15**；ruff 0 告警；smoke 21 项。
- **阻塞（如实记录）**：E6-0 真实联调基线 + push 需小北授权——含「真实照片 ≥3 张实测
  视觉调用」「四管线真实 query 留档」；代码侧验收已由 Fake 全量覆盖，授权窗口执行即可。
