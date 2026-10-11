# LightTrail 当前架构

> 2026-10-09，main / 4b04b81 + U1–U3、U4前置、晨暮、SDK生命周期与拍摄前提本地修复。描述当前代码，不代表目标能力全部完成。产品范围见 PRD，执行顺序见路线图，原始证据见 [审查报告](AUDIT-2026-10-06.md)，证据见 [U1](audit/2026-10-06/u1-verification.md)、[U2-1](audit/2026-10-06/u2-1-verification.md)、[U2-2 验收记录](audit/2026-10-06/u2-2-verification.md)。

## 入口与实际调用

CLI 与 FastAPI 经 composition 装配应用服务。开放问答走 PydanticAI AgentRuntime/工具循环；摄影任务由意图/规划入口选择灵感、规划、临场、复盘四管线，采集工具后合成 DecisionCard。React 六页通过 JSON/SSE 消费服务，D2 另有结构化 SpotPlan 与冻结计划导入。

| 模块 | 当前职责与接线 |
|---|---|
| contracts | 工具、请求上下文、计划、卡、收藏、归档、端口与观测契约 |
| domain | 赶场模型/纯规则；knowledge 门面将相机 catalog 查表与 FTS5 检索接到同一端口 |
| tools | 15 个 ToolSpec：天文5、曝光3、天气2、照片2、时间1、记忆1、匹配1 |
| application | SpotPlan、归档回放与规划入口；结构化计划可 needs_data |
| orchestrator | 四管线与路由/规划/合成；U1 文字决策按实际结果核验字段、目标与时效，关键失败或综合异常为保守卡 |
| runtime | PydanticAI 桥、注册、上下文和 Planner/ReAct；已有护栏不证明结论可采信 |
| adapters/llm、llm/router | 模型 API、限流重试、reason参数；能力意图路由，配置/计价仍有平台默认 |
| adapters/maps、datasources | 地图路线/JS代理；HTTP门面 re-export infra.http，赶场同步调用有缺口 |
| memory | 档案、SQLite事件、语义 JSON、Manager；用户路径隔离不是认证 |
| infra | HTTP、配额、验证、脱敏、置信度、TraceRecorder；分数未统计校准 |
| api | FastAPI、SessionManager、SSE、上传；默认 _local，无公网认证；有限 CORS，服务端独立检查写来源及本机 Host/连接地址 |
| frontend | 六页、机位编辑/地图、计划/回放、参数、照片预览；U1 显示资格、日期/时效和缺失原因，旧卡历史化；U3 按稳定 ID 恢复实际卡/消息/计划，历史与显式重查分开 |
| composition | 依赖构造和生产装配，少量可选工厂环境回退；种子依赖源码根路径 |
| tests / evals | 离线回归、合成 cassette、请求事实断言与启发式裁判 |

import-linter 的三条契约当前通过：contracts边界、领域不反向依赖adapters、下层不依赖交互层。AST 提供额外检查，但不是所有架构承诺或全部输入均被穷尽证明。

## 知识、记忆与数据流

用户记忆位于 data/users/{user_id}/memory/，Web默认 _local，会话另存 sessions/。T2状态初始化锁已存在；语义 pending 与原子保存仍需完善。

生产 `build_knowledge` 装配 `GlobalKnowledgeProvider`：`lookup(key, table="camera_specs")` 委托既有 CameraSpecsCatalog，`search` 委托 FTS5；没有新增端口。相机表优先读取配置 data_dir/knowledge，缺文件时回退源码种子，与 FTS seed 一致；应用关闭释放自建索引，注入知识仍由调用方管理。生产 `build_application → create_scope → ToolContext → registry → StarShutterTool` 离线验证 S5M2 自动 NPF；精确机型别名之外不猜规格。ToolContext.datasource 生产传 None，部分工具直接调用HTTP门面。per-user配置/密钥端口属于预留接口，没有多用户产品。

U2-2 计算仍在既有工具与复盘模块：曝光数值不按显示小数位舍入，参数比较按物理量和 0.1% 容差；太阳事件逐个隔离，明确不存在与失败，派生方位/窗口也独立报告。月相使用 Astral 的 0–28 索引，本地午间映射 UTC 日级样本；真实月龄为 null，另提供估算月龄和余弦亮面近似，不承诺瞬时精度。天文框架和现有 NPF 近似算法未替换。

模型上下文可包含器材和事件；图片请求发送图像/EXIF；天气与地图可能发送坐标。本地持久化只描述落盘位置。默认导出移除结构化经纬度，自填文本仍需核对。

## 会话与结果版本（U3）

