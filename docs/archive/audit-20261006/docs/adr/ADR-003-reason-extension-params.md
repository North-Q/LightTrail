# ADR-003：深推理扩展参数（thinking / reasoning_effort）的跨 SDK 平台适配

**Status**: Accepted（2026-09-07）

**相关**: ADR-002（平台中立性）——本文是其「适配收敛在适配层」原则在**请求扩展参数**上的延续

---

## Context

E4-3 的 reason 通道需要向深推理模型传递思考模式扩展参数
（`thinking` / `reasoning_effort`）。ECNU 官方文档确认 **ecnu-max 支持
`thinking={"type": "enabled"}` + `reasoning_effort`**，但真实联调暴露两层差异：

1. **SDK 版本差异**：openai SDK 3.1.0 的 `Completions.create()` 签名**没有**
   `thinking`/`reasoning_effort` 命名参数——未知 kwarg 直接 `TypeError`，请求根本没发出；
   而文档示例假设的 SDK 版本支持命名参数。同一参数在不同 SDK 版本行为不一致。
2. **平台差异**：不同供应商对扩展字段的支持不同（支持 / 忽略 / 拒绝）。

若把「发 thinking」硬编码在 reason 业务路径（E4-3 初版所为），换 SDK/API 即崩——
与 ADR-002 记录的平台特性渗透教训同构。

## Decision

三个配套决策：

### D1：扩展参数配置化（平台中立，默认适配当前主力平台）

- 新增 `Settings.reason_thinking`（环境变量 `LLM_REASON_THINKING`，**默认 true**，
  语义为「适配 ECNU ecnu-max 思考模式」）；
- `Agent.reason` 只在开关开启时携带扩展参数；关闭时 reason 与普通调用同构，
  任意 OpenAI 兼容接口开箱即用；
- 不支持思考字段的平台：`.env` 设 `LLM_REASON_THINKING=false`，业务代码零改动。

### D2：SDK 能力探测 + extra_body 通道（收敛在 ChatClient 适配层）

- `ChatClient` 启动时运行时探测 `create()` 签名（`_NATIVE_REASON_PARAMS`）：
  - 原生支持 → 走命名参数（规范路径）；
  - 不支持（如 openai 3.1）→ 经 **`extra_body`** 携带扩展参数（SDK 会把其合并进
    HTTP 请求体，OpenAI 兼容网关按请求体字段读取，与命名参数等价）；
- 业务层（`Agent.reason` / 管线）只声明「是否开启思考」，不感知 SDK 与平台差异；
- 无扩展参数时零污染（不产生 extra_body）。

### D3：默认值对齐当前主力平台官方能力

与 ADR-002 D1「默认串行适配 ECNU」同一叙事：默认配置面向 ECNU（官方支持思考模式），
约束进配置、架构保持中立。

---

## Consequences

### 变容易的

- **SDK 升级/降级**：thinking 参数不再因 SDK 签名变化而崩（探测 + extra_body 双路径）；
- **换 API**：改 `.env`（LLM_REASON_THINKING）即可，业务代码零改动；
- **测试**：monkeypatch 探测标志即可覆盖两条路径（命名参数 / extra_body），不依赖真实网络。

### 变难的

- 适配层多一次运行时探测（成本可忽略，模块级缓存一次）；
- 配置项增加（LLM_REASON_THINKING），需与 .env.example 注释同步。

### 风险与缓解

- **extra_body 被代理剥离**：极少数网关只认显式字段——探测优先命名参数已覆盖主流 SDK；
  若遇到剥离场景，升级 SDK 或在该平台注入支持命名参数的客户端版本。
- **开关与平台能力错配**：开了但平台不支持 → 网关忽略或 400；错误信息已携带服务端原因
  （见 infra/validation 与天气工具的 4xx 快速失败），可读可定位。

---

## 相关链接

- 架构 v2.0.1 §2.3（reason 通道）、ADR-002（平台中立性）
- `src/lighttrail/llm/client.py`（`_NATIVE_REASON_PARAMS` / `_attach_reason_params`）
- `src/lighttrail/agent/core.py`（`Agent.reason`）、`src/lighttrail/config.py`（reason_thinking）
- 实测记录：`docs/devlog.md`「真实联调修复」与「ecnu-max think 联调修复」条目
- `tests/test_client_serial.py`（extra_body / 原生两条路径用例）
