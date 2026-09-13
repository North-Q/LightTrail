# TODO · LightTrail（光迹）

> 项目待办 + 灵感收集。更新日期：2026-09-13
> **当前阶段：架构重建期**——任务编号见 `docs/REFACTOR-ROADMAP.md`（B0-1 … B7-6）；方案权威 `docs/architecture-v4-proposal.md`；codex 入口 `docs/codex-longterm-goal.md` v7。
> E1–E8 已完成（旧 E 系列路线图归档至 `docs/archive/DEVELOPMENT-ROADMAP-v2.5.md`）；E9/E10 等未做功能项的处置见 PRD 附录 A2（v0.4 范围修订）。

## 待办（当前阶段：架构重构 B0–B7）

### B0 止血护栏 ✅ 已完成（2026-09-13，停闸门 0）

- [x] B0-1 修复 SessionManager 并发三 bug（死锁 / 共享可变 / 锁外写）+ 淘汰与并发写测试
- [x] B0-2 record_llm 接通 tokens 记账（TraceReport 不再恒 None）
- [x] B0-3 四处假注释清理（client 缓存 / trace tokens / pipelines 时效 / orchestrator 档案定位）
- [x] B0-4 B0 收口（回归 + 重构前基线：pytest 230 / ruff 0 / 冒烟 21 / CLI+Web 真实实跑 / tag refactor-baseline）

### B1 契约层 + 配置 ✅ 已完成（2026-09-13，停闸门 1）

- [x] B1-1 contracts/ 骨架（ToolSpec / ToolContext / RequestContext）
- [x] B1-2 契约模型迁移（Intent / DecisionCard / TraceEvent / Plan + shim）
- [x] B1-3 用户体系预留接口（UserConfigProvider / KeyVault / LLMConfig，Key 掩码）
- [x] B1-4 剩余 Protocol（MemoryStore / KnowledgeProvider / DataSource / TraceSink）
- [x] B1-5 config.py 换 pydantic-settings + 护栏配置项 + import-linter 契约门禁

### B2 引擎重写 🔄 进行中（B2-1 ~ B2-6 已完成；B2-7 部分完成，2026-09-14）

- [x] B2-1 声明式 ToolSpec 改造：basic + exposure
- [x] B2-2 声明式 ToolSpec 改造：astronomy + weather（+ confidence_rule 动态置信度）
- [x] B2-3 声明式 ToolSpec 改造：site_match / memory_tool / photo_analysis + TOOLS 收集点
- [x] B2-4 ToolRegistry 迁入 runtime/ + composition root（agent/tools 转 shim；适配器契约开启）
- [x] B2-5 PydanticAI 自定义 Model 桥（ADR-002/003 语义不丢）
- [x] B2-6 AgentRuntime 接入 + ContextBuilder 迁入 runtime（能力叙述自动生成）
- [~] B2-7 热插拔 ✅ + 架构禁止边 ✅ + CLI/Web 自由对话切 runtime ✅；★ 下一步：编排器/四管线切 runtime
      → TestModel 替换 FakeChatClient → shim 清理 → B2 收口

### B3 适配层 + 并发

- [ ] B3-1 async-first LLMProvider（单 Semaphore，LLM_CONCURRENCY 默认 4，删三套并发机制）
- [ ] B3-2 tenacity 统一重试 + httpx 数据源
- [ ] B3-3 管线并行取数（asyncio.TaskGroup，采集延迟降 ≥40%）
- [ ] B3-4 TraceSink Protocol + OTel GenAI 命名 + 事件载荷白名单
- [ ] B3-5 B3 收口（import-linter 全开 + QuotaLedger 加锁 + 死锁回归）

### B4 契约单一真源 + 前端重接（可与 B2/B3 并行）

- [ ] B4-1 OpenAPI→openapi-typescript 生成流水线（gen:api）
- [ ] B4-2 前端删手抄类型，接 generated.ts
- [ ] B4-3 D3Page 去伪造数据（置信度常量表 / verdictFrom 正则）
- [ ] B4-4 D2Page 去正则解析工具 JSON
- [ ] B4-5 B4 收口（gen:api && git diff --exit-code 门禁）

