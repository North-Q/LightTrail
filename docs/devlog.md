# LightTrail 开发日志

> 按任务单元记录进度与阻塞，供后续接手者审计。格式：任务编号 / 时间 / commit hash / 测试数量。

## 2026-09-07

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