复用 SessionRecord / SessionManager 和原 sessions/{session_id}.json，不增加数据库或 CaptureEvent。记录新增默认空的 results 数组；SavedResult 包含稳定 UUID、会话内版本号、父版本、保存时间、原请求、实际文本、DecisionCard、SpotPlan、消息历史、管线快照和 trace。Orchestrator 保留实际运行的 last_context，由原 snapshot_context 序列化；不是保存输入后恢复时重新生成。现有普通 save 也合并不可变版本，防止旧入口过时副本覆盖结果；单进程可重入结果锁串行合并，原同目录临时文件与 os.replace 原子落盘保留。

GET /api/sessions 和 /api/sessions/{sid}/results/{rid} 读取已保存数据，按 RequestContext 校验归属并禁止响应缓存；恢复不调用模型、工具或当前收藏，不修改目标日期、获取/评估时间或有效期。旧字段缺失使用原内容映射为固定 legacy ID，缺时间保持未知，不补造早期版本。明确引用不存在的会话/版本返回 404。POST /api/saved-spot-plans 复用原 SpotPlan 生成路径后持久化，原 /api/spot-plans 计算接口保留；D2 使用保存入口。

React useSavedResults 以服务端索引及本地稳定指针恢复；D1/D3 显示完整已存消息与原卡，D2 显示冻结原计划，可带父版本继续询问。完整 SPA 接受历史 ID；新选择链接不会被旧保存选择覆盖。续问载入所选版本历史，显式 requery 不把旧历史当作当前请求，结果追加新版本；恢复请求与页面卸载沿 RequestScope 取消等待和拒绝迟到状态回写。当前仍是单进程本地存储，结果/完整历史随版本增长，未建设分页、压缩、多 worker 并发或跨设备同步；照片入口和赶场尚未全部接入结果版本。详见 [U3 验收](audit/2026-10-07/u3-verification.md)与 [数据边界](DATA-BOUNDARIES.md)。

## LLM、SSE 与可靠性

LLM_CONCURRENCY 默认4、配置1即串行；U2-1 保留单个 BoundedSemaphore，异步排队使用非阻塞获取与可取消等待，不再遗留 acquire 线程。许可仅包单次 SDK 往返，退避在外；SDK max_retries=0、每次模型调用总尝试最多3。LLM_TIMEOUT 以 httpx.Timeout 接到同步/异步客户端，按 loop 缓存连接池而共享唯一限流器；connect/write/pool 使用连接值，read 使用读取值。排队不算发送，阶段超时不是整条管线总墙钟预算。

API 部分同步任务在 worker 执行，再以队列转发 SSE。pump 取消等待协程不能终止已运行线程或确认供应商取消；U2-1 页面明确停止等待边界，D1–D4 卸载 abort、按请求身份拦旧事件/缓存/旧 finally，SSE reader 释放。Runtime与SDK同步门面共用infra.async_runner的进程级持久循环，应用关闭自己的池而不终止共享执行器；异步入口仍使用宿主loop，宿主结束请求后、loop停止前await aclose。ApplicationServices自建客户端由Web lifespan/aclose和CLI finally/close释放，注入资源仍由调用方管理。chat 载入所选版本历史；decide 经 Orchestrator.load_history 把真实消息附入意图、综合及 Planner 的模型请求，明确旧数据仅作语义背景，不能代替本轮工具证据。

工具默认 high/medium 不代表调用成功。U1 文字管线在采集与综合后核验必需结果、字段、请求地点/日期/时区和天气有效时间；实际成功结果重建来源，关键不足固定 wait/low/needs_data。综合异常和 plan 异常不再转入自由对话出发建议；复合 Planner 未核验目标证据时保守提示单独查询临场决策。照片反推/复盘仍是独立链路，未完成同等事实核验。

天气请求 GMT，按供应商偏移解析绝对时间再转换到请求时区；保留缺失值，评分缺失分量不加分。infra.forecast 统一核验获取时间和有效覆盖，orchestrator.evidence 核验目标事实；6 小时新鲜度、72 小时远期为工程护栏。DecisionCard 附资格、缺失、评估/获取时间与有效截止；U1 卡不生成旧 ConfidenceDetail，页面不展示其百分比/区间。首页缓存只作历史回看，存储时间不延长预报有效期。

## 契约、地图与交付

U2-3 的 `api/security.py` 使用既有 ASGI 中间件接口，在路由之前校验本机实际地址、严格 Host 与写请求来源；不信任 Forwarded 头，有限 CORS 不替代授权。文本/JSON 响应及 SSE 完整帧经过服务端凭据过滤，`infra.redact` 登记配置及客户端确切凭据并过滤日志/异常；公开 JS SDK 标识不登记为私密凭据。错误保留状态和请求编号；非重试错误不增加请求次数。

