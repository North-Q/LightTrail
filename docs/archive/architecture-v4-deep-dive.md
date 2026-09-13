# LightTrail（光迹）· 架构方案 v4 深度分析

> **作者**：高见远（Gao，软件架构）｜ **日期**：2026-09-11｜ **状态**：待小北对结论与修订建议拍板
> **定位**：本文是 `docs/architecture-v4-proposal.md`（v4）的补充分析，不改动 v4 正文。回答小北四组追问：D7/D9/D11/D13 机制详解、D3「固定管线之外的任务」挑战、RAG 必要性、D10「记忆 vs 知识库」挑战。
> **结论前瞻**：三处挑战中有两处成立，需修订 v4 的表述——**D3 修订为「确定性管线 + 分层兜底」**、**D10 修订为「记忆四层（per-user）+ 知识库（全局）双链路」**；RAG 结论是「分层引入、向量后置」（不笼统拒绝也不盲目上马）。汇总见 §5。

---

## 0. 阅读指南

- §1 详解 D7/D9/D11/D13（各按「问题 → 设计 → 落地 → 演进 → 误区 → 面试」六段）。
- §2 回应 D3 挑战（管线之外的任务）。
- §3 分析 RAG 必要性（知识四分类 + 五候选 + 火烧云案例）。
- §4 回应 D10 挑战（记忆 vs 知识库）。
- §5 对 v4 的修订建议汇总 + 新增决策点（供小北对照拍板）。

---

## 1. 第一组：D7 / D9 / D11 / D13 详解

### 1.1 D7 配置与用户级 Key 优先级链

**① 它到底在解决什么问题**

现状 `config.py:92-110` 的 `load_settings()` 每次调用都直接读环境变量（`_get_env("LLM_API_KEY", "ECNU_API_KEY"）`），从「部署级 .env」到「LLMConfig」之间没有解析层。当前只有一个调用场景（单机单用户），所以不痛。一旦要支持「终端用户在界面上填自己的 Key」，问题立刻出现：

- **发散解析的代价**：如果每个需要 Key 的地方各自读配置（今天 `config.py`、明天某个工具、后天路由中间件），那么「这次请求该用谁的 Key」这个判断会散落在 N 处。改一次优先级规则要改 N 处，漏一处就出 bug；测试要 mock N 处；日志里可能 N 处都打印过 Key。
- **具体场景**：用户 A 填了自己的 DeepSeek Key，用户 B 没填用平台 Key。若解析发散，某条路径（比如照片分析这个「智能工具」内部自建客户端，见 `tools/photo_analysis.py`）可能直接读到部署级 Key，导致 A 的照片分析走平台配额而不是自己的额度——且极难排查。

**② 设计是什么**

单一解析入口 `UserConfigProvider.resolve(ctx) -> LLMConfig`（v4 §2.3），优先级链三条，从高到低：

```
def resolve(self, ctx: RequestContext) -> LLMConfig:
    # 1) 请求级：per-user 覆盖（未来界面填的 Key 经 KeyVault 取出挂到 ctx）
    if ctx.llm_overrides is not None:
        return ctx.llm_overrides
    # 2) 部署级：.env / 环境变量（本期唯一来源）
    # 3) 内置默认（base_url/model 兜底）
    return self._deployment_config   # 本期实现
```

本期实现 `DeploymentConfigProvider` 只读 `.env`；`ctx.llm_overrides` 恒 None。**所有 LLM 调用方只拿 resolve 结果，永不直读环境变量**——这是「未来零侵入」的前提。

**③ 具体怎么落地（B1 / B2）**

1. B1：`contracts/llm.py` 定义 `LLMConfig` 与 `UserConfigProvider` Protocol；`config.py` 换 pydantic-settings，保留 `LLM_`/`ECNU_` 别名（`AliasChoices`）。
2. B2：composition root 构造唯一 `DeploymentConfigProvider`，注入 AgentRuntime / 管线 / 智能工具；把现状中所有「工具内部自读 settings」的点（如 `tools/photo_analysis.py:27`、`tools/memory_tool.py:16` 的 `load_settings()`）改为从 `ToolContext.request` 取。
3. 验收：`tests/` 断言「给两个不同 RequestContext，resolve 出不同 LLMConfig」。

**④ 未来怎么演进**

per-user Key 上线时：新增 `EncryptedStoreKeyVault`（B5 已定 KeyVault Protocol），新增「组合 Provider」（先查用户覆盖、再落部署级）；前端加设置页 + `PUT /api/settings/llm`；中间件解析登录态 → 构造带 `llm_overrides` 的 RequestContext。**业务层（runtime/domain/application）零改动。**

**⑤ 密钥安全设计（per-user Key 上线时必须回答的四问）**

| 问题 | 方案 |
|---|---|
| **存哪** | 不落明文磁盘。存加密后的密文（`EncryptedStoreKeyVault`），主密钥来自部署环境（KMS/环境变量），**主密钥与密文分离存放**；或用系统 keyring（本地自用形态） |
| **怎么加密** | 对称加密（如 AES-GCM / libsodium SecretBox），每条 Key 独立 nonce；主密钥绝不进仓库、不进日志 |
| **怎么防日志泄漏** | ① `LLMConfig` 的 `__repr__` 对 `api_key` 做掩码（只露后 4 位）；② trace/SSE 事件里**禁止出现 Key**（`TraceSink.emit` 的 payload 用白名单字段，不整包 dumps 请求体）；③ 统一 `redact()` 工具在出口过滤 |
| **泄漏爆炸半径** | per-user Key 泄漏只影响该用户自己的额度，不牵连平台 Key——这正是 per-user 化的安全收益；平台 Key 永不落库、永不进会话（现状 `api/session.py:11` 已声明「会话不落凭据」，要保持） |

**⑥ 本地自用 vs SaaS 部署两形态差异**

| 形态 | 用户来源 | Key 来源 | 存储 | 认证 |
|---|---|---|---|---|
| 本地自用（本期） | 恒 `_local` | 部署级 `.env` | 本地 JSON/SQLite | 无 |
| SaaS（未来） | 登录 token | per-user（KeyVault）> 部署级 | 加密 DB | 中间件 + 多用户隔离 |

两形态共用同一套 `UserConfigProvider`/`KeyVault` 接口，差异只在「注入哪个实现」——这是优先级链设计成 Protocol 的根本理由。

**⑦ 常见误区 / 反直觉点**

- 误区：**「Key 从请求上传最方便」**——错误。浏览器直传 Key 会进网络日志、反代日志、trace，且无服务端保管抽象；客户端只该传「我已登录」，Key 由服务端按 user_id 从 Vault 取。
- 反直觉：**优先级链的「请求级」其实不是给 Key 用的，是给「模型/参数覆盖」用的**。用户可能填了 Key 但想临时换 model，所以 `llm_overrides` 是整块 `LLMConfig` 覆盖，不是单个 api_key。

