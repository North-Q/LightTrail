# U4 前置最小修复验收：计价、输出控制与综合数据完整性

日期：2026-10-07。状态：**U4 准备完成，真实验收待批准**。本批范围由小北明确追加；不进入 U5/U6，不调用真实业务 API，不读取个人照片/坐标，不提交或发布。基线 main / 4b04b81 上有大量未提交的 U1–U4 准备内容，先记录 283 个文件哈希，再增量修改；完整保护清单和门禁见 [检查记录](u4-fix-checks.json)。

## 1. 先复现、再修复的实际问题

初轮新增测试 **3 failed**，反例分别为以下三项；不存在以重跑通过代替修复。

| 问题 | 修复前实际行为 | 修复后数值 / 行为 |
|---|---|---|
| ECNU max 默认价与配额过时 | 10000 未缓存输入 + 2000 输出计 3.8 credits；5h/24h/30d 旧窗口；无峰价 | 谷价 5.4、普通峰价 10.8；七天 20000，本应用观测窗口；缓存、价格、时段、时区与例外可配置 |
| 输出配置未接 SDK | 设置上限 77，真实 SDK 内存请求没有 max_tokens | 生产 Settings→build_client→acall body 包含 max_tokens=77；同步 JSON 修复三次均为自定义 91；HTTP 重试每次保留 93；默认 4096 |
| 长天气 JSON 字符切断 | `json.dumps(ctx.data)[:3000]` 导致残缺 JSON、靠后的目标字段丢失 | 先选字段、目标日期与太阳窗口，再完整序列化；一千小时与字段逆序得到相同 JSON，关键字段/null/缺失原因保留，原对象不变 |

独立回归见 [test_u4_controls.py](../../../tests/test_u4_controls.py)，新增 **25 项**。旧账本窗口测试改为显式注入旧窗口验证配置兼容性；并发记账测试验证当前七天默认，没有削弱并发计数断言。

## 2. 官方依据与计费口径

