# LightTrail（光迹）· 架构重构路线图（B0–B7）

> **版本**：v1.0 ｜ **日期**：2026-09-12
> **定位**：架构重建期的**唯一任务清单**。单个 agent 读取对应任务章节即可独立完成该任务；任务粒度为「一个 turn 内可完成的原子单元」。
> **唯一权威依据**：`docs/architecture-v4-proposal.md`（D1–D14 全部拍板定稿）。本路线图不重复方案论证，只做任务拆解；任务与方案的对应关系以「§x」引用标注。
> **接替关系**：E1–E9 时代的旧路线图已归档至 `docs/archive/DEVELOPMENT-ROADMAP-v2.5.md`（仅作历史查阅，不再执行）。E9/E10 等未做功能项的去留见 v4 §9 需求范围表（被动提醒 E9-1/E9-2 延后至重构后，E9-3 砍掉，token 流式延后）。

---

## 1. 总览

### 1.1 批次地图

```mermaid
graph LR
    B0[B0 止血护栏<br/>修3 bug + 补tokens + 清假注释] --> B1
    B1[B1 契约层+配置<br/>contracts/ + pydantic-settings] --> B2
    B1 --> B4
    B2[B2 引擎重写<br/>声明式 ToolSpec + composition root + PydanticAI] --> B3
    B3[B3 适配层+并发<br/>async-first + Semaphore(4) + 并行取数] --> B5
    B3 --> B7
    B4[B4 契约单一真源+前端重接<br/>OpenAPI→TS 生成 + 去假数据] --> B5
    B5[B5 记忆命名空间+清理+文档<br/>user_id + 死代码 + ADR-004] --> B6
    B6[B6 知识库链路<br/>knowledge/ + FTS5 + pixel_pitch 案例]
    B7[B7 意图路由+受控规划通道<br/>规则优先+轻量LLM兜底 + Planner]
```

### 1.2 批次总表

| 批次 | 一句话目标 | 任务 | 依赖 | 可并行 |
|------|-----------|------|------|--------|
| **B0 止血护栏** | 修会话死锁/共享态/锁外写三 bug，补 tokens，清假注释 | B0-1 ~ B0-4 | 无 | — |
| **B1 契约层+配置** | 零依赖 `contracts/` + pydantic-settings + 用户体系预留接口 | B1-1 ~ B1-5 | B0 | — |
| **B2 引擎重写** | 注册表方向反转 + composition root + PydanticAI 接入 | B2-1 ~ B2-7 | B1 | — |
| **B3 适配层+并发** | async-first + Semaphore(4) + 并行取数 + tenacity/httpx | B3-1 ~ B3-5 | B2 | — |
| **B4 契约真源+前端** | OpenAPI→TS 生成 + 删前端假数据 | B4-1 ~ B4-5 | B1 | 与 B2/B3 并行 |
| **B5 记忆+清理+文档** | user_id 命名空间 + semantic 拆分 + 死代码 + ADR-004 | B5-1 ~ B5-5 | B2、B3 | — |
| **B6 知识库链路** | knowledge/ 全局只读 + FTS5 中文 + pixel_pitch 落地案例 | B6-1 ~ B6-5 | B5 | 与 B7 并行 |
| **B7 意图路由+规划通道** | 意图路由器 + 受控 Planner + 护栏生效 | B7-1 ~ B7-6 | B2、B3 | 与 B6 并行 |

### 1.3 批次纪律（每批结束必须满足）

1. **pytest 全绿 + ruff 0 告警 + 系统可用**（CLI 与 Web 均可跑）——「随时可演示」是求职作品硬需求；
2. 批间用 re-export shim 保向后兼容；shim 豁免必须带 TODO 与删除批次，批次结束立即移除（v4 §2.2）；
3. 每批独立 commit（可单 commit revert 回退）；
4. devlog 追加一行（任务编号 / commit hash / 测试数量）；
5. **不按日历赶工**——时间不设限，但批次纪律不变。

### 1.4 统一验收基线

- 后端改动：`pytest tests/` 全绿 + `ruff check src tests` 0 告警；
- 前端改动：`npm run build` 通过；
- 架构边界改动：`lint-imports` 全契约通过（B1 起逐步开启）；
- 契约改动：`gen:api && git diff --exit-code` 通过（B4 起）。

---

## 2. B0 止血护栏（v4 §6 B0）

> 批次目标：消灭三颗已确诊的真 bug 与「注释承诺大于实现」的信用破产点，让重构从干净的基线起步。本批**不改架构**，只修 bug 与注释。

---

### B0-1 修复 SessionManager 并发三 bug

- **目标**：修复 `api/session.py` 死锁 + 共享可变 + 锁外写三个 bug（v4 §1.2）。
- **前置依赖**：无
- **输入上下文**：
  - `api/session.py`：`_evict_if_needed()`（:257）在持 `_lock`（threading.Lock，:161，不可重入）时被 `create`/`restore`/`save` 调用，其内 `self.save(victim)`（:265）→ `save` 再取 `_lock`（:240）→ 同线程永久死锁；
  - `get()`（:183-197）返回缓存对象本体，路由层可直接改；
  - `save` 的 `write_text`（:237-239）在锁外，同 id 并发写损坏 JSON。
- **输出交付物**：
  - `api/session.py` 修复：① 锁内只改内存、落盘移出锁外（或改 `threading.RLock` 并明确注释取舍）；② `get()` 返回深拷贝/不可变快照；③ 落盘改「临时文件 + `os.replace`」原子写；
  - `tests/test_session.py` 新增：淘汰路径用例（缓存超上限触发 evict 不死锁）、并发写用例、`get()` 返回值不可变断言。
- **验收标准**：pytest 全绿（含新增 3 类用例）；复现路径（新建会话被淘汰）不再死锁。
- **涉及文件**：`api/session.py`、`tests/test_session.py`
- **难度**：⭐⭐⭐