**⑧ 面试怎么讲**

「LLM 配置我收敛成一个 `resolve(ctx)` 单点，优先级是『请求级（per-user）> 部署级 .env > 默认』。这样单机自用和 SaaS 用同一套接口，只是注入不同实现。per-user Key 的安全我按四层考虑：加密存储、主密钥分离、repr 掩码、trace 白名单——重点是爆炸半径控制，用户的 Key 泄漏不该牵连平台。为什么不把 Key 从请求上传？因为那等于把密钥放进日志和反代。」

---

### 1.2 D9 架构边界强制

**① 它到底在解决什么问题**

现状 `docs/architecture.md:98-107` 声明六层单向依赖，但实测存在四组反向边（`tools→orchestrator.schemas`、`tools→agent.tools`、`infra/quota.py:19→llm.router`、`orchestrator/orchestrator.py:30→tools.astronomy`）。根因是全局单例 `registry`（`agent/tools.py:158`）让工具反向拉住核心层。「纸面架构」不是疏忽，而是**没有强制手段时，分层约定必然被局部便利侵蚀**。D9 就是把边界变成可执行约束。

**② 设计是什么——import-linter 的实际配置片段**

```toml
# pyproject.toml
[tool.importlinter]
root_package = "lighttrail"

[[tool.importlinter.contracts]]
name = "分层契约：非法方向依赖"
type = "layers"
layers = [
    "lighttrail.interface",
    "lighttrail.application",
    "lighttrail.runtime",
    "lighttrail.domain",
    "lighttrail.contracts",
]

[[tool.importlinter.contracts]]
name = "契约层零依赖"
type = "forbidden"
source_modules = ["lighttrail.contracts"]
forbidden_modules = [
    "lighttrail.interface", "lighttrail.application", "lighttrail.runtime",
    "lighttrail.domain", "lighttrail.adapters",
]

[[tool.importlinter.contracts]]
name = "适配器只实现端口，不被领域反向依赖"
type = "forbidden"
source_modules = ["lighttrail.domain", "lighttrail.application", "lighttrail.runtime"]
forbidden_modules = ["lighttrail.adapters"]

[[tool.importlinter.contracts]]
name = "交互层不被任何层依赖"
type = "forbidden"
source_modules = [
    "lighttrail.contracts", "lighttrail.domain", "lighttrail.runtime",
    "lighttrail.application", "lighttrail.adapters",
]
forbidden_modules = ["lighttrail.interface"]
```

> 说明：`adapters` 不在 `layers` 链里，因为它只允许指向 `contracts`（用独立的 forbidden 契约表达），这样能精确表达「适配器不被上层依赖、也不依赖上层」这一非对称规则。v4 §2.2 的四条禁止边与上面四个 contract 一一对应。

**③ 三者分工边界（为什么三条都要）**

| 手段 | 能抓什么 | 抓不到什么 | 角色 |
|---|---|---|---|
| **import-linter** | 包级/模块级分层与禁止依赖；支持 layers/forbidden/independence 类型 | 函数内 import（延迟 import）、字符串形式的动态 import、`__import__` | 主门禁 |
| **pytest 架构测试**（`tests/test_architecture.py`） | 用 importlib + AST 遍历**源码**，能抓函数内 import、能自定义规则（如「domain 不得出现字面量 'ecnu'」） | 需要自己写，维护成本；不覆盖运行期动态行为 | 兜底 + 自定义规则 |
| **ruff**（`flake8-tidy-imports`） | 单文件级 import 禁令、快速反馈（IDE 内即时） | 表达不了跨模块分层契约 | 快速反馈 + 防品牌字面量 |

反直觉点：**三者不是冗余，而是覆盖不同粒度**。import-linter 抓「模块间图」、ruff 抓「单文件内」、AST 测试抓「两者抓不到的动态/函数内」。缺 import-linter 则分层形同虚设；缺 ruff 则反馈慢；缺 AST 测试则函数内 import 钻空子。

**④ CI 怎么接**

```
# 提交前 / CI
lint-imports                    # import-linter：分层契约
ruff check src tests            # ruff：风格 + import 禁令
pytest tests/test_architecture.py tests/   # AST 架构测试 + 功能测试
```

违规即红。禁止边违约信息会指向具体文件和 import 语句，修复指引明确。

**⑤ 冲突 / 误报怎么办**

- **场景一：合法的「类型检查期」跨界**。`domain` 需要在类型标注里引用 `Runtime` 类 → 用 `if TYPE_CHECKING:` 导入。import-linter 默认把 TYPE_CHECKING 内 import 也算依赖 → 在契约里用 `allow_indirect_imports` 或改为字符串前向引用（`"Runtime"`）规避。
- **场景二：过渡期 shim 违规**。B1/B2 迁移期保留了 re-export shim（如 `agent.Agent` 门面），shim 天然是「旧路径 → 新包」，可能短暂违规。处理：迁移期把该 shim 目录加入 `ignored_imports`，批次结束（shim 删除）后**立即移除豁免**——豁免列表必须带 TODO 和删除批次。
- **场景三：pytest 架构测试误报（正则匹配到注释/字符串）**。AST 方案天然避免（只遍历真实 import 节点），不用正则即可规避。
- **原则**：豁免是临时的、可见的、带到期批次；不做「永久白名单」（那等于把门禁又变回约定）。

**⑥ 面试怎么讲**

「我把分层做成 CI 硬门禁——import-linter 声明分层契约和禁止边，违规 CI 红。为什么三重手段？import-linter 抓模块间依赖图，ruff 抓单文件 import，AST 架构测试抓函数内 import 和自定义规则，三者覆盖不同粒度，缺一就有钻空子的地方。过渡期 shim 我会加临时豁免，但要求带删除批次——不做永久白名单，否则门禁又退化成口头约定。我的原型六层就是没有门禁才退化的，这是真实教训。」

---

### 1.3 D11 评估体系保留度

**① 三层各验证什么、成本多少**

| 层 | 验证对象 | 方法 | 确定性 | 成本 |
|---|---|---|---|---|
| **L1 工具层** | 纯计算/复合工具的算法正确性 | pytest 精确断言（NPF/500 法则边界、ND 档位、日出日落边界） | 完全确定 | 零（离线） |
| **L2 管线层** | 端到端决策卡片的结构与方向 | 黄金用例 + Fake 数据源 + LLM **cassette 录制回放** | 完全确定（回放） | 零 LLM 成本（分钟级跑完） |
| **L3 质量层** | 开放对话与综合表达的质量 | LLM-as-judge 按 rubric 1-5 分 + 人工小样本校准 | 概率性 | 有配额（~500 credits/次，走 QuotaLedger 预算门禁） |

**②「工具即裁判」的具体实现机制**

