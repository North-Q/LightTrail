# U4 首个真实场景验收方案（离线准备，前置修复后）

2026-10-09 执行增补：本方案的一次新生成及必要数据发送ECNU/Open-Meteo已获本人批准，目标固定2026-10-10日出前蓝调至日出后。晨／暮事件错配已最小修复并通过631等离线门禁；随后唯一真实轮次实际模型1/天气1、无重试，concurrency1、max_tokens4096、SDK重试0，上限9/6未变。保存与新进程/真实D3关闭重建只读恢复通过、恢复外调0，不续期；SDK清理异常及模型备选行程问题保留，现场效用待本人判读，U4未全部完成。见[修复记录](../2026-10-09/u4-event-fix-verification.md)、[真实验收](../2026-10-09/u4-real-verification.md)。本轮已经结束，不自动追加查询或换日期，不进入U5/U6。下列2026-10-07火烧云样例、预算及审批表作为历史方案保留，不能把原“待批准”当当前授权状态；08:00由本人明确为从机位开始返程。

状态：**U4 准备完成，真实验收待批准**。日期：2026-10-07。唯一场景：已有一个收藏机位，询问“今晚火烧云是否值得去”。本批只演练合成数据，不代表真实火烧云预测或实际拍摄通过；不进入 U5/U6，不提交或发布。

执行依据：[路线图](../../AGENT-ROADMAP.md)、[数据边界](../../DATA-BOUNDARIES.md)、[U3 验收](u3-verification.md)。本批不读取 `.env`、个人档案、照片或坐标；器材采用已明确的 S5M2、24-105mm F4、70-300mm、契卡 14mm 定焦。真实地点、坐标、时区和出发条件均待本人填写。

## 1. 待填写输入与操作步骤

使用单独的 `LIGHTTRAIL_DATA_DIR`，不复用日常档案目录，不自动挑选、复制收藏或历史。真实阶段须由本人手动提供唯一机位的最小记录，已有收藏的稳定 ID 只在本地使用。当前计算只接受可核验的 WGS84 坐标，不能把 GCJ-02/BD-09 直接标成 WGS84。本次默认不加载地图、不定位、不上传照片、不查询路线；本人填写单程时间、布置时间与返程限制。

| 输入 | 待本人填写 / 核对 |
|---|---|
| 机位名称及 WGS84 纬度、经度 | 留空；确认允许将坐标发送给天气和模型 |
| 坐标来源、朝向、遮挡、开放与安全条件 | 留空；无法核实时标未知，不由模型猜测 |
| 目标本地日期、实际请求时刻、时区偏移 | 留空；中国大陆通常 +08:00，本次仍须确认；当前 Web RequestContext 默认 +08:00，不自动读取收藏时区 |
| 单程通勤、布置分钟数、最早出发、最晚返程 | 留空；不需要家庭地址或出发坐标 |
| 可接受成本与无收获容忍度、雨风安全阈值 | 留空；它们是本人条件，不是工具已核验事实 |
| 携带镜头、三脚架及 ND 是否可用 | 从上述已知器材中本人选择；不要假定三脚架或 ND 已拥有 |
| 令牌当前可用额度、费用容忍及第 7 节剩余边界 | 由本人在平台查看；不粘贴 Key |

完整操作顺序如下。真实执行命令只在批准后启用；现在交付的是请求样例，不执行供应商请求。

1. 批准后新建隔离目录、仅录入本人确认的一处收藏与必要器材。启动本机服务，限制 loopback，配置 `LLM_CONCURRENCY=1`。确认 `LLM_MODEL=ecnu-plus`、`LLM_MODEL_REASON=ecnu-max`、深推理 thinking 开启；本场景命中规则，不发送意图分类请求。凭据仅放服务端配置，不截图、导出或回显。
2. 从 D3 进入新会话，保持地图关闭；核对页面发送说明和唯一收藏。输入“今晚火烧云是否值得去”。如需要让模型考虑出发条件，在同一次输入末尾附本人确认的条件，例如“单程 __ 分钟，布置 __ 分钟，最早 __ 出发，最晚 __ 返程，容忍度 __”；不要加入“复合任务/多步骤/赶场”等会切换路径的要求。无额外条件时模型不能替本人算机会成本。
3. 本机发送 `POST /api/decide`，新查询不带历史 ID。服务端按实际请求时间及 RequestContext 时区确定今晚，按唯一有效收藏确定机位；本地天文计算、两个天气取数及本地机位匹配后，发送综合模型请求。返回 SSE 的 queued/step、card、done；不以 step 个数判断最终成功，须保留完整响应及 card/done。
4. 核对卡片目标日期、地点、时区、获取与有效时间、缺失原因、窗口、行动建议和器材。不用“go”本身作为成功标准；模型拒绝、数据不足、建议不出门都可能是正确结果。缺少可执行窗口或本人条件时记录不通过/待补充，不追加隐式查询。
5. 仅 `done.saved=true` 且含 session/result ID 才确认保存。记录原始卡片、消息和本地管线快照。保存失败不能记录为成功；停止等待也不保证底层已停或未计费。
6. 关闭页面、停止应用，使用同一隔离数据目录重启，按保存的稳定 ID 打开历史。对比原结果内容和时间，确认历史/待更新标签；恢复不得重新取数或续期。过期以后仍只能回看。显式“重新查询原请求（新版本）”会再次发送，须另算预算；本轮最小真实批准默认只含一次新生成及只读恢复，不含续问、重查、地图、照片。
7. 本人决定是否出门并判读结果，用[人工记录表](u4-manual-record.md)记录。没有出门时仍可以评价可执行性；不能据此声称命中实景。记录留在文档，不写拍摄事件、训练记忆或 U5 回流接口。

