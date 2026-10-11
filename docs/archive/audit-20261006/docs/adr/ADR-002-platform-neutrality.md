# ADR-002：平台中立性——并发策略、能力矩阵与配置命名去 ECNU 化

**Status**: Accepted（2026-09-07）

**Supersedes**: ADR-001 中「串行调用约束」一节（本 ADR 将其从架构前提降级为默认配置）

---

## Context

LightTrail 最初以华东师大 ECNU 大模型平台为主要 LLM 后端。ECNU 平台有两个特性：

1. **建议串行调用**（避免并行请求）；
2. **双模型能力互补**（ecnu-plus 支持工具+视觉，ecnu-max 支持深推理不支持工具）。

初始架构把这些**平台特性**当成了**产品需求/架构前提**，导致三处「约束错位」：

- `llm/client.py` 模块级 `_SERIAL_LOCK` 硬编码串行，换 API 无法关闭；
- `llm/router.py` 能力绑定硬编码为 ecnu-plus/ecnu-max 品牌映射，且预设「深推理与工具互斥」；
- 配置命名 `ECNU_API_KEY` 等语义绑定供应商。

问题暴露（2026-09-07）：后续可能接入其他支持并发的 API 做测试，ECNU 只是目前的主力。若继续把平台特性当架构前提，换供应商会导致大规模返工。

**原则**：平台限制进配置，架构保持中立。产品代码只认「能力声明」与「并发开关」，不认「ECNU」三个字。

---

## Decision

三个配套决策：

### D1：并发策略可配置（默认串行）

- 新增 `Settings.serial_llm`，环境变量 `LLM_SERIAL_LLM`（默认 `true`，语义为「适配 ECNU 建议」）；
- `ChatClient` 串行锁改为**实例级**、可配置；锁只包住**单次 API 往返**，重试在锁外（关闭串行后并发与重试解耦）；
- Web 阶段 async 通道（`acall`）的并发控制（默认 `asyncio.Semaphore(1)`）同样由此开关驱动；
- 接入支持并发的 API：`LLM_SERIAL_LLM=false`，业务代码零改动。

### D2：能力矩阵可注入，路由只认能力声明

- `ModelRouter` 新增 `capability_matrix` 参数，默认矩阵对应 ECNU 双模型，但可注入任意模型×能力映射；
- 路由逻辑只认 `needs_tools / needs_vision / needs_deep_reasoning` 三个能力声明，按矩阵线性匹配；
- **移除「深推理与工具/视觉互斥」的平台假设**：单模型全能（`default_model == reason_model`）时默认矩阵视为全能；多模型场景由矩阵表达能力分布；
- 非法声明/矩阵无满足 → `RouterError` 显式抛错，不静默回退。

### D3：配置命名通用化（LLM_ 前缀，ECNU_ 兼容别名）

- 主要环境变量改为通用前缀：`LLM_API_KEY / LLM_BASE_URL / LLM_MODEL / LLM_MODEL_REASON / LLM_SERIAL_LLM`；
- 保留 `ECNU_*` 别名兼容；两者同时存在时 `LLM_*` 优先；
- 文档（架构/README/路线图）统一使用 `LLM_` 前缀，ECNU 仅作为默认值与示例出现。

---

## Consequences

### 变容易的

- **换 LLM 供应商**：改 `.env` 的 base_url/model + 串行开关 + 能力矩阵即可，业务代码（Agent/管线/tools）零改动；
- **并发策略调整**：串行→并发是一行配置，不是改代码每个分支；
- **单模型/多模型架构**：由矩阵表达，不再需要在代码里预设能力组合；
- **配置迁移**：`LLM_` 语义中立，新接入方不会被 ECNU 误导。

### 变难的

- 默认配置下少了「ECNU 串行约束 → 单进程部署」的强论证（但仍成立，见架构 §2.9）；
- 配置项变多（LLM_* 与 ECNU_* 双前缀），需要维护别名解析逻辑（`_get_env`）；
- 能力矩阵的合法性校验（能力名白名单）需要测试覆盖，防止拼写错误。

### 风险与缓解

- **别名混用**：用户同时配了 `ECNU_API_KEY` 与 `LLM_API_KEY`，以 `LLM_*` 为准——在 `.env.example` 中明确注释；
- **能力矩阵错误**：矩阵无法满足声明时抛 `RouterError` 而非静默回退——测试覆盖。

---

## 相关链接

- 架构文档 v2.0 §2.9（并发与会话模型）、§2.3（能力矩阵驱动路由）
- `src/lighttrail/config.py`、`src/lighttrail/llm/client.py`、`src/lighttrail/llm/router.py`
- `tests/test_client_serial.py`、`tests/test_router.py`