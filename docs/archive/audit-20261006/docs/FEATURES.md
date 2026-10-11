# LightTrail（光迹）现有功能介绍

> **版本**：v1.0 ｜ **基线**：2026-10-05 代码实证（devlog 最新至 2026-10-04 T6）
> **文档定位**：基于源码实证盘点当前已实现的功能能力，标注成熟度与代码位置。不替代 [`docs/AGENT-ROADMAP.md`](AGENT-ROADMAP.md)（执行真源）、[`docs/architecture.md`](architecture.md)（架构边界）和 [`docs/PRD-v0.3.md`](PRD-v0.3.md)（需求定义）。完成度与后续任务以路线图为准。

---

## 1. 系统总览

LightTrail 是面向摄影场景的 AI 拍摄决策引擎，沿「灵感 D1 → 规划 D2 → 决策 D3 → 复盘 D4」旅程组织功能，M1 个性化记忆与 M2 可解释性贯穿全程。当前为**本地单用户演示形态**（`_local` 身份，无注册/登录/认证），核心闭环已具备演示骨架。

### 1.1 六层架构

| 层 | 模块 | 职责 | 强约束 |
|---|---|---|---|
| 契约层 | `src/lighttrail/contracts/` | 跨层模型与端口（ToolSpec/ToolContext/RequestContext/Plan/DecisionCard/Intent/SpotPlan/PlanArchive） | 零依赖；契约唯一真源 |
| 领域层 | `src/lighttrail/domain/` | 纯规则（chase 赶场、knowledge 知识库 FTS5/catalog） | 不依赖应用/适配/交互层 |
| 应用层 | `src/lighttrail/application/` | 用例编排（router 规则意图路由、spot_planner 结构化计划、plan_archive 快照） | 不承载 UI 细节与供应商判断 |
| 运行时层 | `src/lighttrail/runtime/` | AgentRuntime（PydanticAI）/ ToolRegistry / ContextBuilder（五层）/ Planner | 只消费契约与领域工具 |
| 适配层 | `src/lighttrail/adapters/` | LLM 客户端 / 配置密钥 / 高德·百度地图 | 实现端口，不含业务决策 |
| 编排层 | `src/lighttrail/orchestrator/` | 四管线 + Orchestrator + PipelineContext | 不反向依赖 adapters |
| 交互层 | `src/lighttrail/api/`、`cli.py`、`frontend/` | FastAPI + SSE + SPA + CLI | 不承载领域规则 |
| 装配根 | `src/lighttrail/composition.py` | 唯一 new 点，构造 ApplicationServices/RequestScope | 唯一允许跨层构造对象 |

`import-linter` 强制上述方向：3 条契约保留，0 条违反（2026-10-04 T6 基线）。

### 1.2 当前可复现基线（2026-10-04）

| 项 | 数值 | 来源 |
|---|---|---|
| pytest | **458 passed** | devlog 2026-10-04 T6 |
| ruff check / format --check | 通过 / 179 files already formatted | devlog T4 |
| 离线 smoke | **21/21** | devlog |
| lint-imports | 3 kept / 0 broken | devlog |
| 前端 OpenAPI 生成 | SHA-256 稳定（连续两次一致） | devlog T6 |
| 前端 build / UI 壳层 | 通过 / 7/7 | devlog |
| L2 语义评估 | 12/12 独立合成 cassette 通过 | R0-7 |
| L3 LLM-as-judge | 保留语义通过数 | R0-7 |

> 上述数字是「回归基线」，不代表产品已完整。C1 赶场为实验能力，E1 CaptureEvent 闭环未接线。

---

## 2. 已实现功能清单（按功能域）

成熟度图例：🟢 生产接线 ｜ 🟡 实验能力 ｜ ⚪ 骨架就绪（契约/端点已通，闭环待补）

### 2.1 D1 灵感域