ToolContext 增加由入口提供的单文件能力清单，ApplicationServices 在创建作用域时解析确切路径；工具在读取 EXIF/图像前检查，模型不能授权其他文件。上传临时文件创建后才授予能力，线程 finally 清理；CLI `--photo` 明确授权。页面 `DataNotice` 在发送/选图前展示实际模型主机名及发送范围，M1 展示保留和删除边界；元数据接口不返回凭据。完整数据流见 [数据边界](DATA-BOUNDARIES.md)。

SSE 桥将待发事件保存在锁保护的队列内，宿主循环实时取出并在终态前 flush；迟到投递不重复发事件。chat/decide/reverse 的保存阶段纳入异常边界，所有管线终态在 finally 输出 done；保存失败明确 error，不假装成功。取消等待仍不终止底层线程；U3 的 done 增加可选 saved/result_id，只有落盘成功才确认版本；保存失败为 error → done(saved=false)，不投递成功卡。

OpenAPI→openapi-typescript→generated.ts，本次重生成哈希一致。SpotPlan 冻结归档可分享，选择 URL 只存输入，历史 URL 只存稳定 session/result ID；二者均不含坐标，导入不触网；部分前端直接 /api，跨域API base未完全接线。

JS地图主动加载并转换坐标；赶场仍有WGS84、重名、起点与提前离场缺口。async端点直接调用同步planner，默认地图getter会拒绝事件循环内同步取数。道路几何未完整接线，手工耗时明确标注。

源码checkout回归通过，独立wheel种子打包和生产静态部署未验证。Python>=3.10声明与TaskGroup需要3.11冲突；README推荐3.11+仅说明边界，未改包声明。

## U4 前置计价、输出与综合摘要

生产装配 `Settings → build_ledger/build_client → ChatClient.acall` 使用可配置的谷价、峰价时段/时区/日期例外、七天本地观测窗口与输出限制。同步 chat 复用同一 acall；JSON 修复每次重新调用也携带限制。ECNU 默认字段为 max_tokens=4096，依据官方同接口示例；max_completion_tokens 仅供明确支持的其他兼容部署选择，不自动移除上限。SDK 重试仍为零，每次逻辑调用最多三次底层尝试，许可只包一次往返。length 响应记用量后失败，管线保存 wait/low 的保守结果；思考是否占该上限未经供应商实测，不保证整轮费用硬上限。

QuotaLedger 沿用内存记录与配置注入，不新增计费框架；窗口、告警和降级仅表示本应用已观测估算，非平台余额，重启清空。缓存属于输入子集、reasoning 属于 completion 子集，不重复相加；缺失/冲突 usage、缓存拆分未知、超时/错误/发送后取消的扣费记未知。排队尚未发送的取消不登记一次供应商扣费。桥接层以现有 step trace 保存用量摘要，历史版本保持原始记录，不恢复或伪造平台余额。

`orchestrator/synthesis.py` 在序列化前按目标日期与太阳事件筛选字段；显式拍摄窗口优先选择核心小时并附前后小时，未带新增窗口的旧调用保持原事件邻域规则：一日摘要、最多七条小时样本、完整目标/时区/时效/缺失与太阳窗口；文本字段最多 400 字符、通用列表八条、通用对象 24 字段，省略记录含路径/数量/必要哈希。完整工具数据 JSON 最多 12000 字符，超限停止综合并保守降级；这不限制整个用户/历史 prompt 的大小。完整 raw ctx.data 留在管线与 U3 快照；资格预检和最终 U1 qualify_card 都读取完整实际证据，摘要不替代它。详情与来源见 [前置修复验收](audit/2026-10-07/u4-fix-verification.md)。

2026-10-09 晨／暮最小修复：Intent 增加缺省空值的 solar_event / solar_window，由原请求 grounding 决定，不能被模型自报覆盖。domain/solar_window 根据目标日、固定时区和实际ISO事件解析窗口，贯穿管线、match_sites、完整JSON摘要及资格。蓝调加日出用晨间蓝调开始到晨间黄金窗口结束，晨间不调用日落火烧云评分；未指定晨／暮的蓝调不猜日落，太阳事件不存在/失败保持明确原因。摘要最多7小时、12000字符，完整原始快照仍是资格依据；qualify_card 核验窗口内每个整点的关键天气、绝对日期/时区、事件及模型窗口。组合场景必须覆盖蓝调并跨日出；只晚间数据、缺晨间样本、错误模型晚间窗口不能ready/go，异常回落仍保守。窗口超过3小时、缺事件或不明则降级；5分钟高度角采样和高纬既有近似保留，见[修复验收](audit/2026-10-09/u4-event-fix-verification.md)。