---

### B0-2 record_llm 补 tokens 记账

- **目标**：`agent/loop.py:77-81` 调 `record_llm` 恒不传 tokens（None），导致 TraceReport 的 token 统计静默失真——把 ChatClient 返回的 usage 真正接上。
- **前置依赖**：无
- **输入上下文**：`agent/loop.py:77-81`（调用点）；`llm/client.py`（chat/acall 返回中已含 usage）；`infra/trace.py:224`（tokens 字段承诺存在但恒 None）。
- **输出交付物**：loop 在每次 LLM 调用后把 `usage`（prompt/completion tokens）传入 `record_llm`；TraceReport 输出真实 token 数。
- **验收标准**：FakeChatClient 注入固定 usage 后，TraceReport 中 tokens 非 None 且数值正确；pytest 全绿。
- **涉及文件**：`agent/loop.py`、`infra/trace.py`、`tests/test_trace.py`
- **难度**：⭐

---

### B0-3 假注释清理（四处）

- **目标**：四处「注释承诺 > 代码实现」要么变成真实现（成本极低时），要么删注释改为诚实描述（v4 §1.2 R3）。
- **前置依赖**：B0-2（tokens 那条已由 B0-2 兑现，此处复核）
- **输入上下文**（逐一核对，逐条处置）：
  1. `llm/client.py:7` 与 `config.py:11`「缓存策略」——全仓无 LLM 响应缓存实现 → **删注释**（缓存在 v4 方案中不是本期项）；
  2. `infra/trace.py:224` tokens —— B0-2 已兑现，核对注释与实现一致；
  3. `orchestrator/pipelines.py:256` 时效计算 —— 注释承诺的时效逻辑不存在 → 实现或删注释（按现场复杂度二选一，devlog 记录取舍）；
  4. `orchestrator/orchestrator.py:311` 档案定位 —— 注释承诺从 `favorite_spots` 取坐标，实际用写死的 `_DEFAULT_LAT/_LON` → 本批**只改注释为诚实描述**（真接线属 B5-3，不在此扩大范围）。
- **输出交付物**：四处注释与实现一致；devlog 记录每条的「实现/删注释」取舍。
- **验收标准**：grep 复查四处注释不再承诺不存在的功能；pytest 全绿。
- **涉及文件**：`llm/client.py`、`config.py`、`orchestrator/pipelines.py`、`orchestrator/orchestrator.py`
- **难度**：⭐

---

### B0-4 B0 收口（回归 + 基线记录）

- **目标**：B0 批次出口检查，建立重构前最终基线。
- **前置依赖**：B0-1 ~ B0-3
- **输出交付物**：pytest 全绿 + ruff 0；devlog 记录基线（commit hash / 测试数）；git tag 或 commit 标记 `refactor-baseline`（本地，不 push tag）。
- **验收标准**：CLI（`python -m lighttrail.cli`）与 Web（uvicorn + npm run dev）均可跑通一次真实查询。
- **涉及文件**：`docs/devlog.md`
- **难度**：⭐

---

## 3. B1 契约层 + 配置（v4 §6 B1，§2.3，§7）

> 批次目标：新建零依赖契约层 `contracts/`（R1/R2 的解药），迁移全部跨层共享类型；配置换 pydantic-settings；用户体系三处预留（RequestContext / 存储命名空间 / UserConfigProvider+KeyVault）在本批定型接口。

---

### B1-1 contracts/ 骨架：tool 与 context 契约

- **目标**：新建 `src/lighttrail/contracts/` 包，落地 `tool.py`（ToolSpec/ToolContext/Tool/Confidence/ToolResult）与 `context.py`（RequestContext）。
- **前置依赖**：B0
- **输入上下文**：v4 §2.3 签名级定义（`contracts/tool.py`、`contracts/context.py`）；RequestContext 字段：`user_id="_local"` / `session_id` / `llm_overrides`（§7.1）。
- **输出交付物**：
  - `contracts/__init__.py`（导出清单）；
  - `contracts/tool.py`：`ToolSpec`（frozen dataclass，含 `main_field`/`confidence`/`capabilities`，`to_openai_schema()`）、`ToolContext`（注入式上下文 + `emit()`）、`Tool` Protocol；
  - `contracts/context.py`：`RequestContext`（frozen dataclass）；
  - `tests/test_contracts.py`：ToolSpec→OpenAI schema 转换、frozen 不可变断言。
- **验收标准**：pytest 全绿；`contracts/` 内除 stdlib+pydantic 外零 import（人工核对，B1-5 起由 import-linter 强制）。
- **涉及文件**：`src/lighttrail/contracts/*`（新）、`tests/test_contracts.py`（新）
- **难度**：⭐⭐

---

### B1-2 contracts 模型迁移：Intent / DecisionCard / TraceEvent / Plan

- **目标**：把散在 `orchestrator/schemas.py` 与 `infra/trace.py` 的跨层模型下沉到契约层，旧路径转 re-export shim。
- **前置依赖**：B1-1
- **输入上下文**：`orchestrator/schemas.py`（Intent/DecisionCard）；`infra/trace.py`（TraceEvent）；v4 §2.3 `contracts/plan.py`（Plan/PlanStep，`max_steps=8`）。
- **输出交付物**：
  - `contracts/models.py`（Intent/DecisionCard）与 `contracts/plan.py`（Plan/PlanStep）、`contracts/events.py`（TraceEvent + SSE 事件类型枚举——前后端单一真源，B4 消费）；
  - `orchestrator/schemas.py` 与 `infra/trace.py` 改为 re-export shim（带 TODO：B5 删除）。
- **验收标准**：pytest 全绿（shim 保旧 import 路径可用）；无行为变化。
- **涉及文件**：`contracts/{models,plan,events}.py`（新）、`orchestrator/schemas.py`、`infra/trace.py`
- **难度**：⭐⭐