| 功能 | 能力描述 | 成熟度 | 代码位置 |
|---|---|---|---|
| 一句话出方案 | 自然语言一句话 → 规则意图路由 → 四管线数据采集 → 深推理综合 DecisionCard；失败降级 ReAct 自由对话 | 🟢 | `orchestrator/orchestrator.py:plan`、`application/router.py` |
| 器材自动补全 | 用户未提器材时从当前用户档案读取机身/镜头注入处方 | 🟢 | `tools/photo_analysis.py:_read_gear`、`memory/manager.py:equipment_for` |
| 多源数据综合 | 管线按意图编排天文/天气/机位/记忆工具采集（受 `pipeline_max_steps` 限制） | 🟢 | `orchestrator/pipelines.py`（四管线） |
| 照片反推方案（D1.2） | 多模态识别参考图场景/光向/时段/机位特征/后期风格 → 候选日数据 → 深推理复刻计划卡 | 🟢 | `tools/photo_analysis.py:reverse_engineer_photo`、`orchestrator/orchestrator.py:run_reverse` |
| 反推专用 system prompt | 反推使用独立 `_REVERSE_SYSTEM`，与分析 prompt 分离（R0-4） | 🟢 | `tools/photo_analysis.py:295` |

### 2.2 D2 规划域

| 功能 | 能力描述 | 成熟度 | 代码位置 |
|---|---|---|---|
| 天象方位查询 | 日出日落/晨昏蒙影/蓝调黄金窗口/方位角（astral 库） | 🟢 | `tools/astronomy.py:sun_times`、`sun_position` |
| 月相/月升月落 | 月相名称/月龄/照亮比例/月光影响建议；月升月落时刻与方位 | 🟢 | `tools/astronomy.py:moon_phase`、`moon_events` |
| 银心可见窗口 | 自研 J2000 赤道坐标 + 一阶岁差近似 + 时角转换（精度约 ±2°） | 🟢 | `tools/astronomy.py:galaxy_visibility` |
| 机位 × 天象匹配 | 多机位 × 日期按「题材朝向 × 天象窗口」0-5 分排序 | 🟢 | `tools/site_match.py:match_sites` |
| 收藏机位 CRUD | 新增/编辑/删除/列表，坐标系校验（WGS84 ready / GCJ-02·BD-09 unsupported / missing），首次修改前备份 | 🟢 | `api/routes.py`（`/api/favorite-spots*`）、`memory/manager.py` |
| M1 机位编辑器 | 列表 + 表单 + 删除二次确认 + 不可计算原因标注 | 🟢 | `frontend/src/pages/FavoriteSpotEditor.tsx`、`M1Page.tsx` |
| 结构化 SpotPlan | 按本轮题材/日期/机位 ID 生成到达/准备/拍摄/离场窗口 + 方向 + 焦段 + 器材 + 参数 + 风险 + 证据；缺事实时 `needs_data` | 🟢 | `api/routes.py:create_spot_plan`、`application/spot_planner.py`、`contracts/spot_plan.py` |
| 计划导出/回放 | 版本化 `PlanArchive` 脱敏快照（默认移除结构化经纬度）；URL 仅含题材/日期/机位 ID；冻结回放不访问档案/天气/地图/LLM | 🟢 | `api/routes.py:export_spot_plan_archive/replay_spot_plan_archive`、`application/plan_archive.py` |
| 地图机位层（D2.1-05） | 用户主动加载高德地图，仅已确认 WGS84 机位经服务端转 GCJ-02 落点；标记与列表按 ID 联动；失败保留列表与原因 | 🟡 | `frontend/src/pages/SpotMap.tsx`、`api/routes.py:convert_map_coordinates`、`adapters/maps/js_service.py` |
| 多机位赶场调度（D2.2） | 2-4 机位确定性赶场规划，含 leave_at/arrive_at/latest_leave_at/effective_shooting/route（provider/queried_at/degraded_reason） | 🟡 | `domain/chase.py:ChasePlanner`、`api/routes.py:create_chase_plan` |
| 赶场路线地图层（D2.2-04） | 暂未接线真实道路路线几何；当前手工耗时/直线距离标注为「直线示意」 | ⚪ | 依赖 O1-2/3，前端路线层待补 |