## 2. 本机请求、返回与落盘样例

以下全部为人工合成，不能当作真实机位。实际样例原文见[本机输入](u4-fix-offline/normal/local-inputs.json)。档案 PUT 与收藏 POST 仅向本机发送，写入本身不调用供应商。

```http
PUT /api/profile
Content-Type: application/json

{"camera_body":"松下 S5M2","lenses":["24-105mm F4","70-300mm","契卡 14mm 定焦"],"preferences":["风光","火烧云"]}

POST /api/favorite-spots
Content-Type: application/json

{"name":"U4合成西岸","latitude":31.0,"longitude":121.0,"coordinate_system":"WGS84","tz_offset":"+08:00","subject_types":["火烧云"],"source":"人工合成，非个人机位"}

POST /api/decide
Content-Type: application/json

{"request":"今晚火烧云是否值得去"}
```

样例实际卡：2026-10-07，U4合成西岸，`data_status=ready / verdict=go / confidence=medium`；本地日落 17:34（+08:00）。Fake 原输出 high，经实际工具资格重建后为 medium；评分 100 是人工构造理想字段的启发式分数，不是成功概率。Fake 窗口 17:00–18:00 是演练输出，不能用于真实出行。[完整 SSE](u4-fix-offline/normal/response.sse.txt)、[实际保存结果](u4-fix-offline/normal/original-result.json)可以核对 card/done 和实际字段。示意终态：

```json
{"type":"done","saved":true,"session_id":"<实际稳定会话ID>","result_id":"<实际稳定结果ID>","text":"<实际中文答复>"}
```

只读恢复：`GET /api/sessions/{session_id}/results/{result_id}`；会话详情和首页索引分别为 `GET /api/sessions/{session_id}`、`GET /api/sessions`。完整 SPA 链接为 `/#/d3?session=<ID>&result=<ID>`，链接不含坐标。恢复读取原对象，不替换为模型再次生成的结果。

本地保存范围：隔离目录 `users/_local/memory/profile.json` 保存器材/收藏；初始化会生成 `events.db`，本批不增加拍摄事件；迁移可能生成 `profile.pre-s1-2.json.bak`；`knowledge/knowledge.db` 是本地知识索引；`sessions/{ID}.json` 保存实际请求、消息、卡、工具结果、trace、版本及时间，可含精确坐标。浏览器保存稳定指针及既有摘要缓存。人工记录、截图和导出文件也是独立副本。实际演练文件哈希在各场景 summary 中；清理须分别核对，删除收藏不删历史，删除本地数据不删供应商副本，现无自动到期删除。

## 3. 按接收方审阅最终发送内容

| 接收方 / 用途 | 实际字段与返回 | 本场景边界 |
|---|---|---|
| ECNU `chat.ecnu.edu.cn` / 综合决策 | model、messages、temperature、thinking；messages 含系统角色/行为/工具能力说明、器材及偏好、当前请求、已确定机位与日期、天气和天文结果片段、评分、卡片格式及 trace 摘要。返回助手文本，供应商 usage 和思考摘要可能进入 trace | 新会话无旧历史，隔离目录无既有事件/语义记忆。所有候选收藏可能进入管线，因此只录入本人批准的一处。续问会扩大到保存历史，默认未批准 |
| Open-Meteo `api.open-meteo.com` / 天气与火烧云评分输入 | GET 参数 latitude、longitude、hourly 六字段、timezone=GMT、forecast_days；两个请求分别 7 天和 3 天。返回小时预报及偏移等元数据 | 日期不作为 date 字段发出：按今天起覆盖取数后，本地筛选目标日。姓名、机位名、档案、照片、历史和出发条件不发送给天气服务 |
| 高德 / 百度 / 地理编码 | **本次 0 调用，无发送字段** | 已有确认坐标，路线非必需；通勤由本人给时长。地图默认关闭。若另批准地图，需要新列起终点/坐标转换/SDK 请求与费用，不能沿用本方案预算 |
| Astral、match_sites、知识检索 | 本地计算坐标/日期/太阳事件、机位匹配、知识查询 | 不是网络供应商请求；不会触发新天气接口，但计算结果可能随后进入模型内容 |