---

### B1-3 用户体系预留接口：UserConfigProvider / KeyVault / LLMConfig

- **目标**：落地 v4 §7 的配置与密钥抽象（本期只实现部署级，但解析单点本期定型）。
- **前置依赖**：B1-1
- **输入上下文**：v4 §2.3 `contracts/llm.py`（LLMConfig/LLMProvider/UserConfigProvider/KeyVault）；§7.2 优先级链（请求级 > 部署级 > 内置默认）；密钥安全四问（`LLMConfig.__repr__` 对 api_key 掩码只露后 4 位）。
- **输出交付物**：
  - `contracts/llm.py`：`LLMConfig`（frozen，含 `concurrency: int = 4`，`__repr__` 掩码）、`LLMProvider` Protocol、`UserConfigProvider` Protocol、`KeyVault` Protocol；
  - `adapters/llm/config_provider.py`：`DeploymentConfigProvider`（只读 .env/默认值，实现 resolve 优先级链）、`EnvKeyVault`；
  - 测试：优先级链（override > env > default）、repr 掩码断言。
- **验收标准**：pytest 全绿；`LLMConfig.__repr__` 不泄漏完整 Key。
- **涉及文件**：`contracts/llm.py`、`adapters/llm/config_provider.py`（新）、`tests/test_config_provider.py`（新）
- **难度**：⭐⭐

---

### B1-4 剩余 Protocol：MemoryStore / KnowledgeProvider / DataSource / TraceSink

- **目标**：补齐契约层其余端口定义（只定义接口与数据类，不实现）。
- **前置依赖**：B1-1
- **输入上下文**：v4 §2.3 `contracts/memory.py`（MemoryStore，方法首参 RequestContext）、`contracts/knowledge.py`（KnowledgeChunk 含 source/version + KnowledgeProvider，**不接 user_id**，D10/D14）、`contracts/datasource.py`、`contracts/observability.py`。
- **输出交付物**：四个契约文件 + 最小测试（Protocol 可被实现、KnowledgeChunk 字段完整）。
- **验收标准**：pytest 全绿；KnowledgeProvider 签名不含 user_id（D10 判定规则）。
- **涉及文件**：`contracts/{memory,knowledge,datasource,observability}.py`、`tests/test_contracts.py`
- **难度**：⭐

---

### B1-5 config.py 换 pydantic-settings + import-linter 契约门禁

- **目标**：配置层重写为 pydantic-settings；落统一护栏配置项；开启 import-linter「契约零依赖」契约（第一条门禁）。
- **前置依赖**：B1-2、B1-3
- **输入上下文**：v4 §2.2 import-linter 完整配置片段（照抄入 pyproject.toml）；§3 D5 护栏表（`LLM_CONCURRENCY=4` / `REACT_MAX_ROUNDS=12` / `PIPELINE_MAX_STEPS=12` / `PLAN_MAX_STEPS=8` / `LLM_TIMEOUT=(30,120)`）；D6 `AliasChoices` 表达 `LLM_`↔`ECNU_` 别名；`LLM_SERIAL_LLM` 作只读兼容别名过渡一版（映射为 concurrency=1）。
- **输出交付物**：
  - `config.py` 重写为 pydantic-settings Settings（类型校验 + 别名 + 护栏项 + data_dir 等既有字段）；
  - `pyproject.toml` 增 `[tool.importlinter]` 四段契约（layers / 契约零依赖 / 适配器不被反向依赖 / 交互层不被依赖），但本批只要求「契约零依赖」通过，其余契约允许暂挂 `ignored_imports`（带 TODO + 删除批次）；
  - 依赖声明加 `pydantic-settings`、`import-linter`；
  - `.env.example` 同步护栏项；
  - 配置单测：别名解析、并发默认值 4、护栏项默认值、`LLM_SERIAL_LLM=true` → concurrency=1 兼容映射。
- **验收标准**：pytest 全绿；`lint-imports`「契约零依赖」通过；ruff 0。
- **涉及文件**：`config.py`、`pyproject.toml`、`.env.example`、`tests/test_settings.py`
- **难度**：⭐⭐⭐

---

## 4. B2 引擎重写（v4 §6 B2，§2.4）

> 批次目标：注册表方向反转（工具声明 ToolSpec、装配根收集）、消灭全局单例、接入 PydanticAI（循环/DI/校验/TestModel 交框架，平台差异留在自定义 Model 桥）。**15 个工具名与行为不变。**

---

### B2-1 声明式 ToolSpec 改造：纯计算工具组（basic + exposure）

- **目标**：`tools/basic.py`、`tools/exposure.py` 改为「声明 ToolSpec + 纯函数 import contracts」，不再 import 全局 registry；元数据（main_field/confidence/capabilities）写在工具定义旁。
- **前置依赖**：B1
- **输入上下文**：v4 §2.3 ToolSpec 字段；§4 工具描述三法则（说清何时用 > 做什么；给参数示例与默认值；写明限制）——顺手按三法则重写 description；现 `agent/tools.py:158` 全局单例是要消灭的对象。
- **输出交付物**：两文件改造（每个工具 = 一个 Tool 实现 + 一个 ToolSpec）；`domain/tools/` 目录形态按 v4 §2.1 目标结构落位（或先就地改造 + B2-3 统一搬迁，devlog 记录取舍）；对应测试改用新调用方式。
- **验收标准**：pytest 全绿；两文件无 `agent.tools` import。
- **涉及文件**：`tools/basic.py`、`tools/exposure.py`、相关测试
- **难度**：⭐⭐⭐

---

### B2-2 声明式 ToolSpec 改造：天文与天气工具组