### 2.3 D3 决策域

| 功能 | 能力描述 | 成熟度 | 代码位置 |
|---|---|---|---|
| 等效曝光换算 | 光圈/快门/ISO 三向等效方案（+1 档 = 进光量 ×2） | 🟢 | `tools/exposure.py:equivalent_exposure` |
| 星空 500/NPF 法则 | 500 法则 + NPF（像素间距修正，赤纬修正）；缺像素间距时查知识库 pixel_pitch | 🟢 | `tools/exposure.py:star_shutter_rule`、`StarShutterTool` |
| 长曝光 ND 换算 | 正向（基准+ND→长曝）+ 反推（目标快门→推荐滤镜），支持 ND4~ND1000 | 🟢 | `tools/exposure.py:nd_long_exposure` |
| 火烧云概率评分 | 启发式模型（云量 30-70%/高云占比/能见度/降水/风速加权 0-100 分 + 等级 + 依据 + 置信度） | 🟢 | `tools/weather.py:sunset_glow_score` |
| 临场赌注决策 | 火烧云评分 + 机位距离 + 时间窗口 → 去/等/放弃 + 依据 | 🟢 | `orchestrator/pipelines.py`（live 管线） |

### 2.4 D4 复盘域

| 功能 | 能力描述 | 成熟度 | 代码位置 |
|---|---|---|---|
| 照片上传与多模态分析 | jpg/png ≤10MB 分块读取；EXIF（exifread）+ 画面 → 构图/曝光/色彩评价 + 可执行处方 | 🟢 | `api/routes.py:photos_review`、`tools/photo_analysis.py:analyze_photo` |
| 原图预览与结果对应（D4-05） | 选中照片后、分析前显示该照片及文件名；批量时当前照片与当前结果一一对应；预览只用浏览器 Object URL，换图/离页释放 | 🟢 | `frontend/src/pages/D4Page.tsx` |
| EXIF 读取 | 常用 tag（Make/Model/LensModel/FNumber/ExposureTime/ISO/FocalLength 等）→ 中文键；无 EXIF 静默不阻塞 | 🟢 | `tools/photo_analysis.py:_read_exif` |
| 可执行处方 | 处方须结合当前用户器材落在具体参数（光圈/快门/ISO/焦段/机位/时机） | 🟢 | `tools/photo_analysis.py:_ANALYZE_SYSTEM`、`_build_analysis_prompt` |
| 计划—实际对账 | 历史计划 DecisionCard JSON 的 params 提取后注入复盘上下文 | 🟡 | `orchestrator/orchestrator.py:_extract_plan_params`、`run_review` |
| CaptureEvent 闭环 | 计划/执行/照片/复盘未关联成统一事件 | ⚪ | E1 批次未做 |

### 2.5 M1 个性化记忆