核心：**用确定性工具的输出反向校验模型的定性建议**。以 `star_shutter_rule`（`tools/exposure.py:114-196`）为例——它实现了 500 法则（`rule_500 = 500.0 / eff_focal`，:177）与 NPF 法则。校验流程：

```python
def judge_shutter_advice(model_card: DecisionCard, focal: float) -> tuple[bool, str]:
    """拿模型建议的快门 vs 500 法则算出的上限做交叉校验。"""
    # 1) 从决策卡片提取模型建议的快门（params 里 name 含"快门"的值）
    advised = _extract_shutter(model_card.params)
    if advised is None:
        return True, "卡片未给快门建议，跳过"
    # 2) 用确定性工具独立算一遍（同样的机身参数）
    rule = star_shutter_rule(eff_focal=focal)          # 纯计算，可复现
    limit = rule["500 法则最大快门（秒）"]
    # 3) 比对：模型建议超过上限 → 判定为"违反物理法则"的假阳性
    if advised > limit * 1.1:                           # 留 10% 容差
        return False, f"模型建议快门 {advised}s 超过 500 法则上限 {limit}s，星点会拖线"
    return True, ""
```

L3 judge 输出里附这个交叉校验结果：**当 judge 说「这张卡片质量高」但确定性校验发现「快门违反 500 法则」时，标记为 judge 假阳性**——这就是「工具即裁判」，用可复现的计算给概率性的 judge 提供锚点。同理可用于火烧云评分（模型说「适合拍火烧云」但 `sunset_glow_score` 算出 20 分 → 质疑）。

**③ 为什么通用框架（Ragas/DeepEval）解释力不足——具体论证**

不是喊「领域不同」，而是三条具体原因：

1. **Ragas 的核心指标是面向 RAG 问答的**：`faithfulness`（回答是否忠于检索上下文）、`answer_relevancy`（回答是否切题）、`context_precision/recall`。LightTrail 的产出不是「基于检索文档的回答」，而是「一张包含结论/依据/置信度/机位/参数的决策卡片」，**faithfulness 的前提（有检索上下文）在本场景不成立**——我们没有「检索文档」这个对象。
2. **通用框架无法接入「领域确定性裁判」**：Ragas/DeepEval 的校验器是「LLM 或 embedding 相似度」，它们没有「调用 `star_shutter_rule` 独立算一遍」这种 hook。而 LightTrail 评估的核心价值恰恰是这个 hook——**确定性工具能反向验证概率性建议**。要么改框架源码，要么自研，自研更干净。
3. **领域 rubric 无法用通用指标表达**：判一张决策卡片好不好，要问「参数是否符合曝光三角/500 法则」「机位朝向是否匹配天象方位」「是否坦白了不确定性」——这些是摄影领域判断，通用的 relevance/faithfulness 指标算不出来。

反直觉点：**LightTrail 评估成本的大头在 L2 的 cassette 录制与维护，不在判分逻辑**；而通用框架能省的恰恰是判分逻辑（小头），省不了领域 rubric 与确定性裁判（大头）。所以自研边际成本更低、解释力更强。

**④ 骨架重写后要改哪些接口**

| 接口 | 改动 |
|---|---|
| L1 工具单测 | 基本不动（领域算法保留）；仅当工具签名从 `func(**kwargs)` 改 `func(ctx, **kwargs)` 时同步（B2） |
| L2 cassette | 采集路径从 `registry.dispatch` 改走新 `ToolRegistry.dispatch(ctx, ...)`，cassette 的录制/回放层要加 `RequestContext` 维度（本期恒 `_local`） |
| L2 断言目标 | `DecisionCard` 移入 `contracts/models.py`，断言 import 路径变（B1） |
| L3 judge | 与 L2 共用契约；`QuotaLedger` 记账接口随 B3 并发改造加锁 |
| 入口 | `python -m evals.runner --level L2` 保持不变（对外命令稳定） |

**⑤ 面试怎么讲**

「我做了三层评估，确定性递增：L1 工具精确断言、L2 cassette 黄金用例回放（零 LLM 成本）、L3 LLM-as-judge。差异化在『工具即裁判』——比如模型建议快门 30 秒，我用 star_shutter_rule 独立算一遍 500 法则上限，超了就判 judge 假阳性。通用框架如 Ragas 的指标是面向 RAG 问答的 faithfulness，我的产出是决策卡片、没有检索上下文这个对象，指标对不上；而且它没有『调确定性工具复核』的 hook。所以评估我自研——无评估不迭代。」

---

### 1.4 D13 用户体系预留方式

**① 三处预留在代码里长什么样**

```python
# ① RequestContext 贯穿（contracts/context.py）
@dataclass(frozen=True)
class RequestContext:
    user_id: str = "_local"
    session_id: str = ""
    llm_overrides: LLMConfig | None = None

# 用法：作为 ToolContext / MemoryStore / SessionManager / UserConfigProvider 的第一参数
ctx = RequestContext(user_id="_local", session_id=sid)
await registry.dispatch("weather_forecast", args, ToolContext(request=ctx, ...))

# ② 存储命名空间（adapters/stores）
#   data/users/{user_id}/sessions/   data/users/{user_id}/memory/{profile.json,events.db,semantic.json}
#   本期 user_id 恒 "_local" → 等价于现状 data/ 单目录

# ③ 配置与密钥抽象（contracts/llm.py）
class UserConfigProvider(Protocol):
    def resolve(self, ctx: RequestContext) -> LLMConfig: ...
class KeyVault(Protocol):
    def get_api_key(self, user_id: str) -> str | None: ...
    def set_api_key(self, user_id: str, key: str) -> None: ...
```

**②「本期恒 `_local`，未来零改动」凭什么成立——未来接认证的 diff 想象**

| 改动范围 | 未来接认证时会改的文件 | 绝不改的文件 |
|---|---|---|
| **动** | `api/middleware.py`（新增：解析 token → 构造 RequestContext）、`api/routes.py`（从请求取 ctx）、`composition.py`（注入 `EncryptedStoreKeyVault` + 组合 Provider）、`adapters/stores/*`（路径拼 user_id；多 worker 时换 DB）、前端设置页 | — |
| **不动** | — | `contracts/*`（Protocol 已定）、`domain/tools/*`、`runtime/*`、`application/pipelines.py`、`memory/*` 的逻辑（只消费 ctx.user_id） |

为什么「不动」成立：这些层的代码**只调用 `ctx.user_id` 和 `resolve(ctx)`，从不自己决定「我是谁」或「用哪个 Key」**——身份与配置的决策被推到了边界（L0 中间件 + composition root）。这是依赖倒置：内层依赖抽象（RequestContext/Provider），具体身份由外层注入。

**③ 多用户 + 多 worker 时会话存储必须换 DB 的临界点**

现状 `api/session.py` 是「进程内字典 + 单机 JSON 落盘」。临界点判断：