- **目标**：同 B2-1，覆盖 `tools/astronomy.py`、`tools/weather.py`。
- **前置依赖**：B2-1
- **验收标准**：pytest 全绿；无 `agent.tools` import；15 工具清单中本组 7 个工具名不变。
- **涉及文件**：`tools/astronomy.py`、`tools/weather.py`、相关测试
- **难度**：⭐⭐⭐

---

### B2-3 声明式 ToolSpec 改造：复合与智能工具组 + TOOLS 收集点

- **目标**：`tools/site_match.py`、`tools/memory_tool.py`、`tools/photo_analysis.py` 改造（智能工具的 LLM 依赖改经 ToolContext 注入，消灭模块级可写全局 setter 注入）；建立单一收集点 `domain/tools/__init__.py` 暴露 `TOOLS: tuple[Tool, ...]`。
- **前置依赖**：B2-2
- **输入上下文**：v4 §1.2「全局态测试注入」问题（`photo_analysis.py`/`memory_tool.py` 模块级全局 + setter）；§2.4 热插拔第 2 条（单一收集点，新增工具 = 追加一行）。
- **输出交付物**：三文件改造；`TOOLS` 元组；服务定位器全局态删除。
- **验收标准**：pytest 全绿（测试并发不再互相污染）；全仓无 `agent.tools` import（旧 `agent/tools.py` 转 shim）。
- **涉及文件**：`tools/{site_match,memory_tool,photo_analysis}.py`、`domain/tools/__init__.py`、`agent/tools.py`（转 shim）
- **难度**：⭐⭐⭐

---

### B2-4 ToolRegistry(specs) + composition root

- **目标**：新建 `runtime/registry.py`（构造注入 ToolSpec 列表，`dispatch(name, arguments_json, ctx)`）与 `composition.py` 装配根（全仓唯一 new 点）；`agent/` 转门面 shim。
- **前置依赖**：B2-3
- **输入上下文**：v4 §2.3 `runtime/registry.py` 签名；§2.2 过渡 shim 纪律（豁免带 TODO + 删除批次）。
- **输出交付物**：`runtime/registry.py`、`composition.py`（组装 Settings→LLMConfig→registry→memory→trace→orchestrator）；CLI 与 API 入口改从 composition root 取实例。
- **验收标准**：pytest 全绿；`lint-imports` 适配器相关契约通过（豁免仅 shim）；无模块级 registry 单例。
- **涉及文件**：`runtime/registry.py`（新）、`composition.py`（新）、`cli.py`、`api/app.py`
- **难度**：⭐⭐⭐

---

### B2-5 PydanticAI 自定义 Model 桥

- **目标**：实现 PydanticAI `Model` 桥，把框架的 LLM 调用接到我们的 `LLMProvider`（ADR-002/003 适配层不丢：`_NATIVE_REASON_PARAMS` 探测 + extra_body 双路径保留）。
- **前置依赖**：B2-4
- **输入上下文**：v4 §3 D2（框架管循环/DI/校验，平台特性留 Model 桥，约 200 行）；`llm/client.py` 现有重试/扩展参数逻辑（语义保留，代码重写）；pydantic-ai v2.x pin 版本。
- **输出交付物**：`adapters/llm/pydantic_bridge.py`；`pyproject.toml` 加 `pydantic-ai`（pin 版本）；桥的单测（thinking/extra_body 透传、无工具模型 prompted 模式）。
- **验收标准**：pytest 全绿；桥层不引入平台品牌字面量到适配层以外（ruff 检查）。
- **涉及文件**：`adapters/llm/pydantic_bridge.py`（新）、`pyproject.toml`
- **难度**：⭐⭐⭐⭐

---

### B2-6 AgentRuntime 接入（ReAct 循环交框架）

- **目标**：`runtime/agent.py`——PydanticAI Agent 组装门面（ReAct 对话 / 意图解析 / reason 综合共用装配）；ContextBuilder 五层逻辑从 `agent/context.py` 保留迁入 `runtime/context.py`，工具能力叙述改由 `registry.to_openai_schema()` 自动生成（消灭 `DEFAULT_ROLE_PROMPT` 手写工具清单）。
- **前置依赖**：B2-5
- **输入上下文**：v4 §2.3 `runtime/agent.py` 签名；§2.4 热插拔第 3 条（能力叙述自动生成）；`agent/context.py:44-52` 现状硬编码清单。
- **输出交付物**：`runtime/agent.py`、`runtime/context.py`；`agent/loop.py`/`agent/context.py` 转 shim。
- **验收标准**：pytest 全绿；新增工具后 system prompt 工具叙述自动包含（无需手改）。
- **涉及文件**：`runtime/{agent,context}.py`（新）、`agent/*`（转 shim）
- **难度**：⭐⭐⭐⭐

---

### B2-7 TestModel 替换 FakeChatClient + test_hotplug + B2 收口

- **目标**：8 处自建 FakeChatClient 统一替换为 PydanticAI TestModel；落地 `tests/test_hotplug.py` 热插拔验收；B2 出口检查。
- **前置依赖**：B2-6
- **输入上下文**：v4 §2.4 第 5 条（动态构造 ToolSpec 注入，断言 dispatch/trace/confidence/能力叙述四处自动生效）；§5 测试保留度（8 处 FakeChatClient 替换）。
- **输出交付物**：测试改造 + `tests/test_hotplug.py`；`lint-imports` 禁止边全开（除 shim 豁免）；devlog 记录。
- **验收标准**：pytest 全绿；test_hotplug 通过；15 工具名不变；`lint-imports` 通过。
- **涉及文件**：`tests/`（多处）、`tests/test_hotplug.py`（新）
- **难度**：⭐⭐⭐

---

## 5. B3 适配层 + 并发（v4 §6 B3，§3 D5）

> 批次目标：async-first 一套实现（消灭同步/异步复制与三套并发机制）；并发 = 纯配置 `LLM_CONCURRENCY` 默认 4；管线并行取数；tenacity 统一重试；httpx 数据源。