### B5 记忆命名空间 + 清理 + 文档

- [ ] B5-1 MemoryStore user_id 命名空间（data/users/{user_id}/）+ 存量迁移
- [ ] B5-2 semantic 层拆分（用户偏好留记忆 / 领域结论迁知识库种子）
- [ ] B5-3 坐标改用 favorite_spots（消灭 _DEFAULT_LAT 双份）+ 预算显式化
- [ ] B5-4 死代码清理 + requirements.txt 对齐 pyproject + shim 到期删除
- [ ] B5-5 ADR-004（并发为纯配置）+ architecture.md 重写 v3.0 + 文档同步

### B6 知识库链路（可与 B7 并行）

- [ ] B6-1 knowledge/ 骨架 + camera_specs.yaml 机型规格表（含 source/version）
- [ ] B6-2 SQLite FTS5 中文检索（trigram 分词）
- [ ] B6-3 KnowledgeProvider 装配 + 「命中才取」注入策略
- [ ] B6-4 pixel_pitch 端到端案例（档案存机型 → 知识库查规格，去手填）
- [ ] B6-5 B6 收口

### B7 意图路由 + 受控规划通道（可与 B6 并行）

- [ ] B7-1 意图路由器：规则优先通道
- [ ] B7-2 意图路由器：轻量 LLM 兜底判定（判定落 trace）
- [ ] B7-3 受控 Planner（Plan 一等对象 / 步数上限 8 / 每步过 dispatch+trace+配额）
- [ ] B7-4 超工具集显式告知 + ReAct 轮数 8→12 护栏生效
- [ ] B7-5 评估适配（L2 cassette 加 RequestContext 维度）
- [ ] B7-6 B7 收口 + 重构总验收

### 重构后再议（本期不做，见 PRD 附录 A2）

- [ ] 开源发布（README/CONTRIBUTING/CHANGELOG，**发布前必须小北确认**）
- [ ] E9-1 复拍机会被动提醒 / E9-2 就近快速推荐（被动 MVP；E9-3 定时推送已砍）
- [ ] E10 体验项（sessions 端点优先；token 流式延后）
- [ ] D2.2 多机位赶场调度 MVP（直线距离 + 手工估算；真实通勤延后）
- [ ] 火烧云判据工具化（低优先级待议，研究结论见 v4 §5.1）

## E1–E8 已完成（历史记录）

### 已基线（工具层 15 工具已注册，176 测试全绿）

- [x] 拍摄参数推荐：曝光换算 / 星空 500·NPF / 长曝光 ND（`equivalent_exposure` / `star_shutter_rule` / `nd_long_exposure`）
- [x] 天文查询：太阳时刻/方位 / 月相 / 月升月落 / 银心可见窗口（`sun_times` / `sun_position` / `moon_phase` / `moon_events` / `galaxy_visibility`）
- [x] 天气与火烧云：分层云量预报 / 火烧云评分（`weather_forecast` / `sunset_glow_score`）
- [x] 机位 × 天象匹配（`match_sites`）

### 阶段一：地基拆分与可观测性（E1+E2）✅ 已完成

- [x] E1-1 核心拆分：agent/core 拆 loop / context / router（`Agent.run` 签名不变）
- [x] E1-2 ContextBuilder 五层组装（静态前缀 ★ 缓存命中）
- [x] E2-1 TraceRecorder + 事件订阅接口（E7-4 SSE 桥接的底座）
- [x] E2-2 trace 注入 prompt + TraceReport（M2 依据/置信度来源）

### 阶段二：记忆层与配额感知（E3+E4）✅ 已完成

- [x] E3-1 用户档案（`data/profile.json` 常驻注入 ≤300 token）
- [x] E3-2 事件记忆（SQLite 按需检索 + `search_memory` 工具，含坐标/天气快照字段）
- [x] E3-3 语义记忆（精选注入，防污染 double-confirm）
- [x] E4-1 ModelRouter 能力矩阵（能力声明驱动 + 单测；能力矩阵可注入）
- [x] E4-2 QuotaLedger 配额账本（预估 / 记账 / 降级链可配置）
- [x] E4-3 reason 通道（深推理纯推理，tools=None，thinking 开启）