- **单进程多用户**：JSON 落盘 + 路径带 user_id 即可，**不用换 DB**。
- **多 worker（`uvicorn --workers N`）+ 会话需跨进程共享**：JSON 落盘会因「worker A 写的会话 worker B 读不到」而失败 → **必须换共享存储（DB）**。
- 判断条件（可执行）：当部署形态从单进程变为多 worker，**且**同一 session 的请求可能落到不同 worker → 换 DB。接口层（`SessionManager`）不变，只换实现（`adapters/stores` 加一个 `DbSessionStore`）。这也是 v4 §7.5 已列的触发条件。

**④ 成本对比：现在预留 vs 事后补穿透（量化）**

| 成本项 | 现在预留（B1/B2 顺手） | 事后补穿透 |
|---|---|---|
| 契约层新增 | 2 个 Protocol + 1 个 dataclass（写进本就要新建的文件）≈ 0.2 人日 | — |
| 签名穿透 | B2 重写时顺手把 `ctx` 设为第一参数，**零额外**（反正每个签名都要重写） | 全仓调用点重穿：session/memory/quota/各工具/各路由，约 **15-25 个签名 + 所有调用点** ≈ 3-5 人日，且极易漏（死锁 bug 就是「漏一处」的教训） |
| 存储命名空间 | B5 记忆改造时一并做 ≈ 0.3 人日 | 迁移既有数据目录结构 + 全路径改 ≈ 1-2 人日 |
| KeyVault/Provider 抽象 | B1 定义 Protocol ≈ 0.2 人日 | 未来重构调用链 ≈ 2-3 人日 |
| **合计** | **约 0.7 人日（且几乎全在「本就要写的代码」里）** | **约 6-10 人日 + 漏改风险** |

结论：差异不是 10 倍，而是「**顺手** vs **返工**」。返工还伴随一个隐性成本——事后补穿透时，边界已经定型，容易被「为省事就地读配置」的诱惑侵蚀，重蹈 R3（注释即承诺）覆辙。

**⑤ 面试怎么讲**

「用户体系本期不做，但我留了三处接缝：RequestContext 贯穿身份、存储按 user_id 命名空间、UserConfigProvider/KeyVault 抽象配置与密钥。未来接认证时，我只需要动中间件、路由装配和存储适配器——domain/runtime/contracts 一行不改，因为它们只消费 ctx.user_id 和 resolve 结果，从不自己决定身份。成本上，现在留是『写进本就要新建的文件』，事后补是全仓重穿签名加漏改风险。这跟我从 ADR-002 学到的教训一致：已知的未来需求，在结构新建期留接缝几乎免费。」

---

## 2. 第二组：D3 挑战——固定管线之外的任务怎么办

> 小北追问：「现有任务固定流程，但有可能用户提出不在固定任务之外的任务？是否要考虑」。这是对 D3 的有效挑战，必须正面回应。

### 2.1 现状：双模是什么，ReAct 通道的能力边界在哪

**现状双模**（`docs/architecture.md` §2.1）：

- **结构化入口** → 四管线（确定性代码化 DAG）：`orchestrator/orchestrator.py:190-210` `run_pipeline` 按 `intent.mode` 或 `default_mode(subject_type)` 选管线。
- **开放式入口** → ReAct（`agent/loop.py`）：`api/routes.py:162-190` 的 `/api/chat` 走 `agent.run()`。
- **降级兜底**：管线任何异常 `orchestrator.py:186-188` 捕获 → `_fallback`（:368-374）转 ReAct；管线成功后可继续追问，也转 ReAct。

**ReAct 通道的实际能力边界**：

| 维度 | 现状 | 证据 |
|---|---|---|
| 最大工具轮次 | **8**（`agent/loop.py:26` `MAX_TOOL_ROUNDS = 8`） | 超过则终止并提示「工具调用次数过多」 |
| 规划能力 | **无显式规划器**。`loop.py:66-91` 是纯 ReAct：每轮 `chat → tool_calls → dispatch → 回传`，模型隐式决定下一步 | 循环体无 plan 步骤 |
| 工具集 | 15 个（`tools/__init__.py`），涵盖曝光/天文/天气/机位/记忆/照片分析/反推 | — |
| 上下文 | ContextBuilder 五层（`agent/context.py`），档案常驻 + 事件按需 + 语义择优 | — |
| 记忆 | 有（每轮注入） | — |

**开放域任务在现状下的典型表现**：

- 「明天杭州适合拍什么？」——ReAct 能调 `weather_forecast` + `sun_times` + 可能 `match_sites`，8 轮内能给出可用的开放回答。**能兜住**。
- 「帮我规划一次三天两夜、从上海自驾去黄山、中间拍银河和日出、预算 1500」——需要多步规划（D1 行程 + D2 机位 + 预算约束），ReAct 无规划器、8 轮上限、无预算工具，**大概率中途截断或答不全**。**兜不住**。
- 「先看这周末条件，如果好就帮我订个计划，不好就推荐室内题材」——需要条件分支 + 用户确认，ReAct 单轮无法持有「如果…否则…」的显式状态。**兜不住**。

### 2.2 枚举「管线之外的任务」类型

| 类型 | 例子 | 现状能否兜住 | 兜不住会怎样 |
|---|---|---|---|
| **T1 跨管线复合任务** | 「先推荐机位，再按最优机位给我参数和计划」 | ⚠️ 勉强（ReAct 依次调工具） | 覆盖不全、无管线级评分与 trace 结构；多机位调度（D2.2）本就未实现 |
| **T2 需多步独立规划的任务** | 三天两夜多目标行程编排、预算约束 | ❌ 否 | 8 轮截断；无规划器导致顺序混乱；无预算工具 |
| **T3 纯知识问答** | 「什么是 NPF 法则？」「蓝调时刻怎么拍？」 | ✅ 是（模型内置知识 + 少量工具） | 一般可答；但专业深度受限（见 §3 知识库） |
| **T4 需用户交互确认的任务** | 「条件好就订计划，否则换室内」 | ❌ 否 | 无 human-in-the-loop，无法暂停等待确认 |
| **T5 完全超出工具集的任务** | 「帮我修这张照片的噪点」（无修图工具）、「拨打电话提醒我」 | ❌ 否 | 模型只能坦白「我做不到」或幻觉 |

**结论**：ReAct 能兜住 T1（勉强）、T3；兜不住 T2、T4、T5。T5 属能力边界（诚实告知即可），T2/T4 是真实的、会暴露给用户的缺口。

### 2.3 候选方案对比