| 功能 | 能力描述 | 成熟度 | 代码位置 |
|---|---|---|---|
| 四层记忆 | 短期（AgentRuntime 内存）/ 档案（JSON）/ 事件（SQLite）/ 语义（JSON） | 🟢 | `runtime/agent.py:_messages`、`memory/profile.py`、`memory/events.py`、`memory/semantic.py` |
| per-user 命名空间隔离 | `data/users/{user_id}/memory/`（profile.json / events.db / semantic.json）；双用户不可串读 | 🟢 | `memory/manager.py:_state`、`contracts/context.py:LOCAL_USER_ID` |
| 并发初始化安全 | 全局字典锁 + 按用户 ID 初始化锁（T2 收敛）；同用户只构造一个 EventStore/SemanticStore | 🟢 | `memory/manager.py:_users_lock`、`_user_state_locks` |
| 档案常驻注入 | 用户档案精简后常驻 system prompt（profile block） | 🟢 | `memory/manager.py:build_injections`、`runtime/context.py` |
| 事件按需检索 | 按关键词/地点/题材模糊匹配，返回含坐标与天气快照的事件列表（search_memory 工具） | 🟢 | `tools/memory_tool.py:SearchMemoryTool`、`memory/manager.py:search_events` |
| 事件确认写入 | 仅 `/api/events` 显式请求才落库；时间戳必须带时区 | 🟢 | `api/routes.py:create_event`、`memory/manager.py:add_event` |
| 语义记忆提炼 | 从事件提炼候选（min_samples/min_success_rate），二次确认后才注入 | 🟡 | `memory/manager.py:sediment_semantics`、`memory/semantic.py:extract_from_events` |
| 收藏机位沉淀 | 高频事件地点（min_count）沉淀到档案 | 🟡 | `memory/manager.py:sediment_favorite_spots` |
| 通勤/常驻画像 | 字段语义未在档案实现 | ⚪ | M1.1-04 待做 |

### 2.6 M2 可解释性

| 功能 | 能力描述 | 成熟度 | 代码位置 |
|---|---|---|---|
| 工具调用轨迹 | TraceRecorder 记录工具/步骤/LLM 事件；SSE 经 TraceBridge 桥接到前端 | 🟢 | `infra/trace.py:TraceRecorder`、`api/events.py:TraceBridge` |
| 建议依据生成 | 工具返回附依据字段（如 NPF 公式、火烧云各分量） | 🟢 | 各工具 `main_field`/`confidence`/`confidence_rule`、`contracts/tool.py:ToolSpec` |
| 来源与置信度标注 | 工具返回附数据来源（如「Open-Meteo（免费）」）；置信度 HIGH/MEDIUM/LOW | 🟢 | `contracts/tool.py:Confidence`、各工具返回 dict |
| 推理摘要 | 深推理通道 thinking 摘要落成步骤事件 | 🟢 | `runtime/agent.py:areason`（ThinkingPart 落 trace） |
| 知识来源标注 | 全局知识命中只记录来源，不把内容复制到 trace | 🟢 | `composition.py:report_knowledge` |

### 2.7 引擎底座