### 阶段三：决策编排与输出契约（E5）✅ 已完成

- [x] E5-1 Orchestrator 四管线（灵感 / 规划 / 临场 / 复盘）+ PipelineContext
- [x] E5-2 结构化输出契约（Intent / DecisionCard pydantic + 自愈 ≤2 次）
- [x] E5-3 一句话出方案闭环（端到端 + 追问回落 ReAct，含黄金用例集雏形）

### 阶段四：多模态与差异化（E6）✅ 已完成（含 E6-0 真实联调 + push）

- [x] E6-0 真实联调基线验证（四管线真实 Key 跑通 + 29 commits push 至 origin/main，HEAD 0b502a6）
- [x] E6-1 照片分析智能工具（analyze_photo，深度=1 红线；真实照片实测并入 E6-0 窗口）
- [x] E6-2 照片反推方案（reverse_engineer_photo + Orchestrator.reverse_plan）
- [x] E6-3 语义记忆提炼（sediment_semantics/sediment_favorite_spots，double-confirm）
- [x] E6-4 复盘管线填充（review 闭环：对账差异报告）
- [x] E6-5 阶段收口（evals/golden/E6-photo-cases.json 8 条 + 文档同步）

### 阶段五：Web 服务层（E7）✅ 已完成（E7-0~E7-5，212 测试 + 真实 SSE 联调）

- [x] E7-0 真实 Key + SSE 长连接联调验证（阶段开场任务）
- [x] E7-1 async ChatClient + 全局 LLM 队列（_AsyncGate 串行闸门，serial_llm 驱动）
- [x] E7-2 SessionManager 会话持久化（JSON 落盘 + LRU 缓存，重启恢复）
- [x] E7-3 FastAPI + SSE 路由（五个端点，事件协议见架构 v2.0 §2.8）
- [x] E7-4 trace 事件桥接 SSE（TraceBridge，「trace 即 UI」实时轨迹面板）
- [x] E7-5 前端 SPA 工程化（Vite + React + TS，三核心页：对话/会话列表/DecisionCard，npm run build + dev 代理实测）

### 阶段五·补：前端旅程页对齐（E7-6 ~ E7-10）✅ 已完成

> 小北确认设计稿（`docs/design/delivery/lighttrail-prototype.html`）为唯一视觉真源，实际前端需对齐补齐。
> 唯一 spec：`docs/design/FRONTEND-SPEC.md`；原设计文档 v1.1 归档于 `docs/design/archive/`。

- [x] E7-6 前端工程基座升级（令牌对齐修正漂移：--bg/--good/--bad → 真源取值；路由 3→6 页 #/home #/d1 #/d2 #/d3 #/d4 #/m1；顶栏 6 项 + 汉堡抽屉 + AppShell）
- [x] E7-7 旅程总览页 + M1 记忆页（今日决策环图 / 四阶段入口 + 器材档案 /api/profile / 事件历史 / 偏好芯片）
- [x] E7-8 D1 灵感页 + D2 规划页（方案卡 A/B/C + 参考图上传 + sky-band 天象时间线 / 机位列表 / 赶场轴）
- [x] E7-9 D3 决策页对齐（三态卡 / 置信度三层 / 倒计时 / 现场模式 / 曝光三角联动 / 四步推理——可解释性铁律①②硬门禁）
- [x] E7-10 D4 复盘页 + 解释中心 + 收口（批量上传 / 4 维分析 / 处方；铁律③；双视口自检 + 全站示例数据标注）

### 阶段六：评估体系（E8）✅ 已完成

> 黄金用例改为各阶段增量交付（每阶段收口 +5~10 条）；E8 本体做框架与 LLM-as-judge。

- [x] E8-0 真实评估基线验证（对标 E6-0/E7-0 联调开场原则，用最小样本验证 eval runner 链路）
- [x] E8-1 黄金用例集 + 管线回归（L2，cassette 回放，~30 条集中补齐；E6-5 已含 ≥8 条）
- [x] E8-2 LLM-as-judge（L3，rubric 打分 + 「工具即裁判」交叉校验）

## 工程遗留（接手 agent 留意）