| 候选 | 优点 | 缺点 | 适用条件 |
|---|---|---|---|
| **A 仅 ReAct 兜底（现状）** | 零新增；简单 | T2 多步任务截断；T4 无法确认；8 轮上限 | 任务域窄、开放问题简单时 |
| **B 双层兜底 + 补强 ReAct**（提高轮数上限、加轻量规划器） | 改动小；复用 ReAct | 轮数上限提高会放大成本与失控风险；轻量规划器仍是隐式、不可测 | 开放任务以「多几步工具调用」为主、少分支时 |
| **C 意图路由器 + 三类分发**（已知意图→管线；可规划意图→Plan-Execute 通道；开放对话→ReAct） | 分工明确；规划通道显式可控可测；复用四管线基础设施 | 要新增一个规划通道与路由判定 | 存在「可规划但不在固定管线内」的任务时（本项目 T2） |
| **D 引入通用 agent 自主编排** | 最灵活 | 用 LLM 编排已知流程，放弃确定性（正是 D3 否掉的路）；不可测、成本不可预 | 任务域完全开放、无固定流程时 |

### 2.4 明确推荐 + D3 是否修正

**推荐 C（意图路由器 + 三类分发），并把 D3 从「代码化 DAG」修正为「确定性管线 + 分层兜底」。**

关键论证——**D3 原始理由在管线覆盖不到的场景是否仍成立？**

D3 否掉 LLM 自主规划的理由是「已知流程交给 LLM 规划等于放弃确定性」。这个理由的**精确边界**是：**对「已在 PRD 写死的四管线」成立，对「管线之外的可规划任务」不成立**——因为那些任务的流程**本来就不是已知的**，不存在「放弃确定性」的问题（本来就没有确定性可放弃）。所以：

- 对 D1–D4 已知流程：**保持代码化 DAG**（原结论不变）。
- 对「可规划但非固定管线」的 T2/T4：**显式引入 Plan-Execute 通道**，让 LLM 生成计划但**在受控骨架内执行**（计划是一等对象、可落 trace、可审核、可测试），而不是让 Agent 自由发挥。

**D3 修订版表述**：

> **D3（修订）确定性管线 + 分层兜底**：主路径是代码化确定性 DAG（四管线，流程已知）。之外设**三层兜底**：① 跨管线复合任务 → 意图路由器将其映射到已有管线的组合；② 可规划但非固定流程的任务（多步行程编排等）→ **受控 Plan-Execute 通道**（LLM 产计划、显式执行、计划可落 trace/可测）；③ 开放对话与纯知识问答 → ReAct（`MAX_TOOL_ROUNDS` 从 8 提升至 12，并把上限、超时、预算作为可配置护栏）。**不用 LLM 自主编排已知流程——已知流程的确定性不放弃；未知流程用显式受控的规划，而非自由 ReAct。**

> 补充触发条件（诚实清单）：若 T4「需用户交互确认」成为高频需求，再评估 human-in-the-loop（届时与 v4 §7.5 的 LangGraph checkpointer 触发条件合流）；本期仅保证「不支持时明确告知并降级」，不实现暂停恢复。

### 2.5 若加规划通道，复用哪些现有基础设施（避免又造一套）

| 复用的基础设施 | 复用方式 |
|---|---|
| **工具注册表**（`ToolRegistry`，B2 后声明式） | 规划通道执行每一步仍经 `registry.dispatch(ctx, ...)`，不新增工具调用路径 |
| **trace**（`TraceSink`） | 计划本身作为 step 事件落 trace（「规划 → 执行 step1/2/3」），复用「trace 即 UI」闭环 |
| **配额**（`QuotaLedger`） | 规划一次 + 执行 N 次 LLM 调用，全部走既有记账与预算门禁；计划执行前先预估 |
| **领域工具** | 计划步骤直接复用 15 个工具，零新增领域逻辑 |
| **ContextBuilder** | 计划执行每步复用五层上下文组装 |
| **决策卡片契约** | 规划通道产物仍输出 `DecisionCard`（contracts），前端零新契约 |

**关键约束**：规划通道是**受控的**——计划先结构化（pydantic `Plan` 模型，步骤列表），执行按步骤循环（本质是「运行时生成的浅 DAG」），每步有超时/重试/降级，计划整体有步数上限。这与「让 Agent 自由 ReAct 到底」有本质区别：**计划是可枚举、可审核、可复现的对象**。

**面试怎么讲（D3 修订）**

「我一开始说不用 LLM 自主规划，因为摄影决策流程已知。但小北追问『管线之外的任务怎么办』是对的——我的理由只对已写死的四管线成立。所以我把它修正成『确定性管线 + 分层兜底』：已知流程用代码化 DAG，跨管线任务由路由器组合已有管线，可规划的非固定任务走受控 Plan-Execute（计划是一等对象、可落 trace、可测），开放对话走 ReAct。核心判断没变——不放弃已知流程的确定性；变的是承认『不是所有任务都已知』，给未知任务一个受控的规划通道，而不是要么硬塞进管线、要么放任 ReAct 自由发挥。」

---

## 3. 第三组：RAG 必要性分析（火烧云 / 专业知识 / 论文）

> 小北问：「火烧云预测可能依赖专业知识库、某些专业论文，有没有加入 RAG 的必要」。关键：**先把「知识」分类，逐类判断**，不笼统答要/不要。

### 3.1 知识四分类与逐类判断

| 类别 | 定义 | 是否 RAG | 用什么形态 | 理由 |
|---|---|---|---|---|
| **(a) 确定性算法的参数/判据** | 火烧云评分的云量/湿度/风权重（`tools/weather.py:330-362`）、500 法则、ND 档位表（`tools/exposure.py:19-29`） | **否** | 代码常量 + 结构化规则 | 要求可复现、可审计、可单测；知识来源可以是论文/经验，但**产物必须是代码或结构化规则**，不是可检索文本 |
| **(b) 结构化领域规则** | 题材×天象事件映射（`tools/site_match.py:26-34` `_THEME_EVENT`）、方位差扣分表（:37-42）、题材关键词表（`memory/manager.py:257-271`） | **否**（用结构化知识库，非向量 RAG） | JSON/YAML/SQLite 表，**精确查表** | 检索是「按键取值的确定性查表」，不需要语义近似；向量化反而引入不可解释性 |
| **(c) 事实性文本知识** | 某机位实际状况、地标信息、天气系统常识、摄影技巧解释、器材特性 | **是**（RAG 正当场景） | SQLite FTS5（中文 trigram / jieba）起步，向量后置 | 这类是「非结构化文本 + 语义/词面检索」，RAG 的经典场景；但当前数据量极小，先 FTS5 |
| **(d) 论文里的方法** | 云微物理参数化、光污染传播建模 | **部分**（RAG 只负责溯源，不负责执行） | 离线人工转化为 (a)/(b) + 论文元数据入库供溯源 | RAG 只能「找到并引用来源」，**不能把论文变成可执行逻辑**；正确路径是离线人工把方法转成算法/规则，RAG 只挂「依据出处」 |