2026-10-09 一轮真实生成、保存及新进程/真实D3关闭重建只读恢复通过，恢复不触网或续期；生成进程清理SDK时RuntimeError的原记录保留。后续[SDK最小离线修复](audit/2026-10-09/u4-sdk-lifecycle-verification.md)经生产Runtime/真实keepalive复现保存后Event loop is closed，再统一同步持久执行器，禁止已停止loop的跨循环关闭兜底；池只在所属loop关闭、成功后移除句柄，异常脱敏报告、失败池保留。新增9、全量640与相关护栏通过，没有真实重跑，也不保证复活已销毁的异步宿主。原模型错误备选与器材假设作为历史事实保留；同类明确前提错误已由后续最小离线约束校验收紧，通用自由文本与现场效用仍有边界，见[原真实记录](audit/2026-10-09/u4-real-verification.md)。

## U4拍摄前提与出发前有效期

2026-10-09按本人本批授权完成[最小离线修复](audit/2026-10-09/u4-shooting-constraints-verification.md)。`domain/shooting_constraints.py`从本轮原文与镜头档案确定通勤/布置分钟、最早出发和器材前提；未知保留null，不从模型、机身、偏好或旧轮次确认。前提在综合前加入完整ctx.data和有界摘要，异常收口重新沿本轮事实确定，不新增公开契约或档案字段。

`orchestrator/constraints.py`在既有完整证据资格校验后约束主方案及独立备选的出发→通勤→布置→开拍间隔，核验三脚架、ND型号及镜头光圈归属。本轮未知覆盖旧档案，变光圈范围不套用全焦段最小f值。违约撤下建议，保留可信目标、来源、原获取/评估/截止时间及可核验行程提示；U1关键数据、晨暮、远期、语义与综合失败护栏仍优先。有限文字表达采用保守处理，不是通用语义证明，不扩展自由对话、复盘或赶场。

原有效期早于计划出发时明确手动更新，不延长新鲜度、不触发调用。历史GET仍原样读取旧版本，不执行新的前提校验改写旧卡；DecisionCardView只读展示原有效截止，早于拍摄窗口时提示出发前手动更新与回看不续期，兼容旧卡缺通勤字段。生产合成新/旧卡重建恢复零模型工具调用和零会话写入；真实React组件SSR及前端构建通过，未重跑浏览器、原真实结果或供应商。

2026-10-11 [最大光圈声明窄修复](audit/2026-10-11/u4-aperture-assertion-verification.md)仅修改本轮规格提取：按原有分句边界汇总同焦段的明确最大光圈值；条件、否定和不确定标记优先，多值冲突保持null，覆盖旧档案。明确且一致的肯定声明仍保留。实际LiveDecisionPipeline综合输入和最终卡均经离线验证，未修改管线装配、档案解析、公开契约或历史恢复，未扩展通用语义解析。本人未出门，无独立现场观察，现场效用无法判读；原真实生成、原卡和技术验收保留，U4未全部完成。

## 后续判断

沿当前模块修事实资格、取消、保存和数值，不做全仓重构。完整CaptureEvent、多用户认证、MCP和多智能体仅为可比较方案，采用前明确用户问题、迁移成本与批准来源。[路线图](AGENT-ROADMAP.md)排期，本文件不维护另一份清单。

## U5独立结果反馈（已实现）

composition装配FeedbackStore/FeedbackService；contracts.feedback区分公开FeedbackSnapshot和内部FeedbackRecord，API只公开last_mutation_id，不暴露请求摘要。数据位于users/{user}/result-feedback/{session}/{result}.json，与memory平级；不修改会话或SavedResult，无迁移。

api.feedback六个JSON端点先校验只读已落盘会话归属；SessionManager.read_saved不进入LRU/get/restore。独立仓库锁内重读、最近mutation去重、CAS和裁决，单文件原子替换涵盖反馈、经验失效和撤回；反馈版本与文档版本独立。源指纹固定v1字段，legacy变源/缺失时GET只读返回派生失效，编辑/确认409、可撤回，坏文件503且不覆盖。

出门与观察独立，非observed结果/范围/结果原因必须null。修改反馈把未撤回经验转needs_review并清确认；重新保存当前反馈版本draft后才能本人确认。经验撤回清正文和确认时间；反馈撤回清经验，墓碑防旧CAS复活。经验仅本地回看，不调用MemoryManager或加入Agent注入，memory_written=false；photo_refs仅空数组。

D3 FeedbackPanel绑定已保存版本，独立RequestScope与目标key隔离迟到响应；状态未知/409须只读核对，失败保留内存草稿，不自动重試或生成。AppShell导航及版本切换有草稿提醒；原卡/有效期不续期。单进程边界及V01–V15证据见[本批验收](audit/2026-10-11/u5-verification.md)。