核实公开 [ECNU 配额与计价](https://developer.ecnu.edu.cn/vitepress/llm/limit.html)：默认多个模型/能力共享七天滚动 20000 credits；北京时间每天 [08:00,18:00) 峰价为两倍，其余为谷价；国庆假期不执行峰价。依据 [2026 假日安排](https://big5.www.gov.cn/gate/big5/www.gov.cn/zhengce/zhengceku/202511/content_7047091.htm)，仅配置 2026-10-01 至 07 例外，不推断未来假期。平台实际令牌额度须本人核对。

| 模型 | 未缓存输入 / M | 缓存输入 / M | 输出含思考 / M | 峰时 |
|---|---:|---:|---:|---:|
| ecnu-plus | 100 | 20 | 400 | ×2 |
| ecnu-max | 300 | 60 | 1200 | ×2 |

费用估算是未缓存输入、缓存输入与 completion 输出总量分别计价；缓存输入是 prompt 子集，reasoning_tokens 是 completion 子集，不额外加进输出总量。此子集解释与当前安装 SDK 的 `completion_usage.py` 一致；真实 ECNU usage 拆分仍须观察。支持 `prompt_tokens_details.cached_tokens` 及 flat `prompt_cache_hit_tokens/prompt_cache_miss_tokens`，冲突/越界记未知，不猜缓存命中率。缺少缓存拆分时按未缓存价估算并明确费用未确认；思考拆分缺失保留 null。

合成例：输入 10000、缓存 4000、输出 2000（其中思考 1500）为谷价 **4.44**；全缓存为 **3.0**，无缓存为 **5.4**。测试覆盖 08:00 含、18:00 不含、23:00 UTC 跨到北京次日及国庆例外。窗口恰满七天或未来记录不算当前水位；自定义价格和额度从生产装配验证。

`QuotaLedger` 明确表示**本应用已观测用量（内存账本，非平台余额）**。已返回 usage 的价格仍是配置估算，不是供应商结算凭证。缺失/无效 usage、缺价格、超时、HTTP 错误及发送后取消登记未知；unknown 存在时 `total_credits=null`，零已观测金额不能解释成确定零费用。排队未发送取消不会登记一次发送。重启清空内存账本，不读取供应商余额；用量 step trace 随既有会话历史保存，历史不重算或续期。额度准入/告警也明确为本地水位，不是平台全部令牌用量。

## 3. 输出控制的实际接线与边界

[ECNU 同接口官方结构化示例](https://developer.ecnu.edu.cn/vitepress/llm/api/structuredoutput.html)明确发送 `max_tokens`。默认 `LLM_MAX_OUTPUT_TOKENS=4096`、`LLM_OUTPUT_LIMIT_FIELD=max_tokens`；自定义正整数和字段选择均可配置，见 [.env.example](../../../.env.example)。SDK 原生也支持 max_completion_tokens，回归验证其能进入内存 body；**没有宣称 ECNU 已确认接受或执行这个替代字段**，真实计划默认不用。

真实 `AsyncOpenAI` + `httpx.MockTransport` 捕获最终 HTTP body，覆盖异步、同步门面、三次 JSON 修复与三次 HTTP 尝试；不是只检查 Settings。同步 chat 复用 acall，SDK `max_retries=0`；每个逻辑模型调用最多三次底层尝试，JSON 自愈最多三次逻辑调用，因此当前 U4 路径组合仍最多九次模型请求。输出限制不会在修复/重试时丢失，也不自动退回无限制。

`finish_reason=length` 即使带合法 `{}` 也明确失败，先记录有效 usage，再拒绝结果；不因 length 触发 HTTP 重试。生产 API/管线反例验证 wait/low、synthesis_failed、实际保存及重建恢复零调用，已返回 usage 只计一次。当前官方材料未明确 max_tokens 在思考模式下是否包括 reasoning；**传输接线已证明，供应商执行与思考限额未证明**。整轮输入、历史、重试、跨峰谷与未知扣费仍可变化，没有整轮 credits 或墙钟硬上限。

## 4. 完整摘要与原始证据

`orchestrator/synthesis.py` 先按字段和目标事件筛选，再 `json.dumps`；不截完整 JSON 字符串。

- 工具数据 JSON 最多 **12000 字符**，正常演练 **4283**，天气失败 **2455**；限制的是工具摘要，整个 user/system/history prompt 另有原上下文预算。
- 保留目标机位/坐标/日期/时区、请求基准、各工具采集与有效期、错误/缺失、目标日关键天气与太阳事件状态/黄金蓝调窗口；日摘要仅目标日，小时按目标日落前后三小时，最多七条，正常六条。
- 单文本最多 400 字符，通用数组八条、对象 24 字段；省略含路径/数量/必要内容哈希，原文从对应 raw 快照追溯。无法完整保留关键原因或总摘要超限时拒绝综合并保守降级，不能伪造可用结果。
- 缺失值保留 null。目标窗口按带时区的绝对时刻选择，字典字段顺序不影响结果。目标区时缺失从请求补齐，已有原目标时区不被替换。
- 完整 `ctx.data` 不修改；资格预检和最终 U1 `qualify_card` 均用完整原结果。三天所有小时原数据继续落盘，摘要不替代历史快照或资格护栏。

[正常最终发送](u4-fix-offline/normal/supplier-requests.json)、[摘要与省略详情](u4-fix-offline/normal/synthesis-summary.json)、[完整原快照](u4-fix-offline/normal/original-result.json)可逐项比较；旧残缺片段仍保留在 [修复前上下文反例](u4-context-check.json)。

## 5. 五种离线重演与预算

[执行脚本](../../../evals/u4_rehearsal.py)经生产 build_client/build_application/API/天气工具/SDK/资格/会话，只替换内存 HTTP。显式 `_env_file=None`、假 Key、LLM_CONCURRENCY=1、空地图凭据；使用新 `.pytest-run-u4/<UUID>` 目录。业务 socket 禁止连接；仅允许 Windows 事件循环内部唤醒。全部输出为合成，不是模型建议质量验收。

| 场景 | 模型 / 天气 / 地图实际尝试 | 已返回用量谷价估算 | 模型未知扣费 | 结果与恢复 |
|---|---|---|---|---|
| 正常 | 1 / 2 / 0 | 5.4 | 0 | ready/go/medium；次日原样、零外调 |
| HTTP 重试 + JSON 修复 | 9 / 6 / 0 | 16.2 | 6 次，总费用未知 | 最终有效；同上 |
| 天气失败 | 1 / 6 / 0 | 5.4 | 0；天气配额扣减另待核实 | wait/low/needs_data；同上 |
| JSON 始终失败 | 3 / 2 / 0 | 16.2 | 0 | wait/low/synthesis_failed；同上 |
| 模型 401 | 1 / 2 / 0 | 未观测有效费用 | 1 次，总费用未知 | 非重试；wait/low；同上 |

每次用量是人为指定的 10000/2000，仅用于计数和计价对照，不能由 prompt 字符数推导真实 token。普通峰时相同返回用量的估算翻倍；九次假定全扣费、同样用量的预留例为 48.6/97.2，仍不是保证。完整逐条 body/状态、SSE、原结果/恢复与落盘哈希见 [五场景汇总](u4-fix-offline/rehearsal-summary.json)。

## 6. 工程门禁与浏览器

| 检查 | 实际结果 |
|---|---|
| 全量 pytest | **616 passed**，新增 25；U1–U3 回归包含在内 |
| smoke / import-linter | **21/21**，**3 kept / 0 broken**（88 files / 244 dependencies） |
| L2 离线 | **12/12 管线 + 10/10 工具**，真实 LLM 0；显式替换 runner 的配置加载，未读 .env |
| 前端 | build 通过；停止/SSE 单测 **8 passed** |
| 真实浏览器组件 | U1、U2-1、U2-3 双视口、U3 及完整 SPA 恢复均通过 |
| Ruff / 文档 / 已有改动 | 相关文件 check/format 与本地链接/基线保护结果见 [检查记录](u4-fix-checks.json) |

浏览器先真实关闭，再重建离线应用：卡片、消息、计划恢复零调用；续问带保存历史、重查保留旧版本、保存失败明确。证据为 [恢复前后指标](u4-fix-browser/restoration-metrics.json)、[后续指标](u4-fix-browser/metrics.json)、[SPA 截图](u4-fix-browser/app-restored.png)与[移动视口](u4-fix-browser/app-restored-mobile.png)。首轮 U3 后端目录不符合既有 fixture 的 .pytest-run-u3 边界，修正启动路径后通过；这属于验收配置错误，不计产品修复。首轮测试缓存 ACL 警告已通过新缓存目录消除，最终全量无警告。

## 7. 人工复核与剩余事项

1. 查看五场景发送账本中的 max_tokens=4096、完整摘要的目标与缺失/省略，以及原结果 pipeline.data 中未裁剪的小时数据。
2. 对照 SDK 截断测试和真实 API 保守结果；对照 retry_and_schema 的已观测 16.2 与六次未知，确认没有把 401 或超时写成确定零费。
3. 离线复跑（输出目录必须新建，保护旧证据）：`.venv\Scripts\python.exe -m evals.u4_rehearsal --evidence docs/audit/2026-10-07/u4-fix-repeat-01`。
4. 页面人工复核沿 [U3 离线步骤](u3-verification.md)：只用新 `.pytest-run-u3` 子目录启动 fixture 与本地前端，生成→关闭浏览器→重建→历史恢复；不点击真实地图，不填写真实个人数据。确认历史标签、原有效期、稳定 ID、续问及重查版本分离。
5. 真正调用前审阅 [更新后的 U4 方案](u4-acceptance-plan.md)第 8 节，填写唯一机位与条件、核对平台额度，明确批准 ECNU/Open-Meteo 的字段和有限尝试范围。当前尚未批准。

保留边界：供应商思考上限/失败扣费/缓存拆分含义与保留删除、真实建议质量及本人现场判读；本地账本不是供应商余额，取消不证明未扣费，本地清理不删除供应商副本。D3 默认请求 +08:00，收藏时区未自动接线；本批不扩大到异地产品设计、整轮硬预算、登录、CaptureEvent 或结果回流。**交付后停止，不标记 U4 完成。**