**一句话**：**能变成代码/规则的知识，不要进 RAG**（那是 (a)/(b)，RAG 会退化其确定性与可测性）；**只能以文本形态提供服务、且需要检索的知识，才进 RAG**（(c)）；**论文是 (a)/(b) 的原料，不是运行时检索对象**（(d)）。

### 3.2 候选方案对比

| 候选 | 优点 | 缺点 | 适用条件 |
|---|---|---|---|
| **A 不引入**（知识进 prompt 或写进代码） | 零新依赖；确定 | 文本知识塞 prompt 会撑爆预算；更新要发版 | 知识量极小、几乎不变时 |
| **B 结构化知识库**（JSON/YAML，精确查表） | 确定、可测、可版本化、零依赖 | 只适合键值型知识，不适合自由文本 | (b) 类结构化规则 |
| **C SQLite FTS5 全文检索**（零新依赖，`sqlite3` 内建） | 词面检索毫秒级；本地化；支持 BM25 排序；可带 snippet 高亮 | **中文需处理**（见下）；不支持语义近似 | (c) 类事实性文本，数据量千级~百万级 |
| **D 向量 RAG**（embedding + 向量库） | 语义检索强；支持「相似但不含关键词」的查询 | 引入 embedding 链路 + 向量存储 + 网络往返；不可解释；当前数据量负优化 | 数据量上千/上万、且语义近似是刚需时 |
| **E 混合（结构化 + FTS5，向量作后续升级）** | 各取所长；向量可后置 | 两套存储 | 同时有 (b) 与 (c) 两类知识时 |

**中文关键事实（联网核实 2026-09-11）**：SQLite FTS5 默认 `unicode61` 分词器**把连续中文整段当一个 token**——实测「华为云开发者社区」原文入库后查「开发者」命中 **0**。解决方案两条：① `tokenize='trigram'`（SQLite ≥3.34 内建，把内容切成 3 字节重叠片段，任意子串可命中，代价是索引体积大）；② 写入/查询前用 jieba 分词（词间空格分隔，两端切法必须一致）。性能上 FTS5 在万级文本查 ~0.14ms、百万级 ~0.04s，比 `LIKE` 快 15 倍以上。**结论：中文场景用 FTS5 必须显式选 trigram 或接 jieba，不能裸用默认分词器。**

**关于 `ecnu-embedding-small`（bge-m3, 1024 维）与 `ecnu-rerank`**：这两个是平台提供的现成能力，意味着**向量 RAG 的技术门槛在我们这里很低**（无需自建 embedding 服务）。但这恰恰是「诱因」而非「理由」——阿里的判断仍是「先 FTS5，不上向量」（九成查询是词面回查）。我们应把「平台已备 embedding」记为**降低向量升级成本的优势**，而不是「所以现在就该上」。

### 3.3 火烧云预测完整案例

**现状怎么算**（读 `src/lighttrail/tools/weather.py:280-398`）：

1. 拉 Open-Meteo 逐小时预报（云量/高云/能见度/降水概率/风速），窗口取「日落前后 3 小时」。
2. 启发式评分（`weather.py:330-362`）：五个分量加权求和——
   - 云量 `cloud_score`：总量 30-70% 最佳（`:342`）；
   - 高云占比 `high_score`：高云（卷云）被夕阳染红最明显，占比 ≥40% 理想（`:344-345`）；
   - 能见度 ≥20km 满分（`:347`）；降水 ≤20% 满分（`:349`）；风速 ≤25km/h 满分（`:351`）。
   - 权重：云量 0.45、高云 0.25、能见度 0.15、降水 0.10、风 0.05（`:355-361`）。
3. 阈值分级：≥75 高、≥50 中、≥25 偏低、否则低（`:363-370`）。
4. 工具自己标注（`weather.py:397`）：**「经验启发式模型（非气象学精确概率）」**。

**这些判据从哪来**：经验规则（云量 30-70%、高云染红、大风吹散）——**属于 (a) 类**，来源可以是摄影/气象经验或论文，但**当前以代码常量形态存在**，这是正确的（可测、可审计）。

**知识注入点在哪**：若要把「判据的来源依据」讲给用户（如「为什么高云占比 40% 是阈值？因为卷云冰晶对夕阳长波散射最敏感」），这个**解释性文本**属于 (c) 类——是 RAG 的正当场景，但它是**锦上添花**，不影响评分本身。

**加 RAG 能带来什么边际价值**：

1. **解释增强**：在决策卡片的「依据」里附「该判据的方法出处」（如某篇关于卷云光学特性的文献）——提升可信度，属 (d) 类溯源。
2. **新判据来源**：检索气象论文发现「湿度/露点差」也是火烧云因子 → **离线人工**把它加入 `sunset_glow_score` 的算法（(d)→(a) 转化），**这是离线开发动作，不是运行时 RAG**。
3. 不能带来的：RAG **不能**让模型「运行时算出更准的火烧云概率」——概率必须由确定性代码算，RAG 给的只是文本。

**值不值得**：评分算法本身**不值得**引入运行时 RAG（它是 (a) 类，必须是代码）。**解释性溯源值得**——但优先级低于核心功能，且可在 (c) 类知识库建好后再挂。**判断：火烧云场景本期不需要 RAG；长期可作为「知识库 → 决策卡片依据」的一个消费方。**

### 3.4 什么时候才需要向量（可判断的触发条件）

与 v4 §7.5 风格一致：

| 触发条件（满足任一） | 说明 |
|---|---|
| **事实性文本知识（c 类）条目数 > 500，且 FTS5 词面回查命中率 < 70%** | 说明用户查询与文档用词不一致（要语义近似），FTS5 不够 |
| **出现「相似但不含关键词」的检索需求** | 如「找类似这张照片的机位/场景」——词面检索天然做不到 |
| **跨模态检索需求成立** | 如「用一张参考图找相似历史拍摄」（照片反推的延伸） |
| **多语言/多表述归一化成为高频诉求** | 同一概念多种叫法，词面检索覆盖不全 |

未满足上述条件前：**结构化知识库（B）+ SQLite FTS5（C，中文 trigram/jieba）足够**，向量后置。升级路径已设计为「`KnowledgeProvider` 换实现」，不动上层。

### 3.5 现有代码盘点：哪些工具其实已经在做「知识查表」