以下为本批**真实 SDK 最终 HTTP body**：[完整发送账本](u4-fix-offline/normal/supplier-requests.json)与[实际综合 JSON](u4-fix-offline/normal/synthesis-summary.json)。模型地址仅演练时替换为不可用本机端口，认证头隐藏。实际顶层字段为 messages/model/temperature/thinking/max_tokens；temperature=0.3、thinking={"type":"enabled"}、max_tokens=4096，无 tools/reasoning_effort/stream。综合工具 JSON 正常 4283 字符、天气失败 2455 字符，完整可解析；按目标日期、太阳窗口保留必要字段并附省略清单，最多 12000 字符，完整 raw 留本地快照与 U1 资格校验。

两个天气 URL 实际如下（只展示，不执行）：

```text
https://api.open-meteo.com/v1/forecast?latitude=31.0&longitude=121.0&hourly=cloud_cover,cloud_cover_high,cloud_cover_low,visibility,precipitation_probability,wind_speed_10m&timezone=GMT&forecast_days=7
https://api.open-meteo.com/v1/forecast?latitude=31.0&longitude=121.0&hourly=cloud_cover,cloud_cover_high,cloud_cover_low,visibility,precipitation_probability,wind_speed_10m&timezone=GMT&forecast_days=3
```

weather User-Agent 为 `lighttrail/0.1`，无需 Key。真实供应商还会收到源网络地址、认证关联身份、连接时间及 SDK/协议头；它们不是本地 JSON 字段。天气字段与 WGS84/GMT 语义依据 [Open-Meteo 官方文档](https://open-meteo.com/en/docs)。ECNU 官方说明模型处理在校内服务器，见[模型介绍](https://developer.ecnu.edu.cn/vitepress/llm/model.html)；这不等于零日志或即时删除。ECNU、Open-Meteo 的确切保留期限、训练用途、删除能力尚待核实，不承诺本地清理会撤回发送。

## 4. 调用数量、重试与费用依据

这是**这句固定请求、规则命中、唯一有效机位、无 Planner/ReAct 的预算**。规则与天文不占模型调用。正常综合 1 次；JSON 校验最多再请求 2 次，每次是新的逻辑模型调用且可重新消耗 token；SDK 自身 `max_retries=0`，适配器每次首次加两次重试。429/500/502/503/504 及相应连接/超时错误可重试；模型 400/401 等不重试。两次天气 GET 各最多 3 次底层尝试，HTTP 层是唯一重试器，没有工具级再次取数循环。

| 情形 | 模型 HTTP 尝试 | 天气 HTTP 尝试 | 地图 | 演练行为 |
|---|---:|---:|---:|---|
| 正常输出与天气 | 1 | 2 | 0 | 保存 ready / go / medium |
| 三次 JSON 输出，每次前两次可重试 HTTP 故障，最后有效 | **9 = 3 × 3** | **6 = 2 × 3** | 0 | 上限组合被实际 SDK/天气传输计数验证 |
| 天气全部失败，模型仍 go/high | 1（若还发生 JSON/传输问题，可至 9） | 6 | 0 | needs_data / wait / low，明确缺失 |
| 天气正常，模型三次非法 JSON | 3（每轮叠加传输重试可至 9） | 2 | 0 | synthesis_failed，wait/low；天气可 ready，但综合仍失败 |
| 模型 401 | 1 | 2（采集发生在综合之前） | 0 | 不重复认证错误，综合失败保守卡 |
| 同目录重启后只读历史 | **0** | **0** | **0** | 原内容、时间和有效期完全相同 |

首轮已选路径的保守发送数量上限为模型 9 + 天气 6 = **15 次**，不含本机 GET/POST。数据采集步骤四个（含本地 sun_times、match_sites）不是四个网络请求。不同措辞若触发 LLM 意图分类，可能再有至多 9 次模型发送；复合 Planner、自由问答、多机位路线及追加续问完全不在本预算内。重查是新的整轮预算，不能误认为只读恢复。实际 ECNU 执行设置 `LLM_CONCURRENCY=1`，且一次只提交一个页面请求；天气工具仍并发取数，不由该配置限流。

超时：模型 connect/write/pool 默认 30 秒，read 120 秒；天气 15 秒。退避在许可之外。排队、分阶段超时、思考、JSON 修复和工具并发导致**没有已实现的整轮墙钟截止**。页面停止仅停止等待，worker 可能继续并保存。发送数量有界不等于 credits 或总时间硬上限。

ECNU 核对日期 2026-10-07：[官方配额与计价](https://developer.ecnu.edu.cn/vitepress/llm/limit.html)为默认每 7 天滚动 20000 credits、多个能力共享。本人实际令牌额度须在平台核对；没有把旧 5h/日/月默认值当作现行官方依据。`ecnu-max` 谷价每百万 token：未缓存输入 300、缓存输入 60、输出/思考 1200；峰价乘 2。北京时间每天 8–18 时为峰时，官方注明国庆假期例外，执行日适用时段仍须核对。`ecnu-plus` 只有换路径时才可能使用，其谷价对应 100/20/400，不自动为本场景额外计一轮。

按每次返回的有效 usage 估算 `f × (300 × 未缓存输入 + 60 × 缓存输入 + 1200 × completion输出总量) / 1,000,000`；缓存属于 prompt 输入子集、reasoning 属于 completion 子集，不重复相加。合成无缓存 10000 输入 + 2000 输出（其中 1500 思考）为谷 5.4 / 峰 10.8 credits；4000 输入缓存时为谷 4.44 / 峰 8.88，全缓存为谷 3.0 / 峰 6.0。正常已观测谷价 5.4；格式失败的三次成功返回合计 16.2；组合重试已观测三次返回 16.2，另六次失败扣费未知，不能记总费 16.2；401 一次未返回 usage，总费未知。若假设九次均计费且同样用量，示例预留谷 48.6 / 峰 97.2，仍不是硬上限。max_tokens=4096 已进入每个请求，但思考是否占限额及服务端严格执行尚未确认；真实输入 token、缓存拆分与失败扣费也须核验。本应用内存账本不是平台余额，重启清空；缺失 usage、超时、异常不记确定零费。

Open-Meteo [官方价格/限流](https://open-meteo.com/en/pricing)：非商业免费接口免 Key，有分钟/小时/日/月限制（600 / 5000 / 10000 / 300000）。按官方计数说明，单地点、六变量、3/7 天各通常为一个调用单位，因此本场景正常约 2、重试最多 6 请求单位；错误是否计入额度未确认。免费接口不保证可用性；商业使用需另行订阅，此方案不购买套餐或扩展服务。保留 Open-Meteo 来源标注。

## 5. 可观察验收标准

| 检查 | 通过标准 / 应记录证据 |
|---|---|
| 日期与机位 | target_date 等于请求时刻在已确认偏移下的今晚；locations 仅为本人确认机位；pipeline 目标坐标与手动输入一致。跨午夜须重新确认，不能沿用之前“今晚” |
| 数据时效 | 能查 evaluated_at、updated_at、valid_until，以及天气获取时间、有效覆盖和时区。获取时间不冒充供应商模型运行时间；后者未提供时标未知。当前 6 小时新鲜度和 72 小时远期限制是工程护栏 |
| 缺失诚实 | 关键天气失败/缺字段/过期时 wait/low/needs_data，有缺失说明；模型 go/high 不能覆盖失败；无未经校准的成功概率或统计区间 |
| 行动可执行 | 明确去/等/不专程、观察窗口和最晚决定点；本人用“到场时间减单程与布置时间”检查最晚出发，核对开放/遮挡/雨风与返程。未知交通不装成路线工具测量；器材只从本人可用清单选择 |
| 效用与正确性 | 本人能用建议作决策；记录机会成本、错误与数据偏差，不把预测没中等同所有流程失败，也不把正常卡等同有用 |
| 结束与保存 | card/done 完整；saved=true 与稳定 ID 可读；error 或 saved=false 明确失败；完整 SSE 可供核查 |
| 恢复与续期 | 关闭及同目录重建后原卡/消息/快照完全相同；过期显示历史/待更新；恢复调用增量为零；只有明确重查可新增版本且旧版可读 |

## 6. 本批离线演练与复核

执行脚本：[evals/u4_rehearsal.py](../../../evals/u4_rehearsal.py)。生产 `build_application → FastAPI decide → Orchestrator → 实际天气工具/HTTP 重试 → ChatClient/OpenAI SDK → U1 资格 → SessionManager`；只替换底层内存 HTTP，天文和机位匹配仍为真实本地计算，没有手写假 API 卡。显式关闭地图凭据、忽略 `.env`，所有数据写专用 `.pytest-run-u4/<UUID>/`。不运行真实服务器或外网请求；Windows 事件循环本机 socketpair 允许，业务连接被拒绝，HTTP 传输为 MockTransport。

五个场景均通过，原始记录见[汇总](u4-fix-offline/rehearsal-summary.json)：

- [正常](u4-fix-offline/normal/summary.json)：输入、最终请求、完整 SSE、原快照与[次日恢复](u4-fix-offline/normal/restored-result.json)。
- [组合重试](u4-fix-offline/retry_and_schema/supplier-requests.json)：9 次模型、6 次天气逐条状态和实际 body。
- [天气失败](u4-fix-offline/weather_failed/original-result.json)：模型乐观输出经过真实资格护栏后的缺失说明。
- [格式失败](u4-fix-offline/schema_failed/summary.json)、[401 不重试](u4-fix-offline/non_retry_error/summary.json)。

每次关闭服务和客户端后重建 FastAPI/应用/SessionManager，使用同一隔离目录；次日 GET 等于原对象，供应商尝试增量为零。另重跑 U1/U2-1/U2-3 浏览器与 U3 真实组件/完整 SPA，关闭浏览器和重建应用后卡片/消息/计划恢复零调用；本批浏览器证据见 [恢复指标](u4-fix-browser/restoration-metrics.json)。

本批全量 pytest **616 passed**（新增 25），smoke **21/21**、分层 **3 kept / 0 broken**、L2 **12/12 + 10/10，零真实 LLM**、前端 build/停止单测和 U1–U3 浏览器通过；相关 Ruff check/format 通过。原始失败反例、保存的 SDK 请求、边界、保护与文档检查见 [前置修复验收](u4-fix-verification.md)。旧 U4 准备证据保留在 u4-offline-final，历史断裂 JSON 反例保留在 u4-context-check.json，不将其解释为修复后状态。

可重复运行（始终离线，输出目录必须为新的名称以保护证据）：

```powershell
cd D:\Project\LightTrail
.venv\Scripts\python.exe -m evals.u4_rehearsal --evidence docs/audit/2026-10-07/u4-offline-repeat-01
```

## 7. 已修复问题与仍待确认的边界

1. **前置修复已完成**：旧计价 3.8 → 无缓存谷价 5.4、普通峰时 10.8；默认窗口改为七天 20000，缓存和峰谷可配置，2026-10-01 至 07 明确为谷价例外，不外推下一年。来源见 [ECNU 规则](https://developer.ecnu.edu.cn/vitepress/llm/limit.html)与 [2026 假日安排](https://big5.www.gov.cn/gate/big5/www.gov.cn/zhengce/zhengceku/202511/content_7047091.htm)。输出上限贯穿实际 SDK body；长工具 JSON 不再字符截断，字段逆序和一千小时合成数据回归通过。旧反例保留，见 [修复验收](u4-fix-verification.md)。
2. **费用与思考边界**：[官方结构化示例](https://developer.ecnu.edu.cn/vitepress/llm/api/structuredoutput.html)使用同一 Chat Completions 的 max_tokens，支持字段有公开依据；文档未明确思考模式的 token 上限包含关系。max_completion_tokens 的 ECNU 接受行为未确认，本轮默认不用。限制有传输证据，供应商执行尚无真实证据；length 保守降级通过。未知扣费、本应用外使用、重启清空及峰谷跨界结算使本地账本不能证明平台余额或整轮硬上限。若必须严格 credits 封顶，应继续暂停真实调用。
3. **时区与现场条件**：D3 无独立时区字段、请求默认 +08:00，收藏时区不自动成为请求偏移。大陆本场景可明确核对；异地须调整方案。没有地形遮挡、实测天气、气象光路或路线证据，评分不能替代本人条件。

离线只证明接线、计数、完整性、降级、保存和恢复；真实可达性、usage 含义、扣费与保留、建议质量、现场天气及本人效用仍待批准后的真实调用和判读。

## 8. 最小批准清单（尚未批准）

本人只需确认：**①一处机位的名称/WGS84 坐标/来源/+08:00 时区及出发条件；②允许必要数据与器材发送给 ECNU 和 Open-Meteo，地图/照片关闭；③本人核对平台额度并接受单次新生成可能模型最多 9 / 天气最多 6 尝试、输出上限尚非费用硬上限与未知扣费边界。** 批准拟仅含一次新生成及零外调历史恢复；重查、续问、地图、照片另行确认。完成本批后停止，不把 U4 标为完成，不进入 U5/U6。