| 功能 | 能力描述 | 成熟度 | 代码位置 |
|---|---|---|---|
| 多轮对话管理 | PydanticAI Agent 维护历史，支持清空/载入 | 🟢 | `runtime/agent.py:AgentRuntime`（`_messages`、`load_messages`、`reset`） |
| 工具调用循环（ReAct） | tool_calls → 执行 → 回传；最大轮数 `DEFAULT_MAX_TOOL_ROUNDS=12`（v0.4 修订） | 🟢 | `runtime/agent.py:arun`、`UsageLimits(request_limit=12)` |
| 声明式工具注册表 | ToolSpec 自描述 + PureTool 适配；新增工具在 `tools/__init__.py` 加一行 | 🟢 | `tools/__init__.py:TOOLS`、`tools/_base.py:PureTool`、`runtime/registry.py:ToolRegistry` |
| 模型路由（能力矩阵） | RouteIntent（DEFAULT/TOOLS/VISION/DEEP_REASONING）→ ModelRouter.resolve；矩阵可注入，不写品牌判断 | 🟢 | `llm/router.py` |
| LLM 并发策略 | 单一配置 `LLM_CONCURRENCY`（默认 4，设 1 即串行）；单 Semaphore 包单次往返，重试在限流器外（ADR-004） | 🟢 | `adapters/llm/client.py`、`config.py` |
| LLM 调用容错 | 429/5xx 指数退避重试（最多 3 次，基础 1s + 抖动）；超时连接 30s/读取 120s | 🟢 | `adapters/llm/client.py` |
| Thinking 模式 | 深推理通道支持 `thinking: {"type":"enabled"}` + `reasoning_effort`（ADR-003） | 🟢 | `runtime/agent.py:areason`、`config.py:reason_thinking` |
| 配额感知路由 | QuotaLedger 拒绝/高水位降级统一在 `Orchestrator._quota_card`（T5 收敛） | 🟢 | `infra/quota.py:QuotaLedger`、`orchestrator/orchestrator.py:_quota_card` |
| 全局知识库（FTS5） | SQLite FTS5 trigram + BM25 中文检索；长问句有限 trigram 扩召回 + 重叠数过滤 | 🟢 | `domain/knowledge/fts.py:FtsKnowledgeProvider` |
| 结构化相机规格表 | 知识库首案 pixel_pitch（S5M2/A7C2/Nikon_FE），星空工具自动查表 | 🟢 | `domain/knowledge/catalog.py`、`tools/exposure.py:StarShutterTool` |
| 规则优先意图路由 | RuleIntentRouter 规则关键词命中优先；未命中走 LLM 路由（置信度阈值 0.6） | 🟢 | `application/router.py:RuleIntentRouter` |
| 受控 Plan-Execute | Planner.make_plan（LLM 产计划，_PlanPayload 校验）→ execute（逐步 registry.dispatch，配额检查，失败降级）；max_steps=8 | 🟢 | `runtime/planner.py:Planner` |
| 超工具集护栏 | 超出摄影能力的请求显式告知不可代办，不静默处理 | 🟢 | `orchestrator/orchestrator.py:plan`（unsupported_reply） |
| 对话持久化 | 会话历史按 session_id 落盘 JSON；支持载入恢复 | 🟢 | `api/session.py:SessionManager` |
| 跨会话恢复 | `/api/sessions/{id}` 返回历史 + 最近 trace 回放 | 🟢 | `api/routes.py:get_session` |

### 2.8 服务与前端

| 功能 | 能力描述 | 成熟度 | 代码位置 |
|---|---|---|---|
| FastAPI 薄服务层 | 协议转换 + 会话管理；业务在 Orchestrator/AgentRuntime | 🟢 | `api/routes.py`、`api/app.py` |
| SSE 事件流 | queued → 步骤 → 工具 → 卡片 → done；同步 worker 经 asyncio.to_thread + TraceBridge 桥接 | 🟢 | `api/events.py:TraceBridge`、`routes.py:_stream_response` |
| CLI 入口 | 自由对话 + `--pipeline` 管线模式；trace 进度走 stderr | 🟢 | `cli.py` |
| 六页 SPA | Home / D1 灵感 / D2 规划 / D3 决策 / D4 复盘 / M1 记忆 | 🟢 | `frontend/src/pages/` |
| 地图组件 | 高德 JS API 机位层（用户主动加载） | 🟡 | `frontend/src/pages/SpotMap.tsx` |
| 计划视图 | 结构化时间线/器材/风险/证据渲染 | 🟢 | `frontend/src/pages/SpotPlanView.tsx` |
| 机位编辑器 | CRUD 表单 + 删除二次确认 | 🟢 | `frontend/src/pages/FavoriteSpotEditor.tsx` |
| OpenAPI → TypeScript | `npm run gen:api` 生成契约类型；连续两次 SHA 稳定 | 🟢 | `api/openapi_export.py`、`frontend/` |
| UI 壳层自检 | `npm run check:ui` 六页路由逐一渲染校验 | 🟢 | `frontend/` |

---

## 3. 工具层详表（15 个声明式工具）