| 工具/模块 | 现有「知识」形态 | 知识从哪来 | 换知识库形态要动什么 |
|---|---|---|---|
| `tools/exposure.py` `_ND_FILTER_STOPS`（:19-29） | 硬编码 dict | 摄影通识（ND 档位是标准） | 可迁为结构化知识库项，但**档位是恒定标准，留代码更合适**（(a) 类） |
| `tools/exposure.py` `star_shutter_rule`（:114-196） | 算法 + 常量（500 法则分母 500） | 天文/摄影经验 | 留代码（(a)）；参数可配置化即可 |
| `tools/weather.py` `sunset_glow_score`（:330-362） | 加权评分常量 | 摄影/气象经验 | 权重可配置化（(a)）；判据出处文本 → 未来 (c)/(d) 知识库 |
| `tools/site_match.py` `_THEME_EVENT`（:26-34）、`_PENALTY_BY_DELTA`（:37-42） | 硬编码映射表 | 摄影经验（题材↔天象事件、方位容差） | **典型 (b) 类**：可迁为结构化知识表，改一处即可扩展题材 |
| `tools/astronomy.py` `_DIRECTION_NAMES` | 16 方位名表 | 通用标准 | 留代码（恒定标准） |
| `memory/manager.py` `_SUBJECT_KEYWORDS`（:257-271） | 硬编码题材关键词 | 产品设计 | (b) 类，可迁知识表 |
| `memory/profile.py` 档案字段 | 用户数据 | 用户输入 | 是**记忆**不是知识（见 §4） |

**关键观察**：**目前所有「知识」都硬编码在代码里，且都属 (a)/(b) 类**——没有一个 (c) 类运行时文本知识库。这正是「RAG 当前无对象可检」的实证：我们还没有 (c) 类知识的存储。所以**当务之急不是上 RAG，而是先把 (b) 类知识从代码里抽成结构化知识库**（可配置、可扩展、可测试），(c) 类等有真实文本内容时再建。

在 `match_sites` 这类工具的改造上：迁 `_THEME_EVENT`/`_PENALTY_BY_DELTA` 到知识表后，扩展题材只需改 YAML，不改代码——**这与 v4「工具热插拔」目标同向**（工具声明 + 知识数据分离）。

**面试怎么讲（RAG）**

「我先把『知识』分四类：确定性判据、结构化规则、事实性文本、论文方法。前两类不能进 RAG——它们要求可复现可测，必须落成代码或结构化规则；论文是这两类的原料，离线转化，RAG 只负责溯源。只有事实性文本知识才是 RAG 的正当场景。火烧云评分是经验启发式模型，属第一类，判据必须留代码，RAG 只能增强它的『依据出处』解释。中文场景我用 SQLite FTS5，但要显式选 trigram 或接 jieba，默认分词器会把中文整段当一个 token。什么时候上向量？条目超 500 且词面命中率不足、或出现相似检索需求。平台备了 bge-m3 embedding 是优势，但不是现在就上的理由。」

---

## 4. 第四组：D10 挑战——记忆该分场景吗

> 小北洞察：「D10 是不是可以分场景处理不同需求的记忆，比如分开处理用户的设备信息和某些工具所需的专业知识库」。**这个洞察是对的——但准确的形式化不是「记忆分两种」，而是「记忆与知识库是两个不同的东西」。**

### 4.1 形式化区分（对比表）

| 维度 | **记忆（Memory）** | **知识库（Knowledge）** |
|---|---|---|
| **归属** | 关于**这个用户** | 关于**世界与领域** |
| **可见性** | per-user 隔离 | 全局共享 |
| **写入权限** | 可写（用户声明 / 系统沉淀） | 只读（运行时不可写） |
| **写入防线** | double-confirm 防污染 | 版本化 + 人工/离线更新 |
| **检索方式** | 规则命中（地点/题材/时间）或命名空间查 | 精确查表 / FTS5 / （未来）向量 |
| **注入策略** | 分层：档案常驻、事件按需、语义择优 | 按需片段注入（命中才取） |
| **生命周期** | 随使用增长、可遗忘 | 随版本迭代、稳定 |
| **失败代价** | 污染用户画像（要能撤销） | 提供错误知识（要能回滚版本） |
| **更新者** | 用户 / 系统 | 开发者 / 领域专家 |
| **user_id** | **必须带** | **不需要**（全局只读） |

一句话：**记忆是「我知道你」，知识库是「我知道这个世界」。** 混在一起会导致两类问题——要么知识被误当成用户数据隔离（每个用户重复存一份 ND 档位表），要么用户数据被误当成全局知识（A 的偏好注给 B）。

### 4.2 小北的两个例子各归哪类

| 例子 | 归类 | 为什么 |
|---|---|---|
| **器材档案**（`data/profile.json` 的 `camera_body`/`lenses`，`memory/profile.py:23-30`） | **记忆** | 关于**这个用户**的器材（他的 S5M2 + 契卡 14mm）；per-user 隔离；可写（用户改器材）；需注入到所有建议的个性化基线。**这是用户的属性，不是世界知识**——同样的 A7M4 在另一个人那里是另一条记录 |
| **「某些工具所需的专业知识库」**（火烧云判据表、ND 档位表） | **知识库** | 关于**世界**（ND 档位是摄影标准，与用户无关）；全局共享（所有用户看到同一份）；只读（用户不会改 ND 表）；版本化管理。**若误当记忆**：每个用户存一份 ND 表、还能各自篡改 → 荒谬 |

反直觉点：**「器材档案」看似是「知识」（机身参数确实是知识）**，但**归属**决定分类——`camera_body = "S5M2"` 是「用户的器材」，不是「相机百科」。这里区分靠的是**「关于谁」而不是「内容像不像知识」**。而机身的**技术规格**（如 S5M2 的像素间距，用于 NPF 计算）才是**知识**——这部分应该进知识库，用户档案只存「我有一台 S5M2」，运行时用「S5M2」去知识库查规格。**这条边界很关键，见表后的拆解。**

### 4.3 现有 memory/ 四层的归位（重点：semantic 层混了两类）

| 现有层 | 内容（证据） | 归哪类 | 说明 |
|---|---|---|---|
| 短期记忆 | 会话对话历史 | 记忆 | 天然 per-user |
| 档案 `profile`（`memory/profile.py`） | 器材/镜头/偏好/常去地点/水平/favorite_spots | **记忆** | per-user；`camera_body` 存「我有 S5M2」，规格查知识库 |
| 事件 `events`（`memory/events.py`，SQLite） | 历史拍摄事件（时间/地点/条件/结果） | **记忆** | per-user；随使用增长 |
| 语义 `semantic`（`memory/semantic.py:1-9`） | **「结论型经验」**：「偏好低云量+高云为主的晚霞，火烧云成功率约 7 成」 | **记忆**（当前实现） | ⚠️ **这里的定义是「从用户事件提炼的偏好」，属记忆**；但**如果是「从论文提炼的领域知识」，那是知识库** |

**关键判断——这两者现在混在一层里吗？**

**是，语义层这个「口袋」有混装风险。** 现状 `semantic.py` 的 `SemanticEntry` 只有 `content` + `keywords`，**不区分「这句话是关于用户的」还是「关于世界的」**：

- 「用户偏好低云量晚霞」（关于用户）→ **记忆**。
- 「卷云冰晶对夕阳长波散射敏感，故高云占比 40% 为阈值」（关于世界）→ **知识库**。