---

### B3-1 async-first LLMProvider（单 Semaphore）

- **目标**：`adapters/llm/` 重写为 async-first LLMProvider 实现：单个 `asyncio.Semaphore(LLM_CONCURRENCY)` 只包单次 LLM 往返、重试在外；删除 `threading.Lock` + `_AsyncGate` + `call_soon_threadsafe` 三套机制；CLI 用 `asyncio.run` 包异步 runtime。
- **前置依赖**：B2
- **输入上下文**：v4 §3 D5（候选 A 已定；`LLM_SERIAL_LLM` 删除，被 `LLM_CONCURRENCY=1` 覆盖）；§1.2「同步/异步双份实现」现状。
- **输出交付物**：`adapters/llm/provider.py`；`llm/client.py` 删除（语义已迁入）；并发测试（concurrency=4 时 4 路并行、=1 时严格串行）。
- **验收标准**：pytest 全绿（含异步用例）；`LLM_CONCURRENCY` 切换测试通过。
- **涉及文件**：`adapters/llm/provider.py`（新）、`llm/client.py`（删）、`cli.py`
- **难度**：⭐⭐⭐⭐

---

### B3-2 tenacity 统一重试 + httpx 数据源

- **目标**：三套重试收敛为 tenacity 一处（LLM 调用 + weather 工具 + photo_analysis 全部复用）；数据源 HTTP 调用统一走 httpx（`DataSource` Protocol 实现）。
- **前置依赖**：B3-1
- **输入上下文**：v4 §1.2「重试三套」现状（client 指数退避 / weather 立即重试 / photo_analysis 手写校验回传）；`infra/validation.parse_with_retry` 是被遗忘的既有资产。
- **输出交付物**：`adapters/datasources/`（httpx + tenacity）；三处重试替换；依赖声明加 tenacity/httpx。
- **验收标准**：pytest 全绿；429/5xx 退避行为不变（测试断言）。
- **涉及文件**：`adapters/datasources/*`（新）、`tools/weather.py`、`tools/photo_analysis.py`
- **难度**：⭐⭐⭐

---

### B3-3 管线并行取数（asyncio.TaskGroup）

- **目标**：四管线数据采集（天气/天文/机位/光污染四源无依赖）从顺序 for 循环改 `asyncio.TaskGroup` 并发；单源失败降级 `{error}` 不拖垮管线；取数并发不受 LLM 信号量约束。
- **前置依赖**：B3-1
- **输入上下文**：v4 §3 D5 配套①（采集阶段延迟预计降 50-60%）。
- **输出交付物**：`application/pipelines.py` 采集步骤改造；并发/降级测试；延迟对比记录进 devlog（改造前后各跑一次计时）。
- **验收标准**：pytest 全绿；采集阶段延迟降 ≥40%；单源失败时管线仍产出卡片。
- **涉及文件**：`application/pipelines.py`、相关测试
- **难度**：⭐⭐⭐

---

### B3-4 TraceSink Protocol 落地 + OTel 命名

- **目标**：`infra/trace.py` 改造为 `TraceSink` Protocol 实现（`adapters/trace/`）；事件字段对齐 OTel GenAI 语义命名（`gen_ai.*`）；tokens 经 B0-2 已接通，本批核对事件载荷白名单（不整包 dumps 请求体，防 Key 泄漏）。
- **前置依赖**：B3-1
- **输入上下文**：v4 §3 D12（命名跟标准、实现保闭环）；§3 D7 密钥安全四问之防日志泄漏。
- **输出交付物**：`adapters/trace/`；SSE 桥接改订阅 TraceSink；白名单过滤 `redact()` 出口。
- **验收标准**：pytest 全绿；trace/SSE 事件无敏感字段（测试断言 payload 白名单）。
- **涉及文件**：`adapters/trace/*`（新）、`infra/trace.py`、`api/events.py`
- **难度**：⭐⭐⭐

---

### B3-5 B3 收口（import-linter 全开 + 死锁回归 + 并发配额锁）

- **目标**：开启全量 import-linter 契约（仅保留必要 shim 豁免）；QuotaLedger 并发记账加锁；B0 死锁修复在 async 形态下回归验证。
- **前置依赖**：B3-2、B3-3、B3-4
- **输入上下文**：v4 §3 D5「并发下 QuotaLedger 记账需加锁（小改）」。
- **输出交付物**：`infra/quota.py` 加锁改造；`lint-imports` 全契约通过；devlog 记录。
- **验收标准**：pytest 全绿（含并发记账用例）；`lint-imports` 通过；CLI/Web 均可跑。
- **涉及文件**：`infra/quota.py`、`pyproject.toml`（豁免清单收敛）
- **难度**：⭐⭐

---

## 6. B4 契约单一真源 + 前端重接（v4 §6 B4，§3 D8）

> 批次目标：pydantic 单一真源 → OpenAPI → openapi-typescript 生成；删前端手抄类型与全部伪造数据。只依赖 B1，可与 B2/B3 并行。

---

### B4-1 OpenAPI→TS 生成流水线

- **目标**：建立 `npm run gen:api` 流水线：FastAPI（复用 contracts 模型）→ OpenAPI JSON → `openapi-typescript` → `frontend/src/api/generated.ts`。
- **前置依赖**：B1
- **输入上下文**：v4 §3 D8（CI 跑 `gen:api && git diff --exit-code`，漂移即红）。
- **输出交付物**：`frontend/package.json` 加 `gen:api` 脚本 + `openapi-typescript` 依赖；生成产物首提交；生成说明注释。
- **验收标准**：生成流水线一键跑通；`generated.ts` 覆盖 SSE 事件与 DecisionCard 类型。
- **涉及文件**：`frontend/package.json`、`frontend/src/api/generated.ts`（新）
- **难度**：⭐⭐