| # | 工具名 | 域 | 能力 | 置信度 | 文件 |
|---|---|---|---|---|---|
| 1 | `get_current_time` | 基础 | 当前日期时间（时区偏移） | HIGH | `tools/basic.py` |
| 2 | `equivalent_exposure` | D3 曝光 | 等效曝光三向换算 | HIGH | `tools/exposure.py` |
| 3 | `star_shutter_rule` | D3 曝光 | 500/NPF 法则 + 知识库 pixel_pitch | HIGH | `tools/exposure.py` |
| 4 | `nd_long_exposure` | D3 曝光 | ND 长曝光正反向换算 | HIGH | `tools/exposure.py` |
| 5 | `sun_times` | D2 天文 | 日出日落/晨昏蒙影/蓝调黄金 | HIGH | `tools/astronomy.py` |
| 6 | `sun_position` | D2 天文 | 太阳高度角/方位角 + 场景判断 | HIGH | `tools/astronomy.py` |
| 7 | `moon_phase` | D2 天文 | 月相/月龄/照亮比例/影响建议 | HIGH | `tools/astronomy.py` |
| 8 | `moon_events` | D2 天文 | 月升月落时刻与方位 | HIGH | `tools/astronomy.py` |
| 9 | `galaxy_visibility` | D2 天文 | 银心可见窗口（自研 J2000+岁差） | HIGH | `tools/astronomy.py` |
| 10 | `weather_forecast` | D2 气象 | Open-Meteo 1-7 天逐日摘要 | MEDIUM | `tools/weather.py` |
| 11 | `sunset_glow_score` | D3 火烧云 | 启发式 0-100 评分 + 依据 + 置信度 | MEDIUM | `tools/weather.py` |
| 12 | `match_sites` | D2 机位 | 多机位 × 天象 0-5 分排序 | MEDIUM | `tools/site_match.py` |
| 13 | `search_memory` | M1 记忆 | 按当前用户检索事件（含坐标/天气快照） | LOW | `tools/memory_tool.py` |
| 14 | `analyze_photo` | D4 复盘 | 多模态 + EXIF + 可执行处方 | LOW | `tools/photo_analysis.py` |
| 15 | `reverse_engineer_photo` | D1.2 反推 | 多模态反推 + 复刻计划 | LOW | `tools/photo_analysis.py` |

> 工具元数据（schema/main_field/confidence/capabilities）声明在 `ToolSpec`，注册表统一分发，trace 主字段与置信度入口不再手抄三处（NF-09 热插拔口径）。

---

## 4. API 端点详表（16 个）

| 方法 | 路径 | 功能 | 返回 |
|---|---|---|---|
| POST | `/api/chat` | 自由对话（ReAct） | SSE 事件流 |
| POST | `/api/decide` | 拍摄决策（D1–D3） | SSE 事件流（卡片或文本边界） |
| POST | `/api/photos/review` | 照片复盘（D4） | SSE 事件流至复盘卡 |
| POST | `/api/photos/reverse` | 参考图反推（D1.2） | SSE 事件流至复刻计划卡 |
| GET/PUT | `/api/profile` | 读取/更新用户档案 | ProfilePayload |
| GET/POST | `/api/favorite-spots` | 列出/新增收藏机位 | FavoriteSpotListPayload / FavoriteSpotRecord |
| PUT/DELETE | `/api/favorite-spots/{spot_id}` | 更新/删除机位 | FavoriteSpotRecord / DeletedSpotPayload |
| POST | `/api/spot-plans` | 按已确认机位生成结构化计划 | SpotPlan（含 needs_data） |
| POST | `/api/spot-plans/archive` | 导出脱敏冻结快照 | PlanArchive |
| POST | `/api/spot-plans/replay` | 校验并回放冻结计划 | PlanArchive |
| GET | `/api/map/config` | 读取地图公开配置 | MapConfigPayload |
| GET | `/_AMapService/{path}` | 高德 JS API 服务端代理（仅 v4/map/styles） | Response |
| POST | `/api/map/convert` | 转换已确认 WGS84 机位为 GCJ-02 | MapConvertResult |
| POST | `/api/events` | 确认写入拍摄事件 | EventCreatedPayload |
| POST | `/api/chase-plans` | 多机位赶场规划（C1-1，确定性） | ChasePlanPayload |
| GET | `/api/sessions/{session_id}` | 会话历史与 TraceReport 回放 | SessionResponsePayload |