> 2026-09-07 已全部清理：`_new_tests/` 已并入 tests/（43 用例全绿）；`cli.py` 全量注册 12 工具；
> `.gitignore` 已排除 `data/`；`_add_astral.py` 已删除；ruff 无告警；`smoke.py` 断言已修正（离线冒烟 19 项通过）。

## 灵感池：值得拍摄的窗口推荐

> 风光摄影「什么时候拍什么」备忘，后续可作为决策引擎的拍摄窗口知识库素材。

### 主动提醒 / 场景化推荐（PRD v0.3 §8.4 能力族，灵感补充）
- [ ] **休息日主动推荐**（D2.3-06）：预设休闲时间（如周末）→ 前日晚提醒「明天休息日，可能有晚霞」；常规机位 + 记忆联动（「计划里的临港，完成度约 8 成」）
- [ ] **复拍机会提醒**（D2.3-07）：3 月拍过福州大楼朝霞 → 7 月预测更优时提醒「优于你 3 月 18 那场，是否再去」；依赖事件记忆坐标 + 天气/天象快照
- [ ] **就近快速推荐**（D3.1-04）：临时陌生区域 / 出差 / 送人顺路 → 5km 内机位（≈20 分钟可达），「拍到比拍好重要」
- [ ] **通勤画像**（M1.1-04）：固定远距通勤（住杨浦 / 工作在安亭）→ 决策地点按当前所在地，下班时间覆盖晚霞/蓝调
- [ ] **UGC 机位挖掘**（PRD §9.1 P2）：小红书等平台搜机位，多源交叉验证（地图核位置 / 日月角度核可行性 / 云图核条件）后入库

### 天文类窗口
- [ ] **悬日 / 悬月**：太阳或月亮在特定日期、特定机位正好落在街道尽头 / 地标正上方 / 天际线交点。城市街道悬日需要提前用巧摄类工具算好日期与机位（如北京长安街悬日），一年只有几天窗口
- [ ] **蓝调时刻**：日出前 / 日落后天空呈深蓝色的时段，华灯初上、城市夜景黄金窗口
- [ ] **金色时刻**：日出后 / 日落前低角度暖光，顺光拍山体、逆光拍剪影
- [ ] **月升 / 月落**：满月前后从地平线升起的「大月亮」，配合地景
- [ ] **超级月亮 / 月食**：年度大事件，需提前踩点
- [ ] **银河季**：3–10 月（北半球），银心方向随季节变化，新月前后无月光干扰最合适
- [ ] **流星雨**：象限仪座（1 月）、英仙座（8 月）、双子座（12 月），极大夜前后
- [ ] **日晕 / 幻日 / 彩虹**：偶发气象窗口，可遇不可求，遇上了别犹豫

### 气象类窗口
- [ ] **水晶天**：冷空气过境 / 大风后空气通透度极高，能见度拉满，适合远眺天际线、雪山、跨海大桥等长焦压缩场景
- [ ] **火烧云**：日落前云层被染红，晚霞爆发，强对流天气傍晚概率更高
- [ ] **反烧（火烧云的一种）**：太阳落到地平线下后 20–40 分钟，残余霞光反照把天空再次染红，色彩常比正烧更浓郁，甚至出现在东边天空——日落收工别急着走
- [ ] **平流雾**：春夏季清晨，江面 / 城市低空铺雾，拍「云中城市」
- [ ] **雾凇 / 雪后初晴**：冬季低温高湿，清晨逆光拍雾凇
- [ ] **耶稣光（丁达尔）**：晨雾 / 云隙光，雨后清晨树林、山间常见
- [ ] **雷暴云 / 乳状云 / 荚状云**：强对流天气的特有云型，晚霞配云层戏剧性最强

### 城市 / 地标类窗口
- [ ] **城市天际线日出日落**：顺光时段，配合水晶天最出片
- [ ] **车轨 / 灯火夜景**：蓝调后 20–30 分钟，天未全黑、灯光已亮
- [ ] **扫街人文窗口**：晨光斜射、傍晚逆光剪影

---
*灵感持续补充中。每条窗口后续可标注：预测要素（天文计算 / 气象预报）、最佳机位参考、拍摄参数要点。*