---

### B4-2 前端删手抄类型，全面接生成物

- **目标**：删 `frontend/src/api/events.ts` 手抄双份（SSE 事件类型 + DecisionCard），全部 import 改指向 `generated.ts`。
- **前置依赖**：B4-1
- **输出交付物**：`events.ts` 删除或收敛为 re-export；全前端类型引用切换。
- **验收标准**：`npm run build` 通过；全仓 grep 无第二份手抄契约。
- **涉及文件**：`frontend/src/api/events.ts`、引用它的全部组件
- **难度**：⭐⭐

---

### B4-3 D3Page 去伪造数据（置信度常量表 + verdictFrom 正则）

- **目标**：删除 `frontend/src/pages/D3Page.tsx:36-40` 置信度硬编码常量表（78/58/34）与 `:44` `verdictFrom` 中文正则猜结论，改接 DecisionCard 真实字段（conclusion/confidence 区间）。
- **前置依赖**：B4-2
- **输入上下文**：v4 §1.2「前端伪造数据」现状；2026-09-10 devlog 产品化打磨时已部分缓解（「由置信度区间换算」），本任务彻底接真实字段。
- **输出交付物**：D3Page 数据层重接；置信度展示直接消费卡片字段。
- **验收标准**：`npm run build` 通过；页面无常量表/正则猜结论逻辑；真实 `/api/decide` 跑通渲染。
- **涉及文件**：`frontend/src/pages/D3Page.tsx`
- **难度**：⭐⭐

---

### B4-4 D2Page 去正则解析工具 JSON

- **目标**：删除 D2Page 对 tool_result 文本的正则解析，改消费结构化字段（契约生成物类型）。
- **前置依赖**：B4-2
- **输出交付物**：D2Page 数据层重接。
- **验收标准**：`npm run build` 通过；无 JSON 正则解析。
- **涉及文件**：`frontend/src/pages/D2Page.tsx`
- **难度**：⭐⭐

---

### B4-5 B4 收口（CI 漂移门禁）

- **目标**：`gen:api && git diff --exit-code` 纳入提交流程（写入 codex-longterm-goal 门禁清单）；B4 出口检查。
- **前置依赖**：B4-3、B4-4
- **验收标准**：契约改动后未重新生成 → diff 非零被拦截（实测验证一次）；`npm run build` + pytest 全绿。
- **涉及文件**：`docs/codex-longterm-goal.md`、`docs/devlog.md`
- **难度**：⭐

---

## 7. B5 记忆命名空间 + 清理 + 文档（v4 §6 B5，§3 D10）

> 批次目标：MemoryStore 落地 user_id 命名空间；semantic 口袋按「绑不绑 user_id」拆分；坐标改用 favorite_spots；死代码与依赖声明清理；ADR-004 落盘；architecture.md 重写为目标架构。

---

### B5-1 MemoryStore user_id 命名空间

- **目标**：所有记忆持久化路径迁入 `data/users/{user_id}/memory/`（本期恒 `_local`）；`MemoryStore` 接口方法首参 `RequestContext`；存量数据迁移脚本（`data/` 旧结构 → 新结构，幂等）。
- **前置依赖**：B2、B3
- **输入上下文**：v4 §7.1 预留②（存储命名空间隔离）；§7.3「不动层只消费 ctx.user_id」。
- **输出交付物**：`memory/` 改造 + 迁移脚本 + 测试（命名空间隔离断言、迁移幂等）。
- **验收标准**：pytest 全绿；迁移后行为与现状等价（单用户）。
- **涉及文件**：`memory/*`、`adapters/stores/*`、迁移脚本
- **难度**：⭐⭐⭐

---

### B5-2 semantic 层拆分（记忆 vs 知识库）

- **目标**：`memory/semantic.py` 混装口袋拆分：绑定用户事件的结论留 `memory/semantic`；领域结论（如「卷云冰晶对长波散射敏感」）迁出，暂存为 B6 知识库的种子数据文件。
- **前置依赖**：B5-1
- **输入上下文**：v4 §3 D10（判定规则：绑不绑 user_id；`memory/semantic.py:1-9` 现状）。
- **输出交付物**：拆分后的 semantic 存储 + `data/knowledge/seed/` 种子文件（待 B6 入库）。
- **验收标准**：pytest 全绿；semantic 中无「关于世界」的条目。
- **涉及文件**：`memory/semantic.py`、`data/knowledge/seed/`（新）
- **难度**：⭐⭐

---

### B5-3 坐标改用 favorite_spots + 预算显式化

- **目标**：消灭 `orchestrator/orchestrator.py:34-36` 与 `pipelines.py` 双份 `_DEFAULT_LAT/_LON` 与 `_candidate_sites`；管线定位改从 `memory/manager.py` 已备的 `sediment_favorite_spots` 取（B0-3 注释承诺在此兑现）；ContextBuilder 各层 token 预算显式化为配置。
- **前置依赖**：B5-1
- **验收标准**：pytest 全绿；grep 无 `_DEFAULT_LAT`；空档案时有诚实降级（提示用户先录机位）。
- **涉及文件**：`orchestrator/*`、`application/*`、`runtime/context.py`
- **难度**：⭐⭐

---

### B5-4 死代码清理 + 依赖声明对齐

- **目标**：删 `agent/tools.py:26` 无 raise 的 `ToolError`、`orchestrator.py:30` 死 import 等死代码；`requirements.txt` 对齐 pyproject（补 fastapi/uvicorn/python-multipart/pydantic-settings 等，删「仅依赖 openai SDK」失实注释）；B1-B3 过渡 shim 按 TODO 到期删除。
- **前置依赖**：B5-3
- **验收标准**：pytest 全绿；ruff 0；`pip install -r requirements.txt` 全新环境可跑（venv 实测）。
- **涉及文件**：全仓死代码点、`requirements.txt`、`pyproject.toml`、各处 shim
- **难度**：⭐⭐