> 所有响应模型进 OpenAPI components（T6 收敛）；`gen:api` 生成 TypeScript 契约，前后端无手写双份。

---

## 5. 已知接线缺口与限制

以下为代码实证与文档声明交叉核对后的**诚实边界**，不构成再次全量重写的理由：

1. **E1 CaptureEvent 闭环未接线**：计划、执行、照片、复盘未关联成统一事件；当前 D4 复盘与 `/api/events` 是独立入口，照片与事件无稳定引用（`orchestrator/orchestrator.py:run_review`）。
2. **本地单用户**：Web 恒用 `_local` 身份，无注册/登录/认证；`user_id` 命名空间已预留但不构成公网多用户服务（`api/routes.py:get_request_context`）。
3. **CORS 本地开发为 `*`**：公开发布前须收口为白名单（P1-1）。
4. **地图代理无认证**：`/_AMapService` 与 `/api/map/convert` 仅限本机同站点（`_require_local_map_request`），不得公网开放。
5. **赶场为实验能力**：`ChasePlanner` 当前手工耗时 + 直线距离估算，非道路导航；路线图层依赖 O1-2/3 真实路线几何（未接线）。
6. **高德费用/许可未独立核实**：S1-4 仅做一笔合成坐标转换验证，不证明零费用、长期使用授权或公网部署条件。
7. **真实天气/用户机位外发**未在 S1 闸门验收范围内。
8. **照片上传**：画面发往配置的多模态模型；档案摘要与检索记忆可进入模型上下文；数据流与告知规则待 P1 隐私收口。
9. **火烧云评分**为启发式模型，非气象学精确概率；未校准前不展示「命中率 X%」类数字。

---

## 6. 与路线图声明的差异

代码实证与 `docs/AGENT-ROADMAP.md` v1.4 + `docs/architecture.md` v3.2 声明基本一致，以下为值得记录的细微差异：

1. **测试用例数**：路线图基线快照为 444 passed（S1-5），devlog 已推进至 458 passed（T6 增加了 API 响应契约、事件时间校验、配额收敛等回归）。路线图未同步更新此数字，但不影响执行顺序。
2. **T2–T6 技术收口**：devlog 2026-10-03/10-04 记录了 5 个技术债小任务（MemoryManager 并发初始化、照片上传分块限额、Ruff 格式门禁、配额决策唯一入口、API 响应契约与事件时间校验），这些不在 ROADMAP 的批次结构中，属于批次内的稳定性收口，已并入基线。
3. **ReAct 轮数**：PRD 正文写最大 8 轮，附录 A2 v0.4 修订为 12；代码 `DEFAULT_MAX_TOOL_ROUNDS=12`（`runtime/agent.py:37`），以 v0.4 修订为准。
4. **地理编码数据源**：PRD §9.1 写「腾讯地图 P0」，§8.2 写「高德 Web 服务 + 百度降级（实验）」；代码实际接入高德（`adapters/maps.py:AMapRoutingAdapter` + `BaiduRoutingAdapter` + `FallbackTravelTimeProvider`），与 §8.2 一致。

---

## 7. 文档地图

| 想了解 | 看哪里 |
|---|---|
| 项目是什么 | [`README.md`](../README.md) |
| 当前已实现什么（本文） | **本文** |
| 当前架构与边界 | [`docs/architecture.md`](architecture.md) |
| 后续任务与执行顺序 | [`docs/AGENT-ROADMAP.md`](AGENT-ROADMAP.md) |
| 产品需求与范围 | [`docs/PRD-v0.3.md`](PRD-v0.3.md) |
| 已完成工作时间线 | [`docs/devlog.md`](devlog.md) |
| 需求 vs 现状对比 | [`docs/GAP-ANALYSIS.md`](GAP-ANALYSIS.md) |