两者都满足「结论型、关键词命中注入」的形态，所以现状会自然地把领域结论塞进 `semantic.json`（用户目录下）——**这就是小北洞察到的混乱**。**拆法**：按「是否绑定 user_id」判定——绑定用户事件提炼的 → 留 `memory/semantic`（记忆）；来自论文/领域的 → 迁 `knowledge/`（知识库）。

### 4.4 架构落地：两条链路

**推荐：两条独立链路 + 两个 Protocol**（而非把知识塞进记忆抽象）。

```python
# contracts/memory.py —— 记忆（per-user，可写）
class MemoryStore(Protocol):
    def build_injections(self, ctx: RequestContext, intent: str,
                         *, budget: TokenBudget) -> list[MemoryBlock]: ...

# contracts/knowledge.py —— 知识库（全局只读）
@dataclass(frozen=True)
class KnowledgeChunk:
    id: str
    text: str
    source: str          # 出处（论文/标准），供溯源
    version: str

class KnowledgeProvider(Protocol):
    """全局只读知识检索；不接 user_id。"""
    def search(self, query: str, *, k: int = 3, scope: str = "") -> list[KnowledgeChunk]: ...
```

| 方面 | 记忆链路 | 知识库链路 |
|---|---|---|
| 存储 | `data/users/{user_id}/memory/`（JSON/SQLite） | `data/knowledge/`（全局；结构化表 + FTS5/FTS5-trigram 索引） |
| 写入 | 用户声明 / 事件沉淀（double-confirm） | 离线构建脚本（版本化，运行时只读） |
| 注入 | ContextBuilder 第④层（档案常驻+事件按需+语义择优） | 命中才取片段，作为「依据/知识」单独一段（第④层旁或工具内） |
| user_id | 必须带 | 不需要 |

**注入策略差异**：记忆是「按用户分层注入」（有的常驻、有的按需）；知识库是「按查询命中注入全局片段」，注入时**不拼 user_id**——这正是两类必须分开的直接原因。

### 4.5 是否修订 D10

**是，D10 应扩展为「记忆（per-user）+ 知识库（全局）双链路」。**

**D10 修订版表述**：

> **D10（修订）记忆与知识库双链路**：① **记忆**——自研四层规则检索（短期/档案/事件/语义），全部 per-user 隔离，写入 double-confirm 防污染，`MemoryStore` 带 `RequestContext`（本期 `_local`）；② **知识库**（新增）——全局只读，承载「关于世界与领域」的知识，分结构化知识表（键值/规则，精确查表）与事实性文本（SQLite FTS5，中文用 trigram 或 jieba）两种形态，经 `KnowledgeProvider` 检索，运行时不可写、版本化管理。**判定规则：绑定 user_id 的是记忆，全局共享的是知识库。** 原语义层中「从用户事件提炼的偏好」留记忆；「从论文/领域提炼的结论」迁知识库。向量检索作为两者共同的后续升级项（触发条件见 §3.4）。

### 4.6 与第三组 RAG 结论保持一致

知识库的检索形态**直接复用 §3 的选型结论**：结构化知识表（B）+ SQLite FTS5 中文方案（C，trigram/jieba）+ 向量后置（D，触发条件见 §3.4）。即 **D10 修订与 RAG 分析是同一条结论的两个面**：知识库是「容器」，RAG/FTS5/结构化查表是「检索形态」，二者一致。

### 4.7 面试怎么讲

「记忆和知识库是两个不同的东西，不是一种东西分两类。记忆是『我知道你』——per-user、可写、要防污染；知识库是『我知道这个世界』——全局、只读、版本化。判定标准是『绑不绑 user_id』。这个区分能解决一个真实混乱：我原来的语义记忆层是个口袋，『用户偏好低云量晚霞』（记忆）和『卷云冰晶对长波散射敏感』（知识）都会往里塞。拆开之后，用户目录只放记忆，领域知识进全局只读知识库。还有个细节——器材档案是记忆，但『S5M2 的像素间距』是知识：档案存『我有 S5M2』，规格运行时去知识库查。」

---

## 5. 对 v4 的修订建议汇总

> 供小北对照 v4 决策点拍板。每条：原表述 → 建议表述 → 理由。

| # | 对应 v4 | 原表述 | 建议表述 | 理由 |
|---|---|---|---|---|
| **M1** | **D3** | 管线编排形态：自研代码化 DAG | **确定性管线 + 分层兜底**（意图路由器 + 三类分发：已知意图→管线；可规划意图→受控 Plan-Execute；开放对话→ReAct，轮数上限提至 12 并可配护栏） | D3 否掉 LLM 规划的理由只对「已知流程」成立；管线之外的任务（T2 多步规划 / T4 交互确认）现状兜不住，需受控规划通道（§2） |
| **M2** | **D10** | 记忆层：自研四层规则检索 + user_id 命名空间 | **记忆与知识库双链路**：记忆四层（per-user，`MemoryStore`）+ 知识库（全局只读，`KnowledgeProvider`，结构化表 + FTS5 中文方案）；语义层按「绑不绑 user_id」拆分 | 记忆（关于用户）与知识库（关于世界）是两类东西；现状语义层混装两者（§4） |
| **M3** | **新增决策点 D14** | — | **知识库检索形态**：结构化知识表（JSON/YAML/SQLite，精确查表）为 (b) 类；事实性文本用 SQLite FTS5（中文 trigram 或 jieba）；向量 RAG 后置（触发条件见 §3.4）。结论与 RAG 分析同源 | 需要为知识库明确检索技术选型；平台已备 bge-m3/rerank 是降本优势而非立即上向量的理由 |
| **M4** | §3 D5 补充 | 并行取数（已列） | 补充「ReAct 轮数上限 / 管线步数上限 / 规划通道步数上限」作为统一的可配置护栏 | D3 修订后开放域任务增多，需成本/失控防护 |
| **M5** | §7.5 触发条件表 | 已列向量/checkpointer 等触发条件 | 增补「知识库向量化」触发条件（条目 >500 且词面命中 <70% / 相似检索需求 / 跨模态检索 / 多语言归一） | 与 §3.4 一致，避免两处口径不一 |

**新增决策点清单**：
- **D14 知识库检索形态**（随 M3）——推荐：结构化表 + SQLite FTS5（中文 trigram/jieba），向量后置。

---

*本文为 v4 的补充分析。M1/M2 是对既有决策点的修正，M3 是新增决策点——请小北一并拍板。落地批次影响：M1 的规划通道可挂在 B2/B3 之后作为增量批次（复用工具注册表/trace/配额，不新造基础设施）；M2/M3 的知识库链路建议作为 B5 之后的新批次（B6：知识库抽取 + FTS5 索引 + KnowledgeProvider），同样每批结束 pytest 全绿 + ruff 0。*