---

### B5-5 ADR-004 + architecture.md 重写 + 文档同步

- **目标**：落盘 `docs/adr/ADR-004-concurrency-as-config.md`（并发为纯配置，记录「ECNU 串行约束不成立」前提变更，不改写 ADR-002）；`docs/architecture.md` 重写为 v3.0（目标架构 = v4 §2，旧六层图与现状描述全部更新）；README/AGENTS.md/TODO 同步。
- **前置依赖**：B5-4
- **输入上下文**：v4 §2.1 目标分层图、§2.2 依赖规则、§2.3 签名；ADR-002/003 格式（保持一致风格）。
- **输出交付物**：ADR-004；architecture.md v3.0；README 状态刷新；devlog 记录。
- **验收标准**：文档基线 = 实际 HEAD；architecture.md 中无「六层」旧表述。
- **涉及文件**：`docs/adr/ADR-004-concurrency-as-config.md`（新）、`docs/architecture.md`、`README.md`、`AGENTS.md`、`TODO.md`
- **难度**：⭐⭐

---

## 8. B6 知识库链路（v4 §6 B6，§3 D10/D14）

> 批次目标：新建全局只读 `knowledge/`（结构化判据表 + FTS5 中文文本检索）；首个落地案例 = `pixel_pitch` 机型规格表（消灭工具描述写死知识 + 用户手填）。**火烧云判据表不在本批**（v4 §5.1 低优先级待议）。与 B7 可并行。

---

### B6-1 knowledge/ 骨架 + 机型规格表数据

- **目标**：新建 `domain/knowledge/` 包骨架与 `data/knowledge/` 目录；录入首个结构化表 `camera_specs.yaml`（机型 → pixel_pitch / 传感器 / 像素，含 `source`/`version` 溯源字段），首批含松下 S5M2、索尼 A7C2、尼康 FE（胶片无 pixel_pitch，标注不适用）。
- **前置依赖**：B5
- **输入上下文**：v4 §3 D10 首个落地案例；§3 D14 知识分类（(b) 类结构化规则精确查表）。
- **输出交付物**：包骨架 + YAML 表 + 加载器（精确查表 `lookup(key, table=)`）。
- **验收标准**：pytest 全绿；查「S5M2」返回 pixel_pitch ≈ 6.0 且带 source。
- **涉及文件**：`domain/knowledge/*`（新）、`data/knowledge/camera_specs.yaml`（新）
- **难度**：⭐⭐

---

### B6-2 SQLite FTS5 中文文本检索

- **目标**：`KnowledgeProvider.search()` 落地：SQLite FTS5，`tokenize='trigram'`（中文必须显式选，v4 §3 D14 实测结论）；B5-2 迁出的领域结论种子数据入库。
- **前置依赖**：B6-1
- **输入上下文**：v4 §3 D14 中文关键事实（unicode61 把连续中文整段当一个 token；trigram 任意子串可命中）。
- **输出交付物**：FTS5 建表/入库/查询实现 + 中文查询用例（查「散射」能命中「卷云冰晶对长波散射敏感」）。
- **验收标准**：pytest 全绿；中文子串查询命中；BM25 排序可用。
- **涉及文件**：`domain/knowledge/fts.py`（新）、`data/knowledge/`（db）
- **难度**：⭐⭐⭐

---

### B6-3 KnowledgeProvider 装配 + 注入策略

- **目标**：`KnowledgeProvider` 接入 composition root；ContextBuilder 增加「命中才取片段」的知识注入策略（不常驻，不占静态前缀缓存）。
- **前置依赖**：B6-2
- **输入上下文**：v4 §3 D10 注入策略列（知识库「命中才取片段」 vs 记忆「分层常驻/按需」）。
- **输出交付物**：装配 + 注入逻辑 + 测试（命中注入、未命中零开销）。
- **验收标准**：pytest 全绿；知识片段注入带 source 溯源标注。
- **涉及文件**：`composition.py`、`runtime/context.py`
- **难度**：⭐⭐

---

### B6-4 pixel_pitch 端到端案例（exposure.py 去手填）

- **目标**：`star_shutter_rule`（`tools/exposure.py:137-140`）的 `pixel_pitch` 参数从「用户手填 + 描述硬编码 S5M2」改为：档案（记忆）存机型名 → 工具运行时经 ToolContext 查知识库规格表得 pixel_pitch；参数仍可显式覆盖（无档案机型时回退手填）。
- **前置依赖**：B6-3
- **输入上下文**：v4 §3 D10 案例描述（消灭 R3 类「知识写死在工具描述里」问题）。
- **输出交付物**：exposure 工具改造 + 端到端测试（档案写 S5M2 → 不传 pixel_pitch → NPF 结果与手填 6.0 一致）。
- **验收标准**：pytest 全绿；工具描述中的机型硬编码删除。
- **涉及文件**：`tools/exposure.py`（或 domain/tools 新路径）、`memory/profile.py`
- **难度**：⭐⭐⭐

---

### B6-5 B6 收口

- **目标**：B6 出口检查；黄金用例新增知识库相关断言；devlog 记录。
- **前置依赖**：B6-4
- **验收标准**：pytest 全绿；`lint-imports` 通过；evals L2 回放仍零成本通过。
- **涉及文件**：`evals/golden/`、`docs/devlog.md`
- **难度**：⭐

---

## 9. B7 意图路由 + 受控规划通道（v4 §6 B7，§3 D3，§3.15）

> 批次目标：意图路由器（规则优先 + 轻量 LLM 兜底，判定落 trace）+ 受控 Plan-Execute 通道（Plan 一等对象、步数上限、每步过 dispatch/trace/配额）+ ReAct 护栏生效。与 B6 可并行。

---

### B7-1 意图路由器：规则优先通道

- **目标**：`application/router.py`——规则表匹配明确意图（现有 `default_mode`/`Intent.mode` 确定性分流逻辑迁入），命中直接分发四管线；规则表可配置。
- **前置依赖**：B2、B3
- **输入上下文**：v4 §3 D3 分发规则表；§3.15(1) 混合判定（规则优先）。
- **输出交付物**：`application/router.py` + 规则表 + 分发测试（四管线各命中用例）。
- **验收标准**：pytest 全绿；已知意图零 LLM 调用直接命中。
- **涉及文件**：`application/router.py`（新）、`orchestrator/orchestrator.py`
- **难度**：⭐⭐⭐

---

### B7-2 意图路由器：轻量 LLM 兜底判定

- **目标**：规则不明确时走一次轻量模型判定（复用 RouteIntent.DEFAULT 轻量配置），输出 `RoutingDecision`；判定结果落 trace 供审计；阈值可配置。
- **前置依赖**：B7-1
- **输入上下文**：v4 §3.15(1)（已知取舍：模糊请求多一次轻量调用，小北已确认接受）。
- **输出交付物**：兜底判定 + `RoutingDecision` 契约 + trace 记录 + 测试（TestModel 模拟判定）。
- **验收标准**：pytest 全绿；判定事件在 trace 可见。
- **涉及文件**：`application/router.py`、`contracts/models.py`
- **难度**：⭐⭐

---

### B7-3 受控 Planner：Plan 生成与执行

- **目标**：`runtime/planner.py`——`make_plan(goal, ctx)`（LLM 产 Plan，pydantic 校验 + 自愈重试）+ `execute(plan, ctx)`（按步执行，每步过 dispatch/trace/配额；步数上限 `PLAN_MAX_STEPS=8`）。
- **前置依赖**：B7-2
- **输入上下文**：v4 §2.3 `runtime/planner.py` 签名；§3 D3「计划是可枚举、可审核、可复现的对象」；复用基础设施表（注册表/trace/配额/ContextBuilder/DecisionCard 零新造）。
- **输出交付物**：Planner 实现 + 测试（合法 Plan 执行到底、超限截断、单步失败降级）。
- **验收标准**：pytest 全绿；规划通道产物为合法 DecisionCard 且计划落 trace。
- **涉及文件**：`runtime/planner.py`（新）、`application/router.py`
- **难度**：⭐⭐⭐⭐

---

### B7-4 超工具集显式告知 + ReAct 护栏生效

- **目标**：意图超出工具集能力时明确回复「暂不支持」+ 替代建议（不幻觉不硬答）；`REACT_MAX_ROUNDS` 8→12 等护栏项全部生效并在 trace 可见。
- **前置依赖**：B7-3
- **输入上下文**：v4 §3 D3 分发表第四行；§3 D5 护栏表。
- **输出交付物**：告知文案 + 护栏接线 + 测试（超集意图 → 诚实告知；轮数达上限 → 终止并说明）。
- **验收标准**：pytest 全绿；护栏触发时 trace 含原因。
- **涉及文件**：`application/router.py`、`runtime/agent.py`
- **难度**：⭐⭐

---

### B7-5 评估适配（L2 cassette 加 RequestContext 维度）

- **目标**：evals 三层随新架构适配：L2 采集路径走新 `ToolRegistry.dispatch(ctx, ...)`，cassette 加 RequestContext 维度（恒 `_local`）；断言目标 import 路径更新；「工具即裁判」交叉校验保留。
- **前置依赖**：B7-4
- **输入上下文**：v4 §3 D11 骨架重写后的接口适配清单。
- **输出交付物**：evals 改造；`python -m evals.runner --level L2` 零成本通过。
- **验收标准**：L1/L2 全绿；L2 仍零 LLM 成本。
- **涉及文件**：`evals/*`
- **难度**：⭐⭐

---

### B7-6 B7 收口 + 重构总收口

- **目标**：B7 出口 + 全部批次总验收：pytest 全绿 / ruff 0 / `lint-imports` 全契约 / `gen:api` diff=0 / CLI+Web 真实跑通 / shim 清零检查。
- **前置依赖**：B7-5（及 B6 完成，若并行）
- **输出交付物**：devlog 重构总结（已完成/遗留/建议 + 平台中立性审计）；codex-longterm-goal 📍 状态块更新。
- **验收标准**：上述六项全过；grep 无残留 shim TODO。
- **涉及文件**：`docs/devlog.md`、`docs/codex-longterm-goal.md`
- **难度**：⭐

---

## 10. 共享约定（执行中遵守）

1. **工具热插拔验收口径**：新增工具只改 `domain/tools/__init__.py` 一行（B2 起），dispatch/trace/confidence/能力叙述自动生效——违反即返工。
2. **shim 纪律**：过渡 shim 必须带 `TODO(删除批次: Bx)` 注释；到期批次不删 = 批次不通过。
3. **品牌字面量红线**（ADR-002）：`ecnu` 等字面量只允许出现在 `adapters/llm` 与 settings 默认值，ruff 静态检查兜底。
4. **知识写死红线**：领域判据/规格不得写死在工具描述或代码常量里，一律进知识库（B6 起）——工具描述只写「何时用」。
5. **每任务节奏**：实现 → pytest/ruff（前端加 npm build）→ 中文 commit → devlog 一行。push 沿项目主线节奏（已授权）；对外发布仍须小北确认。
6. **与 v4 冲突时**：以 `docs/architecture-v4-proposal.md` 为准，并把冲突点记入 devlog 提示主理人同步本路线图。

---

*本路线图 v1.0，与 `architecture-v4-proposal.md`（定稿）配套。批次 B0 → B7 顺序执行；B4 可与 B2/B3 并行，B6 与 B7 可并行。*
