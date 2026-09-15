# LightTrail 开发日志

## 2026-09-14（B4 契约单一真源 + 前端重接：B4-1 ~ B4-5 全部交付，停闸门 4）

> 批次目标（v4 §6 B4 / §3 D8）：pydantic 单一真源 → OpenAPI → openapi-typescript 生成；
> 删前端手抄类型与全部伪造数据（置信度常量表 / 中文正则猜结论 / 工具 JSON 正则解析）。

### B4-1 OpenAPI→TS 生成流水线 — 已提交（b8ccdca）

- 新增契约导出入口 `api/openapi_export.py`（`python -m lighttrail.api.openapi_export [out]`，sort_keys 稳定输出）；
- `frontend/scripts/dump-openapi.mjs` + `npm run gen:api`（导出 → openapi-typescript → `src/api/generated.ts`）；
  中间产物 `frontend/.openapi.json` 入库忽略，入库物只有 generated.ts（808 行，含 SSE 8 事件 + DecisionCard/ProfilePayload）；
- SSE 事件负载显式建模（`contracts/events.py`：8 个 *Event + `SSEEventPayload` 判别联合），三个流式端点用
  `responses={200: {"model": SSEEventPayload}}` + `SSEResponse`（OpenAPI 标 `text/event-stream`）；
- **OpenAPI 兜底 schema 清理**：FastAPI 对非 JSONResponse 的 response_class 先写 `{"type": "string"}`，
  再与 `responses` 里的模型 schema 深合并 → `oneOf` 与 `type` 并存（自相矛盾且污染生成物）；
  `api/app.py` 出口清理该键，生成物是干净的判别联合（`tests/test_api_contract.py` 有断言）；
- 档案端点（GET/PUT `/api/profile`）加 `response_model=ProfilePayload`，前端不再手抄档案类型；
- **门禁修复（如实记录偏差）**：B3 汇报的「lint-imports 全绿」在干净环境（`git archive HEAD` + `--no-cache`）
  复跑为 **2 kept / 1 broken**——B3-5 把客户端实现迁入 `adapters/llm/client.py` 后，旧路径
  `lighttrail.llm.client` 成了 re-export shim，使 orchestrator/tools 传递依赖 adapters。按「shim 豁免带
  TODO + 删除批次」规则补 `ignore_imports`（TODO(B5-4)），复跑 **3 kept / 0 broken**；
- 新用例 `tests/test_api_contract.py`（6 例）：帧字段一致 / 判别联合 / OpenAPI 覆盖契约模型 /
  SSE schema 无兜底 type / 档案端点用契约模型。

### B4-2 前端删手抄类型，全面接生成物 — 已提交（e1a035d）

- `frontend/src/api/events.ts` 收敛为契约门面：类型一律 `components["schemas"][…]`；`SSEEvent` 直接取
  `/api/chat` 200 响应的 `text/event-stream` schema（事件增删自动跟随，不靠人工同步）；
- `client.ts` 删本地 `SessionPayload`，改用 `SessionDetail`；M1Page 的本地 `Profile` 接口改为
  `ProfilePayload` 的映射类型派生（只做「可空 → 非空」归一，不重复字段）；
- 验收：`npx tsc -b --force` 0 错、`npm run build` 通过。

### B4-3 D3Page 去伪造数据（置信度常量表 + verdictFrom 正则） — 已提交（4c69860）

- **契约增量**：`DecisionCard.verdict`（三态结论，模型结构化输出）+ `confidence_detail`
  （`ConfidenceDetail`：level/score/low/high/basis/三档计数）；
- **规则推导进代码**：`infra/confidence.confidence_detail()`——主值 = 依据级别加权平均
  （high 0.85 / medium 0.6 / low 0.35），区间半宽随依据条数收窄（26−2×条数，夹 6–26），
  无依据时退回等级兜底带并在 basis 如实说明；`orchestrator/pipelines.py` 用模板方法
  （`Pipeline.run` → `finalize_card`）统一收口，反推路径（`run_reverse`）同样补全；
- **前端**：删 `confidenceMath`（78/58/34 常量表）与 `verdictFrom`（中文正则猜 go/wait/risk），
  改读卡片字段；「可能性评估」概率条改为**依据构成**（high/medium/low 真实条数 + 占比），
  小字改为「由后端按来源级别规则推导（非模型自评，也不是概率估计）」；HomePage 环图同样改读
  `confidence_detail.score`（顺带消灭第二份常量表）；
- **遗留（记档）**：`D3Page.firstTime` 仍从 `time_window` 文本取首个时刻做倒计时（展示层解析真实字段，
  非伪造数据；结构化时间窗待后续契约增补）。

### B4-4 tool_result 带结构化数据，D2Page 去正则解析 — 已提交（cc58c93）

- 后端：`infra/trace.record_tool` 增 `结果数据`（结构化 dict，非 JSON 结果不落键），出口掩码改
  **递归**（容器内文本同样 redact）；`api/events.map_trace_event` 把它映射为 `tool_result.data`
  （无结构化结果则帧里不出现该键）；
- 前端 D2Page：删 `parseTime` 与四处 `String.match` 正则解析，改读 `event.data` 真实字段
  （日出 / 日落 / 月相名称 / 月光影响建议 / 可见窗口首段）；
- 用例：`tests/test_api_events.py`（data 透传 + 端到端 decide 帧带 data）、
  `tests/test_trace_security.py`（结构化结果入库 + 嵌套掩码 + 非 JSON 结果不落键）。

### B4-5 B4 收口（CI 漂移门禁 + 出口检查） — 已提交（docs 收口，见本提交）

- 门禁写入长期目标清单：**B4 起 `npm run gen:api && git diff --exit-code`**（契约漂移即红）；
- **漂移门禁实测**：`gen:api` 幂等（重跑 diff=0）；手工往 generated.ts 塞一行再 `git diff --exit-code`
  → 退出码 1（会被拦下）；重跑 `gen:api` 复原 → 退出码 0；
- 出口检查单：
  - [x] `npm run gen:api` 一键跑通；重跑 `git diff --exit-code` = 0
  - [x] `generated.ts` 覆盖 SSE 8 事件 + DecisionCard/ConfidenceDetail/ProfilePayload
  - [x] 前端无硬编码置信度表、无中文正则猜结论、无工具摘要正则解析（grep 复查）
  - [x] `npm run build` 通过；pytest **323** 全绿；ruff 0；`lint-imports` 3 kept / 0 broken；smoke 21 项
  - [x] Web 实跑：uvicorn 启动 + `GET /openapi.json` 200（含 CardEvent 等契约模型）
- **偏差/风险**：npm 全局缓存目录在沙箱外（EPERM），本机安装需 `npm install --cache .npm-cache`
  （前端已有 `.npm-cache/` 且已 gitignore）；新依赖 `openapi-typescript@7.13.0` 仅进 devDependencies；
  生成物中文字段/描述直接来自 pydantic docstring（改契约即同时改生成物，属预期）。
- **README 仅同步本批相关两行**（前端契约生成说明 + 测试数 323）；`agent/` 目录、E 时代表述等完整基线同步留 B5-5（文档基线=实际 HEAD）。
- **前端去伪造数据静态门禁（闸门 4 证据加固）**：新增 `tests/test_frontend_contract.py`（6 例）——
  生成物就位、无第二份手抄契约（DecisionCard / SSE 事件 interface）、api 层类型必须从
  `components[...]`/`operations[...]` 派生、已删的伪造辅助（confidenceMath/verdictFrom/parseTime/
  SessionPayload）不得回归、D3Page 读卡片字段、D2Page 读结构化 `data` 且无正则解析；
  **实测有效**：往 `frontend/src/` 放一个含 `confidenceMath` 的探针文件 → 该用例红；
  删掉探针 → 绿（不是不会失败的摆设）。
- **真实联调（2026-09-14，`%TEMP%\lt_live_b4.py`，临时件不入库）**：
  - CLI `--pipeline "今晚上海火烧云值得冲吗？"` rc=0 出卡（结论 + 时间窗 + 机位 + 参数 + 依据 + 备选）；
  - Web `/api/decide` 事件序列 `queued → step×6 → (tool_call/tool_result)×3 → step×2 → card → done`，
    **3/3 tool_result 带结构化 data**（如 moon_phase 的 月相名称/月龄/照亮比例）；
  - 真实卡片 `verdict="go"`（模型结构化输出）、`confidence_detail={level: medium, score: 60,
    low: 44, high: 76, basis: 按 5 条依据的来源级别加权（确定性 high 1 条 / 外部或启发式 4 条）,
    计数 1/3/1}`——**计数与 evidence 5 条一致**（可解释性对得上，M2）；
  - Web `/api/chat`（"现在几点？"）`queued → tool_call/tool_result → token → done` 正常。
  - 联调后测试规模 329（B4 新增 23 例）。
- **D3 真实渲染自检（B4-3 验收项「真实 /api/decide 跑通渲染」）**：无头 Chrome 打开 `npm run dev` 的
  `#/d3` → 真实提问 → 新契约字段全部上屏：三态结论按钮 active =「去」（读 `card.verdict`，不再正则猜）、
  置信度主值 `60%` + 区间 `42–78 · medium`、依据小字「按 4 条依据的来源级别加权（确定性 high 1 条 /
  外部或启发式 3 条）」、依据构成条 1/2/1 条（取 `confidence_detail` 计数，与卡片 evidence 4 条一致）、
  倒计时在走（仍取 `time_window` 文本，见 B4-3 遗留）。截图：桌面 1440×1000 与移动 390×844
  （`%TEMP%\lt_d3_render_desktop.png` / `lt_d3_render_mobile.png`）。
- **⚠ 环境坑（已排除，务必记档）**：首轮浏览器自检拿到的卡片**没有** verdict/confidence_detail——
  排查发现 **8765 端口上有一个 2026-09-09 20:05 启动的残留后端进程**（python，workbuddy 运行时，
  PID 13652），代码是 9 月 9 日的旧版本——它的 `/openapi.json` 里连 `CardEvent`/`ConfidenceDetail`
  都没有；Vite 按 `vite.config.ts` 把 `/api` 静默转发给它，于是前端看起来像「B4 没生效」。
  结束该进程后同一套自检全绿。**教训**：本地复现前先确认 8765 上后端的版本
  （`/openapi.json` 是否含 `CardEvent`/`ConfidenceDetail`），别把残留进程的输出当本次改动的问题；
  自检脚本已加「端口占用即中止 + 契约版本校验」两道前置。
- **前端 spec 同步实际契约（B4 引入的文档漂移修复）**：`docs/design/FRONTEND-SPEC.md` 新增 §0「v1.1 增量」
  （契约类型不手抄 / 置信度来自后端规则推导 / 三态读 `verdict` / 工具结果读 `tool_result.data` / 概率条改为依据构成），
  重写 §4.2/§4.3、更正 §2 IA 行与 §5 映射表三行、补 §6 铁律①数值来源，并新增 §9「契约生成与门禁」；v1.0 正文保留，冲突以 §0 为准。
- **E8 评估回归（L2 回放，零成本，重构后首次复跑）**：`python -m evals.runner --level L2` →
  **pipeline 12/12 ｜ 工具数值断言 10/10 ｜ E6 归档 8 条 全绿**（1.09s，LLM 调用 0）；结果留档
  `evals/results/20260914-141514-L2.json`。结论：B1–B4 的契约层/引擎/并发改写没有打坏 E8 资产
  （v4 §9 要求 E8「随契约/并发适配」）。**建议（待主理人定）**：把「批末 L2 回放」写进统一验收基线——
  零成本且能防 E8 隐式腐化。
- **E8 评估 L3（本地 rubric + 交叉校验 + 1 次真实 LLM 判官）**：
  `python -m evals.runner --level L3` → 四维均值 事实准确性 4.67 / 依据充分性 4.83 / 可解释程度 4.75 /
  不确定性坦诚 4.17，**diff vs 上次（2026-09-09 基线）+0.00**，工具交叉校验违规 0；
  `--llm-judge --limit 1` → 判官打分 5.0 / 5.0 / 5.0 / 4.0（真实 LLM 1 次，配额克制），
  说明 L3 的两条路径（本地 rubric、LLM 判官 + `ChatClient` 直构）在 B1–B4 重构后均可用。
  结果留档 `evals/results/20260914-144758-L3.json`、`…-144810-L3.json`。
- **L2 黄金断言收紧（B4-3 契约不变量入评估层）**：`evals/runner.assert_card` 新增三项校验——
  `confidence_detail` 必填、区间有序（0 ≤ low ≤ score ≤ high ≤ 100）、三档计数 == evidence 条数、
  `verdict` 在允许集合（可用 `expect.verdict_allowed` 收紧单例）。**实测会咬**：构造无明细的卡 →
  报「缺 confidence_detail」；verdict 越界 → 报错。收紧后 L2 复跑 **12/12 + 10/10 仍全绿**（1.02s，零成本），
  说明 cassette 回放的 12 张卡片全部满足 B4 契约不变量；结果留档 `evals/results/20260914-145523-L2.json`。
- **顺手抓到并修掉一个 B3 遗留的脆弱断言**：`tests/test_evals.py::test_runner_l2_offline_zero_llm`
  断言「两次 L2 报告字节级一致」，但 B3-3 起管线**并行取数**（`asyncio.TaskGroup`）导致工具完成顺序不定，
  该断言在 12 条里**偶发失败**（本次 pytest 实测抓到：L2-005 两次 `tools` 顺序不同）。修法：L2 报告 `tools`
  改按工具名排序——取数顺序与语义无关，而报告的意义就是「同样输入 → 同样报告」。
- **tests/test_evals.py 同步 B4 断言**：`_card()` 默认补规则推导明细 + 新增 3 例（缺明细 / 计数不一致 / 区间非法 /
  verdict 允许集合收紧）。pytest **329 → 332** 全绿。
- **真实联调抓到 D4 两个真 bug（已修 + 回归用例）**：
  1. **照片复盘临时文件竞态（阻断级，`43d3efd`）**：`/api/photos/review` 原先在端点里建/删临时文件，
     但 SSE 流是惰性的（端点返回响应后流才被消费）→ worker 线程读图时文件已被删，真实浏览器表现为
     「图片读取失败：No such file or directory: tmpXXXX.jpg」——**D4 页在真实环境从未跑通**；既有 pytest
     用例的 Fake 照片分析忽略图片路径，所以一直没暴露。修法：临时文件改为 worker 线程内建销（与读图同
     生命周期），会话历史改用上传原始文件名；补竞态回归用例（修复前红 / 修复后绿）。修后真实照片联调
     通过（35–38s：四维分析 + 复盘卡）。
  2. **未命中维度用首条依据兜底（误导显示，`49a7b72`）**：复盘卡 evidence 并非四维都有，旧实现
     `?? evidence[0]` 会把「曝光」文案显示到「色彩/时间」卡片下。改为如实留空（「—」），并加静态门禁
     （实测：塞回即红）。真实复跑确认：曝光有内容、其余三维「—」。
- **D2 真实渲染自检（B4-4 结构化数据端到端）**：`#/d2` 真实运行规划 → 天象时间线渲染出
  「05:38 日出 / 18:00 日落」，月相行同样来自 `tool_result.data`——结构化字段确实喂到了 UI。
  **发现（未改，留待主理人定）**：D2 页「银河窗口」行恒为「运行规划后更新」——规划管线采集步骤是
  weather_forecast + moon_phase + sun_times，**不含 galaxy_visibility**，该行属死 UI；
  修法两选（管线补工具 / 页面按意图隐藏），属产品与管线行为决策，本批不动。
- **CLI 在 GBK 控制台下崩溃（真 bug，已修）**：`python -m lighttrail.cli --pipeline ...` 在 cp936 控制台
  下，模型输出里的 `⚠`（卡片降级标注自带）→ `UnicodeEncodeError` → **rc=1、零输出，还白烧一次 LLM 调用**
  （Windows 本地一句话就能踩）。修法：CLI 启动时 `_configure_stdio()`——stdout/stderr
  `reconfigure(errors="replace")`，保持控制台编码不变，只把不可编码字符降级；实测同环境 rc=0、
  中文完整、`⚠` 显示为 `?`。回归用例 `tests/test_cli.py`（含「cp936 写 ⚠ 必抛」的前提断言）。
- **新增 `scripts/live_check.py`（真实联调自检，把 B4 踩的坑固化成守卫）**：一次跑
  CLI `--pipeline` + 五个 Web 端点，内置三道守卫——① 端口被占即中止（防 9-09 残留旧后端被 Vite 代理
  静默喂旧代码）② 起服务后校验 `/openapi.json` 含 `CardEvent`/`ConfidenceDetail` ③ 档案往返自动还原原文件；
  断言卡片契约不变量（confidence_detail 区间/计数、verdict 集合）与 `tool_result.data`。
  **实跑全绿（HEAD 当次）**：CLI rc=0 ｜ decide 16 帧 + verdict=go + detail 60（42–78，计数 1/2/1=4 条依据）
  ｜ chat token+done ｜ photos/review 27.8s 出复盘卡（含 confidence_detail）｜ profile PUT 生效并还原。
  **建议（待主理人定）**：把它与「L2 回放」一起写进 §1.4 统一验收基线，批次收口各跑一次。
- **同类第二个坑在我自己的脚本里**：live_check 首版打印 `✗` 在 GBK 控制台同样 UnicodeEncodeError
  → 已统一改 ASCII 标记 + `_configure_stdio()`（自己踩自己修）。
- **真实渲染覆盖面收尾（D1 + 总览 + M1，六页全覆盖）**：同一套无头 Chrome 自检跑完剩余三页——
  **D1 灵感页**：真实「一句话决策」出方案卡，且**轨迹面板实时显示真实管线步骤**（意图理解 → 管线_planning
  → 采集_weather_forecast → 采集_moon_phase…）——「trace 即 UI」在真实链路上成立；localStorage 卡片带
  `verdict=go` + `confidence_detail`（score 60 / 44–76 / 计数 1-3-1 = 5 条依据）。**总览页**：环图显示
  **60% 中等置信**（取 `confidence_detail.score`，验证 B4-3 的 HomePage 改动）+ 结论 + 下一窗口 + 记忆摘要。
  **M1 记忆页**：真实填写机身 →「保存档案」→「已保存 ✓」→ API 回读 `camera_body=松下 S5M2（联调）` 一致
  → **脚本自动还原 `data/profile.json`**（原本不存在 → 已删除，工作区无残留）。未再发现新缺陷。
- **发现 F1（非本批引入，P1，待定方案）：事件记忆只有读路径**——`EventStore.add_event` /
  `MemoryManager.add_event` 在生产代码里**没有任何调用方**（全仓仅 tests 调用），实测 `data/events.db`
  的 events 表 **0 行**；`search_memory` 是唯一入口（只读）→ E6-3 的语义提炼（按题材聚合成功率、
  需 ≥3 样本）在真实使用中拿不到数据。**历史文档自纠**：E6-0 前的就绪核查里写过「联调会写入事件记忆」，
  实测不成立，已就地加更正标注。方案待选（`record_event` 工具 / 复盘成功后自动落事件 / CLI 显式命令 /
  明确标为未实现）记在 TODO.md F1；同时把 F2（D2 银河窗口死 UI）、F3（L2 + live_check 入验收基线）、
  F4（D3 倒计时结构化时间窗）一并记进 TODO.md「遗留与发现」。
- **补测四管线真实覆盖，又发现两处（F5/F6）**：同一句「这周末想去拍银河，帮我看看去哪、什么时候去」
  - **F6（D2 核心数据缺口）**：意图判为 planning → 规划管线采集步骤写死（weather 7d + moon_phase + sun_times），
    **不含 galaxy_visibility** —— 模型在卡片「降级标注」里自陈「缺失：galaxy_visibility（银心升落窗口）、
    moon_events、match_sites…建议补齐后再定案」；随后**强制 `mode=inspiration` 复跑**，采到 galaxy_visibility 后
    卡片直接给出「19:21–22:21 银心窗口」+ 3 个具体机位（verdict=go，明细 score 75 / 59–91 / 计数 3-2-0）。
    根因定位到 `_collection_steps()` 的题材感知分支**只有灵感管线在用**（规划/临场各自写死步骤）。
  - **F5**：`match_sites`（机位×天象确定性评分）**未接入任何管线**（全仓仅自身定义 + trace 字段映射 + 置信度
    规则引用），规划管线只把「候选机位」（占位坐标）塞进 prompt —— 与 v4「评分代码化」原则相悖。
  - 两项方案待选，已记 TODO.md F5/F6；四管线真实覆盖现状：灵感（强制 mode 复跑 ✓）/规划 ✓/临场 ✓/复盘 ✓。
- **续挖两处（F7/F8，均为「文档叙事 vs 代码接线」类）**：
  - **F7 配额降级链未接线**：`QuotaLedger` 记账**已接通**（`build_client` 注入账本 → `ChatClient` 成功后
    `record()`），但 `estimate()` / `check()` / `degrade()` 在 `src/` 与 `evals/` 里**零调用点**
    （仅 `photo_analysis` 建账本做视觉预估）→ 「水位 >90% 自动降级并标注」在真实路径上不会发生，
    `DecisionCard.degraded` 目前只有「复盘缺图」一种来源。
  - **F8 照片反推（D1.2）没有入口**：`api/*.py` 零处引用 reverse、CLI 也无反推命令、
    `Orchestrator.reverse_plan` 除 tests/evals 外无调用方；D1 页「参考图反推」上传打的是
    **复盘端点 `/api/photos/review`** → 给回的是复盘卡而非复刻计划（FRONTEND-SPEC §5 当时即如此映射，属占位）。
  两项方案待选，已记 TODO.md F7/F8。**审计到此形成清单 F1–F8**：其中 D4 竞态 / D4 兜底显示 / CLI 编码 / L2 脆弱断言
  已当场修复，其余六项属产品与接线决策，等主理人拍板（B5 批次一并定夺最省事）。
- **会话回放链路真实自检（六页最后一条流程）**：D1 决策 → localStorage 会话记录 → 总览页「最近计划」点击 →
  回放渲染出「用户：今晚上海火烧云值得冲吗？」+「光迹：## 拍摄方案（决策卡片）…」+ 决策卡 ✓；
  `/api/sessions/{id}` 真实 JSON 形状与前端 `SessionDetail` 一致（`session{session_id/history/pipeline/
  workspace/user_id/created_at/updated_at}`、`workspace.last_card` 带 `verdict` + `confidence_detail`、
  `trace{llm_calls/tool_calls/steps/sources}`）——该条流程未发现新缺陷。
- **环境清理（第二次）**：5173 端口上又发现一个 **2026-09-09 20:05 启动的残留 Vite dev server**
  （与之前那个 9-09 残留后端同源、同日启动），已结束。教训同前：本地起服务前先确认端口占用，
  否则会被旧实例静默接管（`scripts/live_check.py` 已内建端口守卫）。
- **新增前端壳层自检 `npm run check:ui`（零依赖）**：`frontend/scripts/shell-check.mjs` 用本机 Chrome/Edge 的
  `--headless --dump-dom` 逐个打开六页路由，断言「页面标题渲染出来 + 根节点非空」（防白屏 / 路由断裂 /
  JS 运行时错误这类回归），外加「未知路由回落总览」；可选 `--screenshots <dir>` 顺带出双视口截图
  （1440×900 / 390×844，对应设计规范里遗留的「双视口自检」）。**实测有效**：正常 base → 7/7 通过；
  指向不存在的 base → 7/7 失败且退出码 1（不是摆设）。选择零依赖实现（不引入 Playwright）是为了
  不给项目加浏览器依赖，本机有 Chrome/Edge 即可跑；需要真实数据链路时仍走真实联调脚本。
- **发现 F9（打包声明漏运行时依赖，已修 + 加门禁）**：`httpx` / `tenacity` / `pydantic` 在源码里是**运行期直接
  import**（`infra/http.py` 数据源、`adapters/llm/client.py` LLM 重试、契约层与 API 层），但此前只在
  `[project.optional-dependencies].dev` 里声明 → 干净的 `pip install .` 会让**核心链路**（LLM 重试 + 天气工具）
  直接 ImportError（开源后的「装完就跑不起来」）。修法：三者进 `[project].dependencies`，dev extra 不再重复声明；
  并新增 `tests/test_packaging.py`（2 例）——扫描源码顶层 import、剔除标准库与本包、按发行名别名映射（PIL→Pillow、
  pydantic_ai→pydantic-ai-slim）后必须都在 dependencies 里；**实测会咬**（临时删掉 `httpx>=0.27` 即红，恢复即绿）。
- **发现 F10（档案机位模板/键形状漂移，修一半）**：
  - (a) **✅ 已修**：`data/profile.example.json` 缺 `favorite_spots`（`UserProfile` 有 6 字段、模板只 5）——
    照模板建档案就丢了机位精确坐标（B5-3 要用的正是它）。已补 3 个样例机位（临港海边/外滩/佘山，带坐标与题材），
    并加 2 例门禁：模板字段覆盖 `UserProfile` 全字段、机位形状机器可读（`name/latitude/longitude`）；
    **实测删字段即红**（两例同时失败）。
  - (b) **⏳ 待 B5-3**：键形状分裂——记忆层 `sediment_favorite_spots` 写中文键（名称/纬度/经度/题材），
    前端读英文键（`name/latitude/longitude`）→ 语义提炼沉淀出来的机位在 D2「机位」列表里**静默不显示**。
    建议 B5-3（坐标改用 favorite_spots）顺势统一为英文键。
  - (c) **⏳ B5-1 前置提示**：新增 `tests/test_memory.py::test_user_id_namespace_is_not_implemented_yet`
    断言「记忆仍写在 data_dir 根、无 users/ 目录」——B5-1 落地后请**翻转该断言**，否则改造完成也提醒不到人。
- **顺带查证（结果正常）**：`npm` 之外的 `frontend/scripts/token-diff.mjs` 复跑 ✅ **32 项设计令牌与真源
  `:root` 逐项 diff=0**（E7-10 的令牌门禁仍绿）。


- **测试规模**：332 → **336**（+4：D4 竞态回归、D4 兜底门禁、CLI 编码容错 2 例）；ruff 0；`npm run build` 通过。
### B4 批次总结（闸门 4）

- **测试规模**：306 → **336**（+30：契约 5 / 前端静态门禁 7 / 置信度明细 4 / 编排卡片 3 / 结构化数据 5 / 评估不变量 3 / D4 竞态 1 / CLI 编码 2）。
- **门禁全绿**：pytest 336 ｜ ruff 0 ｜ lint-imports 3 kept / 0 broken ｜ smoke 21 ｜ `npm run build` 通过 ｜
  `gen:api` 幂等（diff=0）。
- **下一步（等确认）**：**B5 记忆命名空间 + 清理 + 文档**——① MemoryStore `data/users/{user_id}/memory/`
  迁移（本期恒 `_local`）② shim 到期删除（含 B4-1 加的 `lighttrail.llm.client` 豁免）③ ADR-004 落盘
  ④ architecture.md 重写为目标架构 ⑤ requirements.txt 对齐 pyproject。批次细节见 REFACTOR-ROADMAP §7。

## 2026-09-14（B3 适配层 + 并发：B3-1 ~ B3-5 全部交付，停闸门 3）

> 批次目标（v4 §6 B3）：async-first 一套实现（消灭同步/异步复制与三套并发机制）；并发 = 纯配置
> `LLM_CONCURRENCY` 默认 4；管线并行取数；tenacity 统一重试；httpx 数据源。

### B3-1 ChatClient async-first 重写 — 已提交（0cb0aad）

- 新增 `_ConcurrencyLimiter`（`threading.BoundedSemaphore` + 异步侧 `asyncio.to_thread`）——
  跨事件循环可用（同步门面走后台共享 loop、Web 走 uvicorn loop），只包单次 API 往返、重试在限流之外；
- 删除三套旧机制（`threading.Lock` + 自研 `_AsyncGate` FIFO 闸门 + `call_soon_threadsafe` 桥）；
- 单一实现：真逻辑只在 `acall`，同步 `chat` 退化为后台共享事件循环上的门面（并显式拒绝在
  事件循环内调用，避免静默死锁）；`concurrency` 成为唯一配置入口（`serial_llm=True` 兼容别名）；
- 测试合并重写为 `tests/test_client_concurrency.py`（11 用例），删旧两份客户端测试。

### B3-2 三套重试收敛（tenacity）+ httpx 数据源 — 已提交（136d60a）

- LLM 调用：手写指数退避 → `tenacity.AsyncRetrying`（次数/退避/可重试语义不变）；
- 数据源：新增 `infra/http.py`（httpx 取代 weather 的 urllib 手写重试；429/5xx/网络错误退避、
  4xx 立即失败带原因），`adapters/datasources/` 作适配层门面；
- 结构化输出校验：`photo_analysis` 自写循环 → 复用 `infra/validation.parse_with_retry`；
- **门禁实战**：weather 直连 adapters 被 AST「禁止边」检查当场拦下（domain → adapters），
  改为 infra 实现 + 适配层 re-export（TODO(B3-5/B5-4) 收敛）。

### B3-3 管线并行取数 — 已提交（4031732）

- `_collect_async`：每源一个 task（`asyncio.TaskGroup`），dispatch 经 `asyncio.to_thread`
  （不占 LLM 信号量）；单源失败降级 `{"error": ...}` 不拖垮管线；trace 口径不变；
- **实测延迟：顺序 0.603s → 并发 0.172s，降幅 71%**（验收线 ≥40%），并固化为回归用例。

### B3-4 TraceSink + 载荷白名单/掩码 + OTel 命名 — 已提交（6dd99fc）

- `api/events.py` 的桥改订阅 `contracts.observability.TraceSink`（类型层解耦）；
- 载荷白名单（llm/tool/step 各一组）+ 统一 `redact()` 出口（`infra/redact.py`：sk-/Bearer/api_key=）；
- `adapters/trace/otel.py`：内部中文键 → `gen_ai.*` 语义键（模型/token/工具名）+ `lighttrail.*` 自定义域。

### B3-5 收口 — 已提交（5ba3508 部分、605cbbd）

- import-linter 补齐第三段契约「交互层不被任何层依赖」（**3 kept / 0 broken**）；
- `QuotaLedger` 并发记账加锁（D5 配套）+ 并发记账用例；
- LLM 客户端实现迁入 `adapters/llm/client.py`，旧路径转 re-export shim（TODO(B5-4)）；
  monkeypatch 目标改真源模块（shim 顶部已注明）；
- 异步形态死锁回归（B0-1 复验）：并发 /api/chat（含共享 session_id + LRU 淘汰）不挂死、
  会话文件始终是完整 JSON。

### B3 批次总结（闸门 3）

- **出口检查**：pytest **306 全绿**；`ruff check src tests evals` 0；`lint-imports` **3 kept / 0 broken**；
  离线冒烟 21 项；**CLI 自由对话 + CLI `--pipeline` + Web `/api/chat` + Web `/api/decide`
  四条真实链路全部通过**；
- **交付**：单一并发机制 + 单一实现（async-first）、三套重试收敛、管线并行取数（-71% 延迟）、
  TraceSink 端口化 + 出口白名单/掩码、QuotaLedger 并发安全、客户端迁入适配层；
- **未完成 / 偏差（如实记录）**：
  ① import-linter 的 `layers` 分层版契约仍待开启——需要 interface/application/runtime/domain
  目标目录迁移，属 B5-4/B3-5 后半（当前用按包列出的三段 forbidden 契约等效覆盖）；
  ② `adapters/trace` 与 `adapters/datasources` 目前是「infra 实现 + 适配层门面」，
  因 runtime/域工具不得 import adapters，最终迁移随目标分层一并做；
  ③ `lighttrail/llm/client.py` 与 `lighttrail/orchestrator/schemas.py` 等 shim 到期删除点在 B5-4；
- **下一步**：闸门 3 等确认后进 **B4（契约单一真源 + 前端重接）**——OpenAPI→TS 生成流水线 +
  删前端手抄类型/置信度常量表/正则解析。


## 2026-09-13（B2 引擎重写 · 进行中：B2-1 ~ B2-4 已完成）

> 批次定位：`docs/REFACTOR-ROADMAP.md` §4。目标 = 注册表方向反转（声明式 ToolSpec + 装配根）+
> 去全局单例 + 接入 PydanticAI。本段记录已完成的四个任务；B2-5（Model 桥）/ B2-6（AgentRuntime）/
> B2-7（TestModel + test_hotplug + 收口）待续。**15 个工具名与行为不变。**

### B2-1 声明式 ToolSpec 改造：basic + exposure — 已提交（72ebe68）

- 新增 `tools/_base.py` 的 `PureTool`（ToolSpec + 无状态函数 → Tool 端口）；
- 两个工具模块的 `@registry.tool` 装饰器改为模块级 `ToolSpec` + 文件末 `TOOLS` 元组，
  **不再 import `agent.tools`**；description / parameters 原文保留，新增 main_field /
  confidence / capabilities（等价迁自 `trace._MAIN_FIELD` 与 `confidence` 三集合）；
- 注册表：`register_tool(Tool)` 主路径 + `dispatch(ctx=...)` + `to_openai_schema/specs` 以
  ToolSpec 为真源；`infra/trace.record_tool` 支持 main_field / confidence 显式覆盖；
- 测试：`tests/test_registry.py`（6 用例）；pytest 272 → 278。

### B2-2 声明式 ToolSpec 改造：astronomy + weather — 已提交（e7f774b）

- 7 个工具（5 天文 + 2 天气）同 B2-1 改造；
- `ToolSpec` 增 `confidence_rule`；`infra/confidence.resolve_confidence(spec, data)` 让动态规则
  （预报时效：覆盖当日 high / 跨天 medium）挂在 spec 上，不再靠工具名手抄表；
- pytest 278 → 279。

### B2-3 声明式改造：site_match / memory_tool / photo_analysis + 去模块级全局 — 已提交（a6c6212）

- **删除两处服务定位器**：`memory_tool` 的 `_DEFAULT_STORE` / `set_event_store` /
  `_get_store`、`photo_analysis` 的 `_DEFAULT_CLIENT` / `set_client` / `_get_client`；
  改为 `SearchMemoryTool(store_factory)` / `AnalyzePhotoTool(client_factory)` /
  `ReverseEngineerPhotoTool(client_factory)` 构造注入 + `build_tools()` 工厂；
- 工具函数增显式依赖参数（`search_memory(store, ...)`、`analyze_photo(..., client=...)`）；
- `orchestrator` 增 `photo_reverse` 钩子（此前 reverse_plan 硬编码 import 模块并靠 monkeypatch
  全局注入，测试只能污染模块态）；
- 顺带修复潜在循环依赖：`llm/client.py` 的 QuotaLedger 改 TYPE_CHECKING（client ↔ quota）；
- 测试改造 4 个文件（test_events / test_photo_analysis / test_reverse_plan / test_pipeline_e2e）；
- **偏差记录**：智能工具的 LLM 依赖走构造注入而非 `ToolContext.llm`——后者是 async 端口，
  同步工具无法 await，等 B3 async-first 后再切（devlog 与 AGENTS 同记）。

### B2-4 ToolRegistry(specs) + composition root — 已提交（6000683）

- 新增 `runtime/registry.py`：`ToolRegistry(tools)` 构造注入、dispatch 走 Tool 端口、trace
  元数据来自 spec、动态注册保留给测试替身；**无模块级单例**；
  签名说明：v4 §2.3 示意写作 `ToolRegistry(specs)`，实现按「spec 挂在 Tool 实例上」调整为
  `ToolRegistry(tools)`——dispatch 需要实现体，只传 spec 无法调用；
- 新增 `composition.py`：全仓唯一 new 点（build_registry / build_client / build_memory /
  build_agent / build_orchestrator / build_recorder / build_ledger），参数显式传入；
- `agent/tools.py` 转 re-export shim（ToolRegistry / ToolError / 迁移期全局单例，TODO(B2-7) 删除）；
  `tools/__init__.py` 不再向全局注册表自注册；
- `cli.py` / `api/app.py` 改从装配根取实例（`ApiDeps.registry` 改 default_factory）；
- 门禁：import-linter 第二段契约「适配器只实现端口，不被其他层反向依赖」开启 →
  **2 kept / 0 broken**（Analyzed 58 files, 160 dependencies）；
- 遗留（如实记录）：`runtime/registry.py` 仍 import `infra.trace` / `infra.confidence`
  （观测与规则收敛到 contracts 端口 / adapters 后消除，B3-5）；旧包 agent / orchestrator / tools
  与目标层目录（runtime/domain/interface）的搬迁随 B3-5 一并落位。

### B2-5 PydanticAI 自定义 Model 桥 — 已提交（230dbbc）

- `contracts/llm.py`：`LLMProvider.complete` 增 `usage_callback`（(输入, 输出) 回调）；
- `adapters/llm/provider.py`：`ChatClientProvider` 把既有 ChatClient（acall async 通道）适配成
  LLMProvider 端口（B3 会以同层 async-first 实现接替，端口不变）；
- `adapters/llm/pydantic_bridge.py`：`LightTrailModel`（继承 `pydantic_ai.models.Model`）——
  instructions / SystemPromptPart → 前置 system（静态前缀利缓存）、tool / retry 分片 → OpenAI 消息、
  `ToolDefinition` → function schema、`extra_body` 的 thinking / reasoning_effort 透传
  （ADR-003 的命名参数 vs extra_body 双路径仍在 llm/client）、usage → `RequestUsage`、
  异常 → `ModelAPIError`；流式按 PRD 附录 A2 延后（docstring 明示）；
- 依赖：`pydantic-ai-slim>=2.41,<3`（slim：自研 provider 桥，不引框架内建供应商 extras）；
- 测试：`tests/test_pydantic_bridge.py` 7 用例（含 Agent 全链路 ReAct）；pytest 279 → 286。

### B2-6 AgentRuntime + ContextBuilder 迁入 runtime — 已提交（f0c5060）

- `runtime/context.py`：五层上下文自 `agent/context.py` 迁入；`DEFAULT_ROLE_PROMPT` 删掉手写工具清单
  （能力叙述由注册表自动生成，layer:role 版本 1→2）；`agent/context.py` 转 re-export shim（TODO(B2-7)）；
- `runtime/agent.py`：`AgentRuntime`——
  · 工具：`ToolSpec → Tool.from_schema(partial(registry.dispatch), json_schema=spec.parameters)`
    （**schema 真源仍是 ToolSpec**，新增工具无需改运行时与任何提示词）；
  · trace 口径不变：LLM 事件由 Model 桥记录（耗时 + tokens），工具事件由注册表记录（spec 元数据）；
  · 轮数护栏：`max_tool_rounds → UsageLimits.request_limit`（settings.REACT_MAX_ROUNDS=12）；
  · 深推理：`reason_model`、无工具、temperature=0.3，`reason_thinking` 时透传扩展参数；
  · 同步门面 `run/reason` + 异步 `arun/areason`（事件循环内调同步门面显式报错）；
- **分层修正（门禁抓到）**：初版把 Model 桥构造放在 AgentRuntime 内 → `lint-imports` 报
  `runtime → adapters` 违规；改为装配根构造 Model、runtime 只收 pydantic-ai Model，契约恢复
  2 kept / 0 broken；
- 测试：`tests/test_runtime_agent.py` 5 用例；pytest 286 → 291。

### B2-7 热插拔验收 + 架构禁止边 — 部分完成（5756583）

- `tests/test_hotplug.py`（3 用例）：动态 ToolSpec 注入后四处自动生效——dispatch / trace 元数据
  （main_field + confidence）/ 动态置信度规则（confidence_rule=forecast）/ 能力叙述自动包含新工具；
  另证框架侧 schema 与 ToolSpec 同源；
- `tests/test_architecture.py` 新增「禁止边」AST 断言（含函数内 import）：contracts / runtime /
  tools / memory 不得依赖 adapters / api / cli / orchestrator——当前零违规，R2 诊断的
  tools→orchestrator 反向依赖确已消除；
- pytest 291 → 295。
- **剩余（B2-7 未完）**：① 生产路径切换到 AgentRuntime（api 会话历史需从 OpenAI dict 迁到
  pydantic-ai ModelMessage、orchestrator / cli 换装）；② TestModel 替换 8 处自建 FakeChatClient
  （依赖①完成后才有意义——现 FakeChatClient 喂的是旧 ChatClient 路径）；③ shim 清理
  （agent/tools.py 与 agent/context.py 的 re-export、旧 Agent 门面）。③ 的启动条件是①，
  三者同批推进，B2 收口（devlog 批次总结 + 闸门 2）在①完成后进行。

### B2-7 生产路径切换（第一批：CLI + Web 自由对话）— 已提交（3e952c1、dc164fb）

- CLI 自由对话改走 `AgentRuntime`（PydanticAI：Model 桥 + ToolSpec 工具 + 轮数护栏），
  `--pipeline` 仍用旧 Agent；`lighttrail/__init__.py` 统一关闭 pydantic-ai 启动横幅；
- api `/api/chat` 改走 `AgentRuntime` + 会话历史双向转换（桥新增 `to_openai_history` /
  `from_openai_history`：instructions 不入历史、工具调用 id→name 解析、多模态内容还原），
  会话层继续以 OpenAI dict 持久化；
- **真实链路抓到一个 fake 掩盖的缺陷**：ChatClient 的 usage 回调回传 `UsageStats` 对象，
  而端口契约是 (输入, 输出) 两个整数——适配本应在 `ChatClientProvider` 完成；测试 fake 写成
  两参数形式掩盖了它，由 Web 真实联调复跑时暴露并修复（fake 已改为镜像真实签名）；
- 验证：pytest 295 全绿、ruff 0、lint-imports 2 kept、冒烟 21 项；**CLI + Web 真实查询均经新
  runtime 跑通**（`queued → tool_call → tool_result → token → done`）。
- **剩余（B2-7 未完）**：① 四管线/编排器切 runtime（orchestrator 的意图解析与 reason 通道
  需经 runtime 的 chat/reason 面）；② TestModel 替换 8 处 FakeChatClient；③ shim 清理
  （agent/tools.py、agent/context.py re-export 与旧 Agent 门面）→ 然后 B2 收口（闸门 2 汇报）。

### B2 批次总结（闸门 2，2026-09-14）

- **出口检查（路线图 §1.4 + B2-7 验收标准）**：pytest **296 全绿**；`ruff check src tests` 0 告警；
  `lint-imports` **2 kept / 0 broken**（契约层零依赖 + 适配器不被反向依赖）；离线冒烟 21 项；
  `npm run build` 通过；**15 个工具名与行为不变**；`tests/test_hotplug.py` 通过；
  CLI（自由对话 + `--pipeline`）与 Web（`/api/chat` + `/api/decide`）**真实查询全部通过**。
- **交付**：B2-1 ~ B2-6 完成；B2-7 完成主体——**生产路径全部切到 AgentRuntime**
  （CLI / Web / 四管线编排器），会话历史经桥 `to_openai_history` / `from_openai_history` 双向转换；
  测试换装 5/9 文件（orchestrator / pipeline_e2e / reverse_plan / reason / agent）。
- **未完成（如实记录为 B2 尾巴）**：
  ① `test_trace` / `test_context` / `test_memory` 三个用例文件 + `evals/runner.py` 两处尚未换装；
  ② orchestrator 的旧 `Agent` 分支与 `agent/` 下 shim（`agent/tools.py`、`agent/context.py`、旧 `Agent`
  门面）尚未删除。两者互为前提：先无引用，才能删。
- **架构收益**：注册表方向反转（声明式 ToolSpec + 装配根唯一 new 点）；元数据单一真源
  （schema / 主字段 / 置信度 / 能力全来自 ToolSpec）；PydanticAI runtime 接管 ReAct 循环与用量护栏
  （平台差异留在 Model 桥，ADR-002/003 语义不丢）；**R1/R2 两个根因消除**（全局单例退场、
  `tools → orchestrator` 反向依赖消失）。
- **平台中立性审计**：新增代码零品牌字面量（`contracts/`、`adapters/` 由 AST 测试把关；品牌默认值只
  出现在 `config.py` 默认值与适配层）；LLM 调用一律经 `LLMProvider` / Model 桥；模型名由装配根绑定，
  运行时无品牌判断。结论：**通过**。
- **遗留风险**：runtime 与 legacy `Agent` 暂时并存（镜像双路径），必须在 B2 尾巴删除 legacy 分支，
  否则违反「不做永久白名单/双实现」纪律；`runtime/registry.py` 仍 import `infra.trace` / `infra.confidence`
  （B3-5 随观测端口收敛消除）。

### B2-7 尾巴细化（2026-09-14，供接续执行）

- **已完成换装的用例文件（6/9）**：test_orchestrator、test_pipeline_e2e、test_reverse_plan、test_reason、
  test_agent、test_context；
- **剩余换装**：`tests/test_trace.py`（Agent + ToolRegistry）、`tests/test_memory.py`（Agent）、
  `evals/runner.py`（2 处 Orchestrator 构造）；
- **shim 清理的真实工作量（重要）**：`lighttrail.agent.tools` 的**迁移期全局 registry** 被 13 个文件引用
  （test_astronomy / test_events / test_exposure_rules / test_memory / test_orchestrator /
  test_photo_analysis / test_pipeline_e2e / test_reason / test_registry / test_reverse_plan /
  test_site_match / test_tools / test_weather；另有 evals/runner.py）。删除 shim 前需给它们一个
  替代入口——推荐在 `tests/conftest.py` 提供共享 `registry`（`ToolRegistry(TOOLS)`）或改为显式构造，
  然后按序删除：orchestrator 旧 Agent 分支 → agent/core.py 旧门面 → agent/loop.py → agent/context.py →
  agent/tools.py（最后，等 13 处引用清零）。

### B2-7 收尾（2026-09-14，B2 批次闭环）

- **换装全部完成（9/9 用例文件 + evals）**：test_orchestrator / test_pipeline_e2e / test_reverse_plan /
  test_reason / test_agent / test_context / test_memory / test_trace / evals-runner；
- **删除旧 `agent/` 包**（`core.py` 旧门面、`loop.py` 旧 ReAct 循环、`context.py`/`tools.py` 两个 shim），
  orchestrator 去掉迁移期双路径（`self._runtime` 成为唯一 LLM 交互面），`composition.build_agent` 移除，
  `smoke.py` 换装；pyproject 的 import-linter 配置同步去掉 `lighttrail.agent`；
- **真实联调抓并修掉一个真 bug（重要）**：桥的 `_map_messages` 会把逐轮变化的 `instructions`
  （⑤层轨迹）累积成多份 system 消息 → ECNU 对重复 system 返回 500，表现为「Web 工具轮失败而 CLI 正常」；
  已改为只取最新一份 + 补回归用例（`5784120`）；
- **收尾验证**：pytest **298 全绿**；ruff 0；`lint-imports` 2 kept / 0 broken；冒烟 21 项；
  CLI 自由对话 + CLI `--pipeline` + Web `/api/chat` + Web `/api/decide` 四条真实链路全部通过
  （decide 产出真实 DecisionCard）；
- **B2 批次结论**：引擎重写完成——注册表方向反转（声明式 ToolSpec + 装配根）、元数据单一真源、
  PydanticAI runtime 接管循环（Model 桥保 ADR-002/003）、R1/R2 根因消除、迁移期 shim 与 legacy 分支清零。

### B2 中间态验证（B2-1 ~ B2-7 第一批后）

- pytest **295 全绿**；ruff 0；`lint-imports` 2 kept / 0 broken；离线冒烟 21 项；`npm run build` 通过；
- **CLI 自由对话 + Web /api/chat SSE 真实查询复跑通过**（装配根接管后系统可用）；
- 下一步：**编排器/四管线切 runtime**（意图解析与 reason 通道经 runtime 的 chat / reason 面），
  随后 TestModel 替换 FakeChatClient、shim 清理与 B2 批次收口。
## 2026-09-13（B1 契约层 + 配置：零依赖契约 + 用户体系预留 + 统一护栏）

> 批次定位：`docs/REFACTOR-ROADMAP.md` §3。目标 = 新建零依赖 `contracts/`（R1/R2 的解药）+
> 契约模型下沉 + 用户体系三处预留定型 + 配置换 pydantic-settings 与门禁。基线起点 B0 `58a530c`。

### B1-1 contracts/ 骨架 — 已提交（b6a445f）

- `contracts/tool.py`：ToolSpec（main_field / confidence / capabilities，替代 trace._MAIN_FIELD、
  confidence 三集合、DEFAULT_ROLE_PROMPT 手写工具清单三处漂移源）+ ToolContext（注入式上下文 +
  emit）+ Tool Protocol + ToolResult + Confidence；ToolContext 的端口注解走 TYPE_CHECKING
  （避免骨架期就依赖未落的协议文件）。
- `contracts/context.py`：RequestContext（frozen；user_id="_local" / session_id / llm_overrides）。
- 偏差记录：`Confidence` 用类 `str, Enum` 而非 v4 草案的 `enum.StrEnum`（后者需 py3.11，项目要求 py310+）。
- 测试：`tests/test_contracts.py` 7 用例；pytest 230 → 237。

### B1-2 契约模型迁移（Intent / DecisionCard / TraceEvent / Plan）— 已提交（4bf9c67）

- `contracts/models.py` / `events.py` / `plan.py` 承接模型：Intent / Source / ParamSuggestion /
  LocationSuggestion / PhotoAnalysisReport / PhotoReverseReport / DecisionCard、TraceEvent + KIND_*、
  SSEEvent（SSE 事件类型前后端单一真源，B4 生成前端类型）、Plan/PlanStep（步数上限护栏进类型）。
- 旧路径转 re-export shim：`orchestrator/schemas.py`（整文件）、`infra/trace.py` 的
  TraceEvent / KIND_*，均带 TODO(B5-4)，不含新增逻辑。
- src 调用方改指契约真源；`tools/photo_analysis.py` 不再 import `orchestrator.schemas`
  ——**R2 诊断的 tools→orchestrator 反向依赖已消除**。
- 测试：+6 用例（emit 投递 / Plan 护栏 / frozen 与默认工厂 / shim 同一对象 / infra.trace re-export /
  SSE 单一真源）；pytest → 243。

### B1-3 用户体系预留接口（LLMConfig / LLMProvider / UserConfigProvider / KeyVault）— 已提交（a8321d1）

- `contracts/llm.py`：LLMConfig（frozen、repr 掩码 api_key 只露后 4 位、concurrency 默认 4）+
  三个 Protocol；模块内零品牌字面量（ADR-002）。
- 新增 `adapters/` 适配层包：`adapters/llm/config_provider.py`
  - DeploymentConfigProvider：配置解析单点，优先级链「请求级 llm_overrides > 部署级」；
  - EnvKeyVault：部署级只读，写入抛 KeyVaultError（不静默失败）。
- 测试：`tests/test_config_provider.py` 8 用例；pytest → 251。

### B1-4 剩余 Protocol（MemoryStore / KnowledgeProvider / DataSource / TraceSink）— 已提交（9e39191）

- `contracts/memory.py`（TokenBudget / MemoryBlock / EventRecord + MemoryStore：per-user 可写、
  方法首参 RequestContext）、`knowledge.py`（KnowledgeChunk 带 source/version + KnowledgeProvider：
  全局只读、签名不含 user_id，D10/D14）、`datasource.py`（async get）、`observability.py`（TraceSink）。
- `infra/trace.py`：TraceRecorder / NullTrace 补公开 `emit`（实现 TraceSink 端口；_emit 转为构造事件
  后转交 emit，既有记录 API 与行为不变）。
- 测试：+8 用例；pytest → 259。

### B1-5 config.py 换 pydantic-settings + 统一护栏 + import-linter 门禁 — 已提交（2856495）

- 配置重写：AliasChoices 表达 `LLM_`↔`ECNU_` 别名（LLM_ 优先）、frozen 快照、非法值直接抛
  ValidationError（不再静默取默认值）；
- 护栏项落地（D5）：`LLM_CONCURRENCY=4` / `REACT_MAX_ROUNDS=12` / `PIPELINE_MAX_STEPS=12` /
  `PLAN_MAX_STEPS=8`（与 contracts.Plan 同源）/ `LLM_TIMEOUT=(30,120)`（支持 "30,120" 写法）；
- 兼容：`LLM_SERIAL_LLM` 只读别名（=true → concurrency=1）；`settings.serial_llm` 改为派生属性
  （concurrency == 1），B3 随旧开关删除；
- 注释诚实化：`agent/loop.py`、`llm/client.py` 删掉「默认串行适配 ECNU」表述，改为「并发是纯配置、
  默认 4、B3 换 async-first 单 Semaphore」（ADR-004，硬约束 3）；
- 门禁：`pyproject.toml` 增 `[tool.importlinter]`「契约层零依赖」契约（KEPT）；另三段目标分层契约
  按 v4 §2.2 文本备好并标 TODO(B3-5)；
- 测试：`tests/test_settings.py`（10 用例）+ `tests/test_architecture.py`（3 用例：契约零依赖 AST
  兜底含函数内 import / 契约层第三方白名单 / contracts+adapters 无品牌字面量）；pytest → 272。

### B1 批次总结（闸门 1）

- **门禁**：pytest **272 全绿**；`ruff check src tests` 0；`lint-imports` **1 kept / 0 broken**
  （Analyzed 54 files, 149 dependencies）；离线冒烟 21 项；`npm run build` 通过；
  **CLI 自由对话 + Web /api/chat SSE 真实查询复跑通过**（配置重写后系统可用性验证）。
- **平台中立性审计**：B1 新增代码的品牌字面量集中在 `config.py` 默认值（允许处）；`contracts/`
  与 `adapters/` 经 AST 测试确认零品牌字符串；业务层未新增任何平台判断；LLM 调用仍走
  ModelRouter + 后续 LLMProvider 适配层。结论：**通过**。
- **遗留与偏差（如实记录）**：
  1. import-linter 另三段契约（分层 / 适配器反向 / 交互层）未开启——目标分层包
     （interface/application/runtime/domain）尚不存在，现在开启必然误报；文本已备好，B3-5 开启；
  2. `tools/` 仍 import `lighttrail.agent.tools`（全局注册表 R1）→ 由 B2-1~B2-4 的声明式 ToolSpec +
     装配根解决，本批不动；
  3. `.env` 不再注入 `os.environ`（旧 `_load_dotenv` 行为）：仅影响非 Settings 键的外部覆盖
     （如 `tools/weather.py` 读的 `OPEN_METEO_BASE_URL` 需用真实环境变量），B3-2 数据源适配层归一化；
  4. `Confidence` 用 `str, Enum` 而非 `enum.StrEnum`（py310 兼容取舍，见 B1-1）。
- **下一步**：停在闸门 1，等主理人确认后进 B2（引擎重写：B2-1 ~ B2-7）。
## 2026-09-13（B0 止血护栏：修三 bug + 补 tokens + 清假注释）

> 批次定位：`docs/REFACTOR-ROADMAP.md` §2。B0 不改架构，只修已确诊 bug 与「注释承诺 > 实现」
> 的不实注释，让重构从干净基线起步。基线起点 `c3cdafb`（文档体系切 v4）→ B0 四任务全部完成。

### B0-1 修复 SessionManager 并发三 bug — 已提交（f875f5d）

- **死锁**：`_evict_if_needed` 改为锁内只摘出（返回待落盘列表）、落盘在锁外完成，不再
  「持不可重入锁再调 save」；复现路径（新建会话被淘汰）不再挂死。
- **共享可变**：`create/get/restore` 一律返回深拷贝，缓存主体不外借，调用方改动须 `save()`
  才生效（路由层直接改缓存对象的问题消除）。
- **锁外非原子写**：落盘改「同目录临时文件 + `os.replace`」，加实例级写锁串行化落盘 +
  Windows 目标占用（WinError 5）退避重试，同 id 并发保存不再写出半截 JSON。
- **测试**：新增 4 用例（淘汰不死锁含线程超时兜底 / 淘汰补落盘 / 同 id 并发保存文件始终可
  解析 / get-create 深拷贝隔离），`tests/test_session.py` 12 → 16；连续 6 轮跑无 flake。

### B0-2 LLM usage 接通 tokens 记账 — 已提交（18d47c6）

- `llm/client.py`：新增 `UsageStats` 与每次调用可选 `usage_callback`（chat/acall 共用
  `_extract_usage`），usage 既回传上层记账、又照旧折算 QuotaLedger credits。
- `agent/loop.py`（ReAct）与 `agent/core.py`（深推理 reason，管线末端综合走它）把 usage 折算
  为 tokens/输入/输出 传入 `record_llm`；`TraceReport` 不再恒 None，供应商未返回 usage 时
  三项如实 None（不填 0 冒充）。
- **测试**：新增 3 用例（ReAct 真实 token / reason 通道 / 无 usage 如实 None），5 个测试
  伪客户端对齐新签名；pytest 223 → 230。

### B0-3 假注释清理（四处）— 已提交（9fca611）

| 位置 | 处置 | 理由 |
|---|---|---|
| `llm/client.py:7`、`config.py:11`「缓存策略」 | 删注释 | 全仓无 LLM 响应缓存实现；响应缓存属 v4 非本期项 |
| `infra/trace.py` tokens | 复核一致 | B0-2 已兑现，docstring 改为与实现一致（本批无新增改动） |
| `orchestrator/pipelines.py` 时效 | 删注释 | 承诺的时效计算不存在；本批不扩大范围，时效判据随 B7 受控规划通道再评估 |
| `orchestrator/orchestrator.py` 档案定位 | 改诚实描述 | 坐标仍为兜底常量，接 `favorite_spots` 属 B5-3 |

- 顺带修正：`_candidate_sites` 的坐标是按序偏移的**占位值**（非真实机位坐标）已在 docstring
  如实标注；`memory/semantic.py` 过期表述「E6-3 再做事件自动提炼」改为已落地表述。
- 纯注释变更：pytest 230 全绿、ruff 0；grep 复查无残留空头承诺。

### B0-4 B0 收口（回归 + 重构前基线）— 已提交（HEAD 58a530c）

- **门禁**：`pytest tests/` 230 全绿；`ruff check src tests` 0 告警；离线冒烟 21 项通过
  （同步修掉 `smoke.py` 伪客户端的 `usage_callback` 签名，B0-2 遗留）。
- **真实实跑（B0 出口检查单）**：CLI `python -m lighttrail.cli` 自由对话一次真实查询通过
  （`get_current_time` 工具链路 + 2 次 LLM 往返）；Web `uvicorn lighttrail.api.app:app` +
  `POST /api/chat` SSE 一次真实查询通过（事件序列 queued → tool_call → tool_result → token → done）。
- **基线标记**：本地 tag `refactor-baseline`（按纪律不 push tag）。
- **平台中立性审计**：B0 新增代码无任何供应商品牌字面量——`UsageStats`/`usage_callback` 是
  通用 OpenAI 兼容语义；模型名一律沿用既有 `ModelRouter` 解析结果，未新增品牌判断；新注释中
  的 B5-3/B7 引用均为批次编号。结论：**通过**。
- **下一步**：停在闸门 0，等主理人确认后进 B1（契约层 + 配置，B1-1 ~ B1-5）。
## 2026-09-12（架构重建启动：文档体系切换至 v4 方案）

> 背景：v4 重构方案（`docs/architecture-v4-proposal.md`，D1–D14）已定稿，本期重心 = 架构重构（B0–B7）。
> 本次为纯文档变更，无代码改动。

- **新建** `docs/REFACTOR-ROADMAP.md` v1.0：B0–B7 八批次拆为 35 个原子任务（B0-1 ~ B7-6），
  每任务含目标/前置/输入上下文/交付物/验收标准/涉及文件/难度，供 codex 单任务读取执行。
- **重写** `docs/codex-longterm-goal.md` → v7：当前位置 = B0 之前；批次闸门 0–7；
  硬约束更新（并发为纯配置 LLM_CONCURRENCY、shim 纪律、热插拔口径、知识写死红线）。
- **归档**（移入 `docs/archive/`）：`architecture-v3-proposal.md`（被 v4 取代）、
  `architecture-v4-deep-dive.md`（M1–M5 已并入 v4，v4 为单一权威）、
  `DEVELOPMENT-ROADMAP.md` → `DEVELOPMENT-ROADMAP-v2.5.md`（E 系列时代结束）。
- **更新**：PRD-v0.3 增附录 A2（v0.4 范围修订：本期=重构，D2.2 缩减，E9-3 砍，火烧云工具化低优先级待议）；
  `architecture.md` 加「重构前现状」横幅（B5-5 重写为 v3.0）；`AGENTS.md` 当前阶段/目录结构/并发约定同步；
  `README.md` 状态与文档链接同步；`TODO.md` 重排为 B0–B7 待办 + E1–E8 历史记录。
- **下一步**：B0-1 修复 SessionManager 并发三 bug。

## 2026-09-10（前端产品化打磨 · 小北反馈）

> 反馈：页面里充斥着「示例 / 原型规格 / 铁律编号 / 开发路径」等文字，不像产品。
> 处理原则：去掉一切开发措辞；能接真实数据的接真实，没数据给空态引导；不再用假数据填充。

- **HomePage**：环图由固定 72% 改为接最近一次真实决策卡（localStorage `lt.last_card`，
  由 D1/D3 决策落盘）——置信度主值（高 78/中 58/低 34 → 区间换算）+ 依据工具名 + 结论 +
  time_window「下一窗口」；无决策记录时显示空态 + 「去决策」引导；删除示例数据标注。
- **D2Page**：机位列表改接「我的记忆」档案（common_locations + favorite_spots，空档案给
  空态引导）；删除 Fake 机位/月相/银河/赶场轴示例；天象时间线与月相只在运行规划后解析到
  真实 tool_result 才显示；赶场信息改用最近决策卡 time_window + 机位（真实）。
- **D3Page**：删除「铁律①②」「D3 ·」「本地规则化演示」「card.evidence」「示例交互」等全部
  开发措辞；「相似历史（示例）」板块整体移除（无真实数据不展示）；概率条 →「可能性评估
  （由置信度区间换算）」；三态/置信度/倒计时文案产品化。
- **D4Page**：删除 FALLBACK_CARD 假示例分析面板；未上传时展示空态说明，上传后才渲染
  4 维分析与处方；移除 `/api/...`、E7-10 等文字。
- **D1 / M1 / AppShell**：去掉「D1 ·」编号、`/api/...`、GET/PUT、E7-x、「原型即规格」
  路径、第二迭代占位等；页脚收为产品标语；解释中心移除「示例数据」标签与 fake 字段。
- **验证**：`npm run build` 通过；令牌 diff 保持 0；pytest 223 全绿（纯前端改动）。
- **commit**：0e0ffb2。

## 2026-09-09（阶段六 E8 评估体系）

### E8-1 L2 黄金用例集 + 管线回归（E8-0 精神并入：真实最小样本开场）— 已提交

- **evals/**：`runner.py`（`python -m evals.runner --level L2`）+ `fake_data.py`
  （FakeDispatcher 确定性数据源 + CassetteChatClient 回放 + RecordingChatClient 录制）+
  `golden/L2-cases.json`（12 管线 ×5 组 + 10 工具数值断言，E6 照片 8 条归档，合计 ~30）。
- **cassette 录制/回放**：`cassettes/{group}.json`（galaxy/live/planning/sun/polar 5 组，
  每组意图+综合两段）；**E8-0 开场真实录制**：真实 Key 跑 5 个代表样本（10 次 LLM 调用），
  之后 L2 回放零 LLM 成本（实测 0.11s、llm_calls=0）。
- **断言**：`assert_card`（结构合法：结论非空/置信度枚举/evidence 非空/机位数上限/降级标注/
  关键词/时间窗口）+ 工具断言（eq/close/range/truthy/match/error 六种 op）。
- **测试**：`tests/test_evals.py` 11 用例（assert_card ×4 / rubric / 交叉校验 ×2 /
  cassette 回放 / LLM 判官降级 / L2 零成本可重复 / L3 报告结构）。pytest 212 → **223 全绿**。
- **commit**：d729dfd。

### E8-2 LLM-as-judge + 工具交叉校验（L3）— 已提交

- **evals/judge.py**：`LocalRubric`（4 维确定性打分：决策合理性/依据完整性/个性化程度/
  不确定性坦白，默认零成本）+ `LLMJudge`（深推理判官，JSON 解析失败自动降级本地）+ `ToolCrossCheck`（「工具即裁判」：快门参数 vs 500 法则/须解析）。
- **runner L3**：`--level L3 [--llm-judge --limit N]`；报告含 4 维均值 / 与上次 diff /
  交叉校验违规清单 / LLM 判官均值 `means_llm`；存档 `evals/results/`。
- **真实判官验证（配额克制）**：--llm-judge --limit 5 跑 5 条代表性卡片 → 均值
  决策合理性 4.0 / 依据完整性 3.6 / 个性化 2.4 / 不确定性坦白 3.6（比本地 rubric 更严格，
  个性化低分属实：回放卡片未引用记忆，诚实记录）。交叉校验违规 0。
- **配额记录**：E8 全阶段真实 LLM 样本 = L2 录制 5 条 + L3 判官 5 条 = **10 条（≤10 红线）**，
  调用 15 次；精确 credits 未持久化（QuotaLedger 未落盘）——遗留：联调后导出一份记账。
- **commit**：ecfc156。

### E8 阶段总结

- 出口检查单：L1 pytest **223 全绿**；L2 分钟级（0.11s）**零 LLM 成本可重复**（测试断言）；
  L3 报告存档 `evals/results/`（本地 + 真实判官两份），形成质量曲线起点；真实样本 ≤10 条
  记录如上；devlog 总结 + roadmap 勾选下方；平台中立性审计：evals 仅用 ModelRouter 解析
  判官模型（RouteIntent.DEEP_REASONING），无品牌判断、无 ECNU 硬编码。
- 遗留：① QuotaLedger 精确 credits 未持久化到评估报告（建议 L3 联调时导出）；② judge 打分
  稳定性：真实判官比本地 rubric 更严（尤其个性化维度），后续可调 rubric 权重核对；
  ③ E6 照片用例仍需真实照片窗口（E6-0 遗留，不影响 L2 结构回归）。

## 2026-09-09（阶段五·补 前端旅程页对齐）

### E7-6 前端工程基座升级（令牌对齐 + 6 页路由 + AppShell）— 已提交

- **令牌对齐（真源唯一）**：新增 `frontend/scripts/token-diff.mjs` 核对脚本——
  从真源 `lighttrail-prototype.html` :root 提取 32 项令牌，与前端 styles.css
  逐项比对（规范化忽略空白差异）。现在 **diff=0 通过**；styles.css 整体重写为
  真源令牌（--bg-base #0A0D14 / --semantic-go #3FCF8E / --dusk-gradient 三段渐变
  / --sky-band 天象带 等），旧令牌（--bg/--good/--bad…）保留为兼容别名指向真源值。
- **路由 3→6 页**：`#/home 总览` `#/d1 灵感` `#/d2 规划` `#/d3 决策` `#/d4 复盘`
  `#/m1 记忆`；旧 hash 别名兼容（#/chat→#/d1、#/sessions→#/home、#/card→#/d3，
  location.replace 不留历史堆积）。
- **AppShell**：顶栏旅程导航（灵感→规划→决策→复盘 带箭头）+ 我的记忆 +
  渐变落点（dusk-gradient）；≤560px 汉堡抽屉（#main-nav.is-open + 遮罩 +
  滑入 ≤300ms，prefers-reduced-motion 降级）；页脚「数据与依据 · 解释中心」
  模态（SourceContext 全局数据源清单，铁律③ 底座）；全局设置占位模态。
- **6 页落地**（E7-6 骨架）：Home（环图 Ring + 四阶段入口 + 最近计划/会话回放）、
  D1（对话/决策迁入 + 示例 prompts chips + 参考图上传区占位 + 方案卡）、D2
  （机位列表/月相/天象时间线 sky-band，Fake 标注）、D3（三态卡示例 + 决策输入
  + DecisionCardView 底座）、D4（批量上传 + 4 维分析 + 处方，Fake 标注）、M1
  （器材档案 GET/PUT /api/profile 可用 + 偏好/事件 Fake 标注）。示例板块一律
  显眼标注「示例数据」（诚实原则）。
- **验证**：`npm run build`（tsc 严格 + vite）通过；`npm run dev` 各页模块
  转换 200、/api 代理 200；令牌 diff=0；pytest 212 全绿（纯前端改动零回归）；
  ruff 0 告警。双视口（390×844/360×780）媒体查询已落地（汉堡/单列/触控 ≥44px），
  截图自检待有浏览器环境时执行。
- **commit**：0572a0e。

### E7-7 总览页 + M1 记忆页（真实档案读写）— 已提交

- **HomePage（总览）**：环图今日卡（72% 火烧云示例 + 显式「数据来源：示例计算」
  标注）+ 四阶段入口卡（灵感/规划/决策/复盘 hash 跳转）+ 记忆摘要改接
  `/api/profile` 真实数据（器材/偏好/常去机位，空档提示去 M1 填写）+ 最近计划/
  会话回放（localStorage 会话 + GET /api/sessions/{id}）。
- **M1Page（记忆）**：器材档案 GET/PUT `/api/profile` 真实读写（机身/镜头/水平/
  常去机位/偏好）；偏好芯片渲染档案 preferences 真实值，可编辑保存；事件历史 =
  最近会话（真实，本地记录 + 详情摘要）；EXIF 事件摘要注明待 D4 复盘接入（E7-10）。
- **验证**：`npm run build` 通过；令牌 diff 保持 0；后端 212 全绿（未动后端）。
  双视口布局依赖 grid 断点（≤820 单列）已在 E7-6 落地；截图自检待浏览器环境。
- **commit**：4e512fa。

### E7-8 D1 灵感页 + D2 规划页 — 已提交

- **D1Page**：参考图反推接通——`postFormSSE`（multipart → SSE 帧解析，api/client.ts
  重构抽公共 pipeSSE）→ `/api/photos/review`，文件校验 jpg/png ≤10MB，上传即跑照片
  反推/复盘管线并把卡片渲染；方案卡 A/B/C：主卡（DecisionCardView）+ 备选卡
  （card.alternatives 逐一渲染为 B/C 卡）。SSE 流式渲染与轨迹面板沿用 E7-6。
- **D2Page**：「运行一次规划决策」→ sendDecide（规划意图）→ 解析 tool_result 摘要
  更新天象时间线（sun_times 日出/日落，✦ 实时标记）与月相/银河可见（moon_phase /
  galaxy_visibility）；机位列表与赶场时间轴保持结构正确的示例数据并显眼标注。
  done 回调用局部 parsedSun 标志避免闭包过期。
- **验证**：`npm run build` 通过；令牌 diff 保持 0；后端 212 全绿、ruff 0（未动后端）。
- **commit**：4a1f3d5。

### E7-9 D3 决策页（三态大卡 + 置信度三层 + 倒计时 + 现场模式 + 曝光三角 + 四步推理）— 已提交

- **三态大卡**（铁律② 逐条勾验 ✓）：go/wait/risk 三态按钮 = **语义色**（--semantic-go/
  -wait/-risk + 对应 bg 变体）+ **图标**（Check/Clock/Warn SVG）+ **文字**（去/再等等/放弃
  与文案说明）；verdict 由 card.conclusion 规则判断，可手动切换，is-active 高亮（inset+glow）。
- **置信度三层**（铁律① 逐条勾验 ✓）：主值（font-display 大号 %）+ 区间条（interval-scale
  含 range 与 marker，本地规则化转换 high 78/68-88、med 58/46-72、low 34/22-48）+ 依据列表
  （card.evidence：工具/字段/置信度徽标/说明）——无单一数字。
- **倒计时**：解析 card.time_window 首个 HH:MM，每秒滴答（--font-data tabular-nums），
  过期自动滚到次日；**现场模式**（decision-panel.field-mode）：倒计时 24px + 参数卡
  横向 scroll-snap（min-width 280px）。
- **曝光三角联动**：光圈/快门/ISO 三滑块拖动任一保持 EV 守恒（纯前端计算，公式与工具同源），
  输出 EV 实时显示。
- **四步推理**：tool_result 事件收集为推理链（标题/结果/来源标签），桌面内联 + 移动端
  底部抽屉（sheet）；无工具时回退 card.evidence。
- **概率依据条/相似历史**：本地规则化演示 + 示例数据标注（诚实原则）。
- **验收**：`npm run build` 通过；令牌 diff=0；**/api/decide 真实跑通**（本轮 1 条真实
  query：queued→step→tool_call×3→tool_result×3→step→card→done，conf=medium、evidence=3）；
  后端 212 全绿、ruff 0（未动后端）。双视口媒体查询已落地，截图自检待浏览器环境。
- **commit**：b31f281。

> 按任务单元记录进度与阻塞，供后续接手者审计。格式：任务编号 / 时间 / commit hash / 测试数量。

## 2026-09-08（E7 Web 服务层）

### E7-1 async ChatClient + 全局 LLM 队列（acall + _AsyncGate 串行闸门）— 已提交

- **llm/client.py**：新增 `ChatClient.acall()` async 通道（基于 `openai.AsyncOpenAI`，惰性创建）；
  - 并发边界由 `serial_llm` 配置驱动：True 时经 `_AsyncGate`（容量 1）严格串行——**只串行 LLM
    往返**，工具计算/HTTP 数据请求不进闸门；False 时不设闸门直接并行（ADR-002 平台中立，
    业务代码零改动）；
  - `_AsyncGate`：公平 FIFO 放行（先到先得），线程安全 + 跨事件循环唤醒，支持排队位置查询
    `queue_position()`（供 SSE queued 事件）；
  - 重试/超时迁移为 async 版本（asyncio.sleep 指数退避，不阻塞事件循环）；
  - 同步 `chat()` 路径保持不变（CLI 零改动，旧锁语义不变）。
- **pyproject.toml**：dev 依赖增 `pytest-asyncio>=0.23`，ini 增 `asyncio_mode = "auto"`。
- **测试**：`tests/test_client_async.py` 9 用例（串行/并行/排队位置/重试/重试耗尽/扩展参数
  extra_body/配额记账）。pytest 177 → **186 全绿**；ruff 0 告警；smoke 21 项通过。
- **commit**：371a8af（已 push：1dd6ea4..371a8af）。

### E7-2 SessionManager 会话持久化（JSON 落盘 + LRU 缓存）— 已提交

- **新增 `api/session.py`**（E7-2，A-08 对话持久化）：
  - `SessionRecord`：{history（消息历史）, pipeline（管线上下文快照）, workspace（记忆工作区）,
    user_id（预留）}；`snapshot_context` / `restore_context` 做 Intent/DecisionCard 的
    JSON 安全快照往返（pydantic → dict → 还原，兼容缺失字段）；
  - `SessionManager`：`create()` / `get()` / `restore()` / `save()`——内存缓存（LRU 上限，
    淘汰只移出内存不删磁盘）+ JSON 落盘 `data/sessions/{id}.json`，重启进程可恢复；
  - 默认单进程模型：接口以 session_id 为键、与进程无关，未来多 worker 换后端即可（架构
    v2.0 §2.9）；非法 session_id / 损坏落盘 / 非 JSON 对象均有兜底。
- **测试**：`tests/test_session.py` 12 用例（创建落盘/重启恢复/多会话隔离/LRU 淘汰留盘/
  覆盖更新/快照往返/JSON 兜底/边界）。pytest 186 → **198 全绿**；ruff 0 告警。
- **commit**：3a4fd15（含 api/__init__.py）。

### E7-3 FastAPI + SSE 路由（五端点）— 已提交

- **依赖**：fastapi>=0.110 / uvicorn>=0.29 / python-multipart（pyproject 主依赖），
  httpx>=0.27（dev，测试用）。
- **api/app.py**：`create_app(deps=...)` 工厂（测试注入 Fake；默认真实 Settings）+
  `app = create_app()`（uvicorn lighttrail.api.app:app 可启动）；CORS 本地放开；
  工具全量注册。uvicorn 实测启动成功，/docs 200 且 OpenAPI 含全部五端点。
- **api/routes.py**：五端点 `/api/chat` `/api/decide` `/api/photos/review`
  `/api/profile` `/api/sessions/{id}`；请求模型 pydantic（白拿 OpenAPI）；
  SSE 事件流按 §2.8 协议（queued/step/tool_call/tool_result/token/card/error/done）；
  同步 ReAct/管线经 asyncio.to_thread 在线程池执行 + asyncio.Queue 桥回事件循环，
  不阻塞事件循环（§2.9 线程模型）；Agent 历史从 SessionManager 恢复（新增
  Agent.load_history，CLI 不受影响）；照片上传限 jpg/png、≤10MB。
- **说明**：当前 token 事件按「每轮完整文本」发送（同步通道无流式 SDK），
  逐 token 流式留待后续 streaming 通道。
- **测试**：`tests/test_api.py` 8 用例（chat 全协议/error/decide card/照片 review
  与类型校验/profile 读写/会话 404/OpenAPI）。pytest 198 → **206 全绿**；
  ruff 0 告警；smoke 21 项通过。
- **commit**：ee49bdc。

### E7-4 trace→SSE 桥接（trace 即 UI）— 已提交

- **api/events.py**：事件协议单一事实源——`map_trace_event`（step → SSE step；
  tool → tool_call + tool_result 两条；llm 不入协议）、`sse_text` 帧编码、
  `pump` 队列泵、`TraceBridge`（挂 TraceRecorder.subscribe，经
  call_soon_threadsafe 线程安全入队 asyncio.Queue，attach/detach 生命周期）。
- **api/routes.py 重构**：三处 SSE 端点统一走 TraceBridge + pump；注入型 dispatch
  （测试 Fake）补记 record_tool，使 Web 事件流中工具调用可见（与生产
  registry.dispatch 记录行为一致）。
- **验收**：decide 管线 SSE 收到 step → tool_call → tool_result → card → done
  全序列且顺序正确（tests/test_api_events.py 断言相对顺序 + call/result 成对）。
- **测试**：`tests/test_api_events.py` 6 用例（事件映射 ×3 / SSE 帧 / 线程桥
  转发与 detach / decide 全序列）。pytest 206 → **212 全绿**；ruff 0 告警。
- **commit**：4140df0（含 test_api.py 诊断断言增强）。

### E7-0 真实 Key + SSE 长连接联调（阶段开场任务）— ✅ 已完成

> 执行时机说明：E7-0 需 SSE 基础设施（E7-3/4），故在 E7-4 之后执行——同 E6-0
> 先例（阶段开场任务在依赖就绪后验证，禁止仅凭 mock 判定阶段完成）。

- **验证**：uvicorn 启动真实服务（127.0.0.1:8766）→ `POST /api/chat`
  「现在几点？」最小 query（1.8s 完成，消耗极小）→ SSE 长连接事件序列：
  **queued(position=1) → tool_call(get_current_time) → tool_result → token → done**，
  session_id 正常返回且已落盘——「现有 CLI 链路在 Web 侧可跑」验证通过，
  协议（§2.8）真实链路无结构性缺陷。
- **说明**：token 文本为真实模型回复（终端 GBK 显示乱码仅为控制台编码问题，
  数据本身 UTF-8 正常）；本轮只发 1 条真实 query，未烧配额。
- **commit**：本条为文档记录（无代码改动）。

### E7-5 前端 SPA 工程化 — 已提交

- **frontend/**（Vite + React + TS）：首迭代严格 3 核心页（裁剪原则）——
  对话（ReAct + 一句话决策，SSE 流式渲染 token/工具事件 + 右侧轨迹面板
  trace 即 UI）/ 会话列表（本地记录 → GET /api/sessions/{id} 回放历史与
  最近决策卡）/ 决策卡片渲染（读取 workspace.last_card 结构化渲染：结论 /
  置信度区间条 / 机位 chips / 参数表 / 依据来源标签）。
- **api/client.ts**：fetch 封装 + SSE 帧解析（postSSE）+ EventSource 封装
  （connectSSE）；**api/events.ts** 与后端 api/events.py 映射保持一致（共享
  schema，E7-4 约定同步落地）。设计令牌在 styles.css（黄昏渐变 #E8A23B →
  #5F8DF2 品牌时刻）；移动端特化：≤760px 单列布局 + 触控目标 ≥44px（双视口
  390×844 / 360×780 自检）。
- **验证**：`npm run build`（tsc 严格 + vite）通过；`npm run dev` 启动后 /
  index 200、/api 反向代理到后端 8765 实测（profile 200）；3 页可切换由
  hash 路由实现（构建期 TS 校验）。
- **commit**：ead1e8e（含 package-lock.json；tsbuildinfo 已 .gitignore）。

### E7 阶段总结（2026-09-08）

- **交付**：E7-1 async ChatClient（acall + _AsyncGate 串行闸门）→ E7-2
  SessionManager（JSON 落盘 + LRU）→ E7-3 FastAPI + SSE 五端点 → E7-4
  trace→SSE 桥接（TraceBridge，「trace 即 UI」）→ E7-0 真实 Key SSE 最小
  query 联调通过 → E7-5 SPA 三核心页。
- **测试**：177 → **212**（+35：async 9 + session 12 + api 8 + events 6）；
  ruff 0 告警；smoke 21 项；前端 npm build 通过。
- **平台中立性审计（ADR-002/003）**：新增代码无 ECNU 品牌判断（并发边界仍由
  LLM_SERIAL_LLM 配置驱动；acall 闸门与 sync 锁同为配置驱动，双通道并存）；
  thinking/reasoning_effort 仍收敛在 ChatClient 适配层；API 层零平台语义。
- **Web 形态可演示**：uvicorn + npm run dev 双进程即可演示——对话页 SSE
  流式渲染 + 轨迹面板实时滚动 + 决策卡片（真实/伪造 Key 均可，Fake 由测试
  覆盖，真实链路 E7-0 已验证）。
- **剩余**：E8 评估体系（黄金用例 runner/cassette + LLM-as-judge）→ 开源发布
  → E9 主动提醒被动 MVP。
- **遗留（建议）**：① token 逐字流式（当前为每轮完整文本，需 ChatClient
  流式通道）；② GET /api/sessions 列表端点未加（前端会话列表已用本地记录
  兜底，多端同步时需补）；③ AGENTS.md/长期目标文档的「下一步」待主理人更新
  至 E8（AGENTS.md 修改需小北同意）。

## 2026-09-07


### 收尾：TODO 状态同步 + devlog 总结 — 已提交

- TODO.md：阶段一/二/三全部勾选完成（E6/E7/E8 待办保留）。
- docs/devlog.md：整理条目顺序（E1→E2→E3→E4→E5）并追加收尾总结（commit hash 清单 /
  剩余任务 / 遗留问题 / 平台中立性审计）。
- 最终验证：pytest 145 全绿；ruff 0 告警；smoke 19 项通过；仅 commit 未 push。


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

### E3-2 事件记忆（SQLite 按需检索 + search_memory 工具）— 已提交

- **新增 memory/events.py**：`EventStore`（data/events.db）——add_event / search_events
  （关键词+地点 LIKE、题材精确，参数化防注入，时间倒序）/ occurrence_counts 地点聚合 /
  to_prompt_section 注入文本；事件字段含 **coordinates（精确坐标）与 weather_snapshot
  （天气/天象快照：云量/火烧云评分/月相…）**，是 D2.3-07 复拍提醒的数据前提。
- **MemoryManager**：`retrieve_events(intent)` 地点/题材规则命中才检索 top-k（不常驻），
  `build_injections` 在档案块后追加 events 块；`add_event` 门面；Agent.run 携带意图文本注入。
- **新增 tools/memory_tool.py**：注册 `search_memory`（第 13 个工具）——中文结构字段
  （坐标/快照/器材），ReAct 主动检索入口；默认存储走 settings.data_dir，测试可注入替换。
- **测试**：新增 tests/test_events.py 12 用例（增查闭环 / 注入安全 / 聚合 / 注入文本 /
  意图检索 / 工具返回）。pytest 100 全绿；ruff 0 告警；smoke 19 项通过。


---

### E3-3 语义记忆（精选注入 + double-confirm 写入）— 已提交

- **新增 memory/semantic.py**：`SemanticStore`（data/semantic.json）——`match(intent)`
  按关键词规则命中注入最多 2 条；写入走 **double-confirm**（staged_add 候选池 → confirm/reject
  显式裁决，未确认不计入命中、不落盘；重复确认/空内容防护），防污染；
- **MemoryManager**：`build_injections` 注入顺序 profile → semantic → events；
  门面方法 semantic_propose / semantic_confirm / semantic_reject（E6-3 自动提炼挂载点）；
- **模板**：data/semantic.example.json（结论型经验样例，E6-3 提炼结果格式对齐）。
- **测试**：test_memory.py 新增 6 个语义用例（命中/空库/双确认/否决/注入上限/块顺序）。
  pytest 106 全绿；ruff 0 告警；smoke 19 项通过。

### E4-1 ModelRouter 能力矩阵（收尾）— 已提交

- **能力矩阵可注入已在前序平台中立性重构（ADR-002）中落地，本任务不重做**：
  - 新增 `RouteIntent` 意图声明枚举（DEFAULT/TOOLS/VISION/DEEP_REASONING），
    调用方按能力声明路由，不点名模型品牌；
  - `ReActLoop` 对话路径接路由：`model` 显式指定优先，否则
    `RouteIntent.TOOLS.resolve(router)` → 默认工具模型（ecnu-plus）——验收「loop 实测走 plus」通过；
  - `Agent` 把 router 透传给 loop。
- **平台中立性自查**：未回退品牌硬编码；能力声明、矩阵注入、LLM_ 前缀均沿用 ADR-002；
  单模型全能（tools+deep 同模型）由矩阵表达，不预设互斥。
- **测试**：test_router.py 补 RouteIntent 2 用例 + test_agent.py 补 loop 路由 1 用例。
  pytest 109 全绿；ruff 0 告警。

### E4-2 QuotaLedger 配额账本 — 已提交

- **新增 infra/quota.py**：`QuotaLedger`——`record(model, in, out, cached_in)` 按计价表折算 credits、
  `estimate(CallPlan)` 管线成本预估、`check(estimate)` 三窗口放行判定（5h / 日 / 30 天滚动）、
  `usage()` 水位快照、`degrade(intent, router)` 降级建议文本（随 TraceReport.degradation 输出）。
- **平台中立性（ADR-002）**：计价表（`Pricing`）与降级链（`degrade_map`，按**能力**表达如 deep→tools+thinking）
  全部可注入，默认值对齐 ECNU 官方计价；目标模型名由 ModelRouter 矩阵解析，**不硬编码 ecnu-max → ecnu-plus**。
- **接线**：`ChatClient(quota=)` 成功响应后按 usage 自动记账（含缓存命中价）；
  `Settings.quota_warn_threshold`（LIGHTTRAIL_QUOTA_WARN_THRESHOLD，默认 0.9）；cli 注入账本。
- **测试**：新增 tests/test_quota.py 13 用例（记账/预估/窗口/放行/降级可注入/client 集成/参数校验）。
  pytest 122 全绿；ruff 0 告警；smoke 19 项通过。

### E4-3 reason 通道（深推理，thinking 开启）— 已提交

- **Agent.reason 增强**：`reason(prompt, *, system, model, reasoning_effort, temperature=0.3)`——
  模型走 `RouteIntent.DEEP_REASONING` 路由（默认矩阵 → 深推理模型，**不写死模型名**）、
  `tools=None`、`thinking={"type": "enabled"}`、`reasoning_effort` 透传（平台不支持时不计入 payload）；
- **ChatClient**：`chat()` 新增 `thinking` / `reasoning_effort` 可选透传参数（None 不携带，
  保持平台中立）；`_to_message_dict` 提取 reasoning_content/thinking 摘要（≤2000 字符）；
- **推理可见（M2-04）**：reason 调用记 LLM 事件，thinking 摘要以 reason_thinking 步骤入 TraceReport；
- **测试**：test_reason.py 重写为 5 用例（深推理路由/无工具/thinking/effort 透传/thinking 入报告）。
  pytest 126 全绿；ruff 0 告警；smoke 19 项通过。

### E5-1 Orchestrator 四管线 + PipelineContext — 已提交

- **orchestrator 包**：`context.py`（PipelineContext：intent/data/scores/card）、
  `pipelines.py`（四管线：灵感 / 规划 / 临场 / 复盘骨架 + PipelineEnv 运行环境 +
  代码化评分 + 题材→采集步骤映射）、`orchestrator.py`（意图理解默认模型强约束 JSON →
  管线调度 → 失败降级 ReAct；dispatch 可注入 Fake 数据源）。
- **数据采集直调**（非 ReAct 轮次），每步落 TraceRecorder；评分确定性规则（不让模型自评）；
  综合走 reason 深推理通道（RouteIntent，不写死模型名）。
- **cli --pipeline**：「一句话出方案」入口（编排层与 CLI 打通）。
- **测试**：tests/test_orchestrator.py 9 用例（管线注册/模式映射/灵感管线走通落 trace/
  评分/复盘骨架/plan 端到端/降级 ReAct/卡片上下文携带/渲染完整性）。Fake 数据源 + FakeChatClient，不触网。
  pytest 141 全绿；ruff 0 告警。

### E5-2 结构化输出契约（pydantic + 自愈校验）— 已提交

> **提交顺序说明**：E5-2 的 schemas（Intent/DecisionCard）被 E5-1 四条管线
> 作为前置契约依赖，故契约层先行落地提交，随后提交 E5-1 编排（见下一条）。

- **orchestrator/schemas.py**：`Intent`（subject_type/location/time_hint/mode）、
  `DecisionCard`（conclusion/evidence/confidence 必填——结构性保证 M2 不落空；
  time_window/locations/params/alternatives/degraded 可选）+ `Source`/`ParamSuggestion`/
  `LocationSuggestion`；可作 E7 FastAPI 请求/响应模型（白拿 OpenAPI）。
- **infra/validation.py**：`parse_with_retry(schema, chat, prompt, max_retries=2)`——
  LLM 输出 → 剥 markdown 围栏/截取 JSON → pydantic 校验 → 失败把错误回传模型自愈
  （≤2 次）→ 仍失败抛 `SchemaError`（管线捕获降级 ReAct）。
- **测试**：新增 tests/test_validation.py 6 用例（一次通过/围栏剥离/自愈重试/耗尽抛错/
  必要字段强制）。

### E5-3 一句话出方案闭环（端到端）— 已提交

- **端到端串联**：`Orchestrator.plan(user_request)`——一句话 → 意图（默认模型强约束 JSON）
  → 数据采集（直调工具）→ 代码评分 → reason 深推理综合 → DecisionCard → 中文渲染；
  追问「参数激进一点」经 `_fallback` 转入 ReAct 自由对话，且把最近卡片结论作为
  背景上下文一并带入（追问可在此基础上调整）。
- **cli --pipeline**：「python -m lighttrail.cli --pipeline 这周末想去拍银河」输出结构化
  方案（有 Key 时真实执行；无 Key 提示配置）。
- **黄金场景验证（E8 黄金用例集雏形）**：tests/test_pipeline_e2e.py——银河灵感、
  火烧云临场、黄金用例可解析性、CLI 入口（Fake 编排器）4 用例，全部 Fake 数据源 +
  FakeChatClient，不触网、不依赖真实 API Key。
- **测试数量**：pytest 145 全绿；ruff 0 告警；smoke 19 项通过。

### 真实联调修复（reason thinking 配置化 + Open-Meteo 400 + 评分键对齐）

### ecnu-max think 联调修复（extra_body 兼容 + 默认开启）

### CLI 管线进度输出（trace 订阅 → stderr 进度）— 已提交

> 真实 `--pipeline` 联调时「一条 200 后长时间无输出」——意图解析成功但后续
> 天气采集 / ecnu-max 思考模式均无可视反馈。复用 E2-1 预留的 recorder.subscribe，
> CLI 把 trace 事件打印为 stderr 进度（[意图]/[采集]/[工具]/[综合]/[管线]），
> 不污染 stdout 结果，等待期可见可诊断（定位卡在采集还是深推理）。

— 已提交

> **背景**：上一条修复把 reason thinking 默认关闭，但 ECNU 官方文档明确 ecnu-max
> **支持** `thinking={"type": "enabled"}` + `reasoning_effort`（参数名并无问题）。
> 实测 TypeError 的真正根因是 **openai SDK 版本**：3.1.0 的 `Completions.create()`
> 签名没有 thinking 命名参数 → 未知 kwarg 直接拒绝。

- **ChatClient 运行时探测 SDK 能力**：`_NATIVE_REASON_PARAMS` 检查 create() 是否原生支持
  thinking/reasoning_effort——原生支持走命名参数（规范路径）；否则（如 openai 3.1）经
  **`extra_body`** 携带扩展参数（OpenAI 兼容网关从请求体读取该字段，与命名参数等价）。
  普通调用无扩展参数时零污染。这补全了 ADR-002「并发/能力适配收敛在适配层」的一环。
- **默认开启**：`LLM_REASON_THINKING` 默认 **true**（对齐 ECNU 官方文档，与「默认串行适配
  ECNU」同一叙事）；不支持思考的平台在 .env 关闭即可，业务代码零改动。
- **测试**：test_client_serial.py 新增 3 用例——extra_body 路径 / 原生命名参数路径 /
  无参数零污染（monkeypatch 探测标志强制两条路径）。pytest 149 全绿；ruff 0 告警。
- **验证方式**：未用真实 Key 联调（遵守约定）；extra_body 为 openai SDK 标准通道 + ECNU
  网关按文档读取 thinking 字段，组合可信，待小北本机重跑确认。

— 已提交

> **背景**：小北在真实 API 环境执行 `--pipeline` 与自由对话，暴露三处问题。其中
> thinking 硬编码属于**平台中立性修复**（E4-3 把 ECNU 不支持的扩展参数写死在了 reason 通道）。

- **【平台中立性修复】reason 通道 thinking 配置化**：`Agent.reason` 原先总是发送
  `thinking={"type": "enabled"}` 与 `reasoning_effort`，真实 OpenAI 兼容接口（ECNU 实测）不支持 →
  SDK `TypeError` → 管线必挂。现改为 `Settings.reason_thinking`（`LLM_REASON_THINKING`，默认 false）
  控制，关闭时 reason 与普通调用同构（不带扩展参数），通用接口开箱即用；支持的平台在 .env 开启。
- **Open-Meteo 400 修复**：上游 hourly 变量 `cloud_cover_medium` 已失效（实测 400 且报变量损坏），
  从 weather_forecast / sunset_glow_score 两处请求中移除；`_fetch_json` 对 4xx 不再盲目重试 2 次，
  立即抛 `WeatherError` 并携带服务端 reason（模型/用户可见可读原因）。
- **评分键对齐**：pipeline 代码化评分读取 `"评分"`，真实工具返回键为 `"评分（0-100）"`（月相为
  `月相名称`/`月光影响建议`）→ 评分从不进入 ctx.scores。改为按真实键提取并前缀匹配；测试假数据
  同步为真实键名防回归。
- **测试**：test_reason.py 改为「默认不带扩展参数 + 配置开启时携带」；test_orchestrator / e2e 假数据
  键名与真实工具对齐。pytest 146 全绿；ruff 0 告警。

## 收尾总结（2026-09-07，阶段三完成）

### 已完成任务（commit hash 清单）

| 任务 | commit | 测试 |
|---|---|---|
| 平台中立性重构（ADR-002） | a7924c7 | 43 |
| 前置工程遗留清理 | 01d2d07 | 43 |
| E1-2 ContextBuilder 五层组装 | 910f521 | 57 |
| E2-1 TraceRecorder + 订阅 | e331535 | 69 |
| E2-2 trace 注入 + TraceReport | a7af5ec | 80 |
| E3-1 用户档案 | f0948f2 | 88 |
| E3-2 事件记忆（SQLite + search_memory） | 3045ec6 | 100 |
| E3-3 语义记忆（double-confirm） | a32a874 | 106 |
| E4-1 ModelRouter 能力矩阵收尾 | 490c369 | 109 |
| E4-2 QuotaLedger 配额账本 | 5b5fd5f | 122 |
| E4-3 reason 深推理通道 | c705749 | 126 |
| E5-2 结构化输出契约 | 26ca6e3 | 141 |
| E5-1 Orchestrator 四管线 | ae6b38c | 141 |
| E5-3 一句话出方案闭环 | 27035d1 | 145 |

**最终状态**：pytest **145 全绿**（相对基线 43 新增 102 用例）、ruff 0 告警、smoke 19 项通过；
仅 commit 未 push（推送需小北确认）。

### 剩余任务（下一接手者）

- **E6（多模态与差异化）**：E6-1 照片分析智能工具（Pillow/exifread、深度=1 红线、schema 输出）、
  E6-2 照片反推方案（reverse_engineer_photo）、E6-3 语义记忆自动提炼（semantic.extract_from_events）。
- **E7（Web 服务层）**：E7-1 async ChatClient（并发边界由 serial_llm 驱动）、E7-2 SessionManager、
  E7-3 FastAPI+SSE、E7-4 trace→SSE 桥接（TraceRecorder.subscribe 已预留）、E7-5 SPA。
- **E8（评估体系）**：黄金用例集雏形已在 tests/test_pipeline_e2e.py（GOLDEN_CASES），扩展为 evals/。

### 遗留问题 / 注意事项

1. **tests/ 目录 ACL**：本沙箱会话下 tests/ 无 CodexSandboxUsers 写权限，测试文件更新需
   「temp 生成 → escalated copy」；`pytest tmp_path` 亦不可用（pytest-of-North 目录不可扫描），
   新测试请用 tempfile.mkdtemp 自建房（见 test_memory.py 的 memory_dir fixture）。
2. **TODO.md 同受目录 ACL 限制**，更新需走「temp → escalated copy」流程。
3. **管线数据采集**：E5 管线默认坐标写死上海（31.23,121.47），档案常去机位尚无精确坐标字段；
   E6/E7 阶段可在记忆层补「机位坐标」字段后按档案精确定位。意图 time_hint 的「周末」近似为明日。
4. **QuotaLedger 默认计价表**：对齐 ECNU 官方计价，换 API 须注入新 pricing（勿改默认表）。
5. **review 管线为骨架**：输出骨架卡片，E6-1 照片分析接入后填充。

### 平台中立性状态（ADR-002 审计）

- **已收敛**：并发策略可配置（LLM_SERIAL_LLM）、能力矩阵可注入（ModelRouter capability_matrix）、
  配置 LLM_ 前缀、reason/意图解析全部走 RouteIntent（不写死模型品牌）；E5 编排层零 ECNU 品牌判断。
- **残留（历史默认值，合规）**：`ecnu-plus`/`ecnu-max` 仅出现在 config.py DEFAULT_*、quota.py
  默认计价表键、.env.example 注释、docs/adr 历史叙述中——符合 ADR-002「模型名只出现在 config 默认值/.env/能力矩阵」。
- **本次会话新增修复**：文档中 `LIGHTTRAIL_SERIAL_LLM` 命名残留统一为 `LLM_SERIAL_LLM`（a7924c7）。

## 2026-09-07（晚）· 全面检查与文档同步 — 软件开发团队

> 齐活林（主理人）组织全面盘点 Codex 本轮 E1–E5 交付（20 commits），架构师高见远出后续路线，主理人执行文档同步。

- **验证**：pytest 149 全绿 / ruff 0 告警 / smoke 19 项 / 13 工具注册实测 / 工作区干净（20 commits 待 push）。
- **后续路线（v2.2）**：E6 多模态（含新增 E6-0 真实联调基线 / E6-4 复盘管线填充 / E6-5 收口）→ E7 Web（E7-0 联调开场，SPA 3+3 页两迭代）→ E8 评估（黄金用例改各阶段增量交付）→ 开源发布 → **新增阶段八 E9 主动提醒被动 MVP**（复拍提醒 + 就近推荐；定时推送延后）。
- **文档同步**：README（状态勾选/目录结构/13 工具/149 测试）、AGENTS.md（当前阶段/目录/串行策略 ADR-002 化）、DEVELOPMENT-ROADMAP v2.2（§1.1 现状/PRD 矩阵全勾/E9 阶段）、architecture v2.0.1（基线 + ADR-002 补注）、TODO 同步、PRD-v0.2.md 重命名为 PRD-v0.3.md（全局引用替换）。
- **遗留处置排期**（交叉引用 §收尾总结遗留问题）：push 待小北确认（并入 E6-0）；坐标写死上海 → E6-3 favorite_spots 根治；review 骨架 → E6-4 填充；.env.example 补配置 → 开源发布 J-1。

## 2026-09-07（深夜）· 文档补漏核查 — Codex 开发会话

> 应小北要求，在 df63d9e 全面同步之后再次核查 docs 未归档文档，发现并修复如下遗漏：

- **docs/codex-longterm-goal.md 换代 v2（E6 起点）**：原文停留在 E5 前基线（目标「做到 E5」、
  roadmap v2.1 / 12 工具 / 43 测试 / f344eb7 / 「平台中立性重构待审阅提交」待办）。重写为
  E6-0~E6-5 主线目标，基线刷新（13 工具 / 149 测试 / roadmap v2.2 / 21+ commits 未 push 待确认）；
  平台中立原则保留并增补第 5 条（扩展参数收敛适配层，见 ADR-003）。
- **新增 ADR-003-reason-extension-params.md**：本次真实联调确立的架构决策正式入库——
  thinking/reasoning_effort 扩展参数 = 配置化（LLM_REASON_THINKING 默认 true，适配 ECNU）
  + SDK 能力探测（原生命名参数 vs extra_body）双路径，业务层只声明开关（符合 ADR-002
  「适配收敛适配层」的延续）。
- **AGENTS.md 一致性修正**：df63d9e 中 E6 子任务顺序写反（E6-3 复盘填充 / E6-4 语义提炼），
  已对齐 roadmap v2.2（E6-3 语义记忆提炼 → E6-4 复盘管线填充）。
- **Roadmap 小残留**：§3.4 任务编号范围 E8-2 → E9-2（并注明 E9 为产品化增量）；J-1 README
  目标「现状 12 工具」过时改为随进度核对（当前 13）。
- **TODO 头**：任务编号范围精确为 E1-1…E9-2（E9-3 定时推送为可选待议，未入路线图）。
- 未动（无需更新）：architecture v2.0.1（df63d9e 已同步基线；E9 无新架构演进）、
  PRD-v0.3（重命名已完成）、devlog 历史条目。

## 2026-09-07（深夜）· E6 阶段启动（长期目标 v2）

> 依据 docs/codex-longterm-goal.md v2（E6 起点）推进阶段四。

### E6-0 真实联调基线验证 + 远程基线 — ✅ 已完成（2026-09-07 深夜，commit 1076e81）

### E6-0 真实联调基线验证（授权后执行）— 已提交修复

> 小北授权（2026-09-07 深夜）后执行 devlog E6-0 清单。**结果：四管线真实 query 全部跑通，
> push 成功（66cb492..1076e81，28 commits），origin/main 与本地一致（rev-parse 相等）。**
> 附带记录：授权前就绪核查、授权后执行清单见下方引用块。

- **四管线真实 query 全部跑通**（ECNU 真实 Key + Open-Meteo）：
  - 灵感「这周末想去拍银河」→ 9/12 首选 / 9/13 云量大否，机位（天荒坪/太子尖/南汇嘴）+ 参数（14-24mm f/2.8 20-25s ISO1600-3200）✓（reason ~3.5min，thinking 摘要入报告）；
  - 规划「周末两天三机位对比」→ 9/12 崇明日落+夜景双保险，逐时云量依据 ✓；
  - 临场「今晚火烧云值得冲吗」→ 评分 43，建议不专程，若在附近守黄金时刻 ✓；
  - 复盘（UI 稿 d2.png 走多模态链路）→ 识别画面计划并给处方 ✓；TraceReport 落点正常。
- **真实链路验证结论**：thinking extra_body 兼容生效（reason_thinking 步骤出现）、
  Open-Meteo 修复生效、评分键对齐生效；未发现结构性架构问题。
- **真实联调修复 1 项**：exifread 对无 EXIF 文件抛 ExifNotFound → 原代码打 warning 噪音；
  改为静默返回空 dict（截图/无 EXIF 是常态），其它读取异常仍 warning。
- **遗留（待小北）**：真实摄影照片 ≥3 张的视觉处方实测——本机暂无摄影样张，已用 UI 稿
  验证链路；请放图到 data/photos/ 后补测（analyze_photo/reverse_engineer_photo）。



> **授权前就绪核查（2026-09-07 深夜，goal continuation）**：
> - git remote 已配置：`origin = https://github.com/North-Q/LightTrail.git`，upstream=origin/main；
> - f344eb7 之后 **26 个 commit 未推送**（E5/E6 全量 + 文档）；
> - .env 已含 API Key（LLM_ 或 ECNU_ 别名），真实联调开箱即可用；
> - data/events.db 已存在（~~联调会写入事件记忆~~，无害）。**→ 2026-09-14 更正：实测 events 表 0 行，`add_event` 全仓无生产调用方——联调并不会写入事件（见 B4 期发现 F1 / TODO.md）。**
>
> **授权后执行清单（E6-0）**：
> 1. 灵感/规划/临场/复盘四管线各 ≥1 条真实 query（CLI `--pipeline` 与 `Orchestrator.review`，
>    需 1 张真实照片走 analyze_photo）；
> 2. 真实照片 ≥3 张验证视觉调用 + 视觉单价计入 QuotaLedger 预估；
> 3. TraceReport/进度输出留档（stderr 已输出 [意图]/[采集]/[综合] 阶段）；
> 4. 发现的问题按「真实联调修复」流程修（commit + devlog 标注）；
> 5. `git push` 建远程基线（26 commits），推后核对 origin/main 与本地一致。

### E6-1 照片分析智能工具

### E6-2 照片反推方案（D1.2 图 → 复刻计划）

### E6-3 语义记忆提炼（事件聚合 + double-confirm + favorite_spots 沉淀）

### E6-4 复盘管线填充（review 闭环）— 已提交

- **pipelines.py ReviewPipeline 闭环**：照片（image_path）→ env.photo_analyze（默认绑
  E6-1 analyze_photo，测试可注 Fake）→ EXIF + 评价 + 处方 → **与历史计划对账**（EXIF
  光圈/快门/ISO 与计划 params 宽松数值对比，产出差异行，键名统一避免 E5 评分键错位教训）→
  reason 深推理 → 复盘 DecisionCard；缺图返回 review_missing_image 骨架卡（不触 LLM）。
- **Orchestrator.review / run_review**：`review(image_path, focus, plan_reference)`——计划
  引用支持 DecisionCard JSON（取其 params）；失败自动降级自由对话。
- **测试**：test_pipeline_e2e.py 新增 3 个复盘用例（闭环/对账差异进 prompt/缺图骨架）+
  test_orchestrator 骨架测试更新为缺图语义。pytest 176 全绿；ruff 0 告警；smoke 19 项。

— 已提交

- **events.py**：事件表新增 `outcome`（success/fail）列 + 轻量迁移（旧库自动 ALTER），
  成功率统计的数据前提；add_event/search/to_record 全链路支持。
- **semantic.py**：`extract_from_events(events, min_samples=3, min_success_rate=0.7)`——
  按题材聚合成功率，命中规则产出 `SemanticCandidate`（候选）；`SemanticStore.contains`
  防重复沉淀。
- **manager.py**：
  - `sediment_semantics()`：全量事件 → 规则提炼 → staged_add 进待确认队列（未确认不注入，
    须 semantic_confirm 才写入 semantic.json 参与第④层注入——double-confirm 防污染）；
  - `sediment_favorite_spots(min_count=2)`：高频带坐标机位沉淀进档案 `favorite_spots`
    （新字段，含 名称/纬度/经度/题材）——为 E9-2 就近推荐与管线按档案定位根治「坐标写死上海」。
- **profile.py**：`favorite_spots` 字段（_FIELDS/展示/存取整链），注入段输出「常去机位：名(lat,lng)」。
- **测试**：tests/test_semantic.py 8 用例（outcome/迁移/规则阈值/double-confirm 生效链/防重/
  favorite 沉淀与去重/无坐标跳过）。pytest 173 全绿；ruff 0 告警；smoke 19 项。

— 已提交

- **tools/photo_analysis.py 增 `reverse_engineer_photo`**（第 15 个工具）：从参考图反推
  场景/光向/推断时段/机位特征/后期风格 + 复刻计划；输出走 `PhotoReverseReport` schema
  （schemas.py 新增契约，replication_plan 必填保证「在哪/什么时候/怎么拍」三要素）；
  复用 E6-1 的编码/EXIF/器材/VISION 路由/自愈机制。
- **Orchestrator.reverse_plan(image_path, note, equipment)**：参考图 → 多模态反推 →
  候选日采集（未来 3 天取云量最低日 + sun_times/moon_phase + 档案候选机位）→
  reason 深推理综合 → 复刻计划 DecisionCard → 渲染；失败自动降级自由对话。
- **测试**：tests/test_reverse_plan.py 5 用例（工具消息含 image_url/note/VISION 路由、
  自愈、注册、Orchestrator 端到端、失败降级）。pytest 165 全绿；ruff 0 告警；smoke 19 项。

 — 已提交

- **tools/photo_analysis.py**：注册 `analyze_photo`（第 14 个工具）——EXIF + 画面多模态 →
  构图/曝光/色彩评价 + **结合器材的可执行处方**（D4-04）；
  - 深度=1 红线：内部不调用 registry.dispatch（测试断言调用形式不存在）；
  - 走同一并发边界与配额账本（模块级 ChatClient = settings 串行 + QuotaLedger；set_client 可注入 Fake）；
  - 多模态模型按 `RouteIntent.VISION` 路由（默认矩阵 → plus）；tools=None；
  - 图片控 token：最长边 ≤1024 缩放 + JPEG q88 + base64 data URL（体积 <500KB 验证）；
  - EXIF 经 exifread 提取，tag 尾部完整匹配避免 Model/LensModel 误中；无 EXIF 空 dict 不阻塞；
  - 输出走 **PhotoAnalysisReport schema**（schemas.py 新增契约，E5-2 自愈机制复用，
    首轮外 ≤2 次纠错重试，仍失败抛 PhotoError）；
  - 器材来源：显式 equipment 参数优先，缺省读 data/profile.json 档案。
- **依赖**：Pillow>=10.0、exifread>=3.0（pyproject + requirements 声明，装进项目 .venv）。
- **测试**：tests/test_photo_analysis.py 11 用例（编码/EXIF/多模态消息含 image_url 与 VISION 路由/
  自愈重试/连续失败/器材档案与显式覆盖/注册/红线）。pytest 160 全绿；ruff 0 告警；smoke 19 项。
- **待授权窗口**：真实照片 ≥3 张实测 + 视觉调用单价入 QuotaLedger 预估（并入 E6-0 真实联调）。



- **状态**：未开始。前置为「真实 Key 联调 + push 20+ commits 建远程基线」，均属需小北确认的外部动作。
- **记录**：按长期目标阻塞规则如实记录，跳到下一个不依赖它的任务（E6-1 代码侧 Fake/mock 可先行；
  E6-1 的「真实照片 ≥3 张实测」验收项并入 E6-0 授权窗口执行）。
- **建议**：小北授权后按 roadmap v2.2 E6-0 验收执行（四管线真实 query + trace 留档 + push）。

### E6-5 阶段收口（黄金用例增量 + 文档同步）— 已提交

- **黄金用例增量（E8-1 策略落地）**：`evals/golden/E6-photo-cases.json`——8 条照片分析/
  反推/复盘结构化用例（request + expect 关键词），结构校验测试护航（≥8、id 唯一、字段完整）。
- **smoke 扩展**：照片分析/反推工具注册检查（19 → 21 项）。
- **文档同步**：README（15 工具/176 测试/E6 勾选）、TODO（E6 勾选 + E6-0 阻塞标注）、
  roadmap v2.3（现状/阶段表/规模）、AGENTS（下一步指向 E7，E6-0 授权前置说明）。
- **回归**：pytest 176 全绿；ruff 0 告警；smoke 21 项通过。

### E6 阶段总结（2026-09-07 深夜）

- **交付**：E6-1 照片分析（analyze_photo，深度=1 红线）→ E6-2 反推（reverse_engineer_photo +
  Orchestrator.reverse_plan）→ E6-3 语义提炼（outcome 字段+迁移、sediment_*、favorite_spots
  沉淀根治坐标写死数据前提）→ E6-4 复盘闭环（照片分析 + 计划对账差异报告）→ E6-5 收口。
- **测试**：149 → **176**（+27）；工具 13 → **15**；ruff 0 告警；smoke 21 项。
- **阻塞（如实记录）**：E6-0 真实联调基线 + push 需小北授权——含「真实照片 ≥3 张实测
  视觉调用」「四管线真实 query 留档」；代码侧验收已由 Fake 全量覆盖，授权窗口执行即可。

## 2026-09-09 · 前端对齐设计稿：规范与计划更新（软件开发团队 / 主理人执行）

> 小北反馈：现有 `frontend/`（E7-5，3 个窄功能页）效果太差，满意的是 `docs/design/delivery/lighttrail-prototype.html`（6 页旅程设计稿）。

- **根因**：E7-5 按「首迭代 3 核心页」裁剪执行，设计稿的 6 页决策旅程信息架构（总览/D1/D2/D3/D4/M1）未落地；且实际 styles.css 令牌与真源漂移（--bg #12121c ≠ #0A0D14、--good #7fe0a3 ≠ --semantic-go #3FCF8E、缺 sky-band/字体三栈）。
- **正向确认**：后端 E7-3 五端点 + SSE 8 事件已覆盖设计稿 90% 数据需求 → 前端补齐是纯前端工作，不动后端。
- **产出**：
  1. 新增 `docs/design/FRONTEND-SPEC.md`（v1.0，前端唯一 spec：6 页信息架构、令牌全集精确提取、组件规格、组件→API 映射、可解释性三铁律、移动端规范、Fake 标注原则）
  2. 原版归档：`docs/design/archive/`（DESIGN-OVERVIEW-v1.1 / 交付说明-v1.1 / lighttrail-prototype-v1.1）——设计文档与原型 html 原路径保留不删
  3. DESIGN-OVERVIEW 升 v2.0（补「设计稿 vs 前端差距」小节 + spec 引用）；交付说明升 v2.0（日期勘误 + §6 接入方向改为已实现工具/端点映射）
  4. ROADMAP v2.4→**v2.5**：新增阶段五·补 E7-6~E7-10（前端旅程页对齐），排 E8 前
  5. TODO：阶段五·补 E7-6~E7-10 待办；阶段六 E8 退为后续
  6. codex-longterm-goal v4→**v5**：主线改「阶段① E7-6~10 前端对齐 → 阶段② E8 → 阶段③ 开源/E9」；新增前端对齐硬约束（令牌只取真源、后端不动、Fake 标示例数据、三铁律门禁、3 页融合不删）

### E7-10 D4 复盘页 + 解释中心（铁律③）+ 阶段收口 — 已提交

- **D4Page**：批量上传（multiple jpg/png ≤10MB）→ 逐张跑 `/api/photos/review` →
  复盘卡 → 4 维分析（曝光/构图/色彩/时间，从复盘卡 evidence 按 field 映射）+ 可执行
  处方（params 按高/中/低优先级渲染）；无真实卡时显示结构正确的示例并标注「示例」。
- **解释中心（铁律③ 勾验 ✓）**：页脚「数据与依据 · 解释中心」模态持续汇总各页
  tool_result.data_source 清单（SourceContext 全局登记），含更新时间与示例标注——
  每个决策都可溯源。
- **阶段出口检查单（阶段五·补）**：
  - [x] 6 页路由全部可访问可切换（#/home #/d1 #/d2 #/d3 #/d4 #/m1 + 旧 hash 别名）
  - [x] 设计令牌与真源 :root 逐项 diff=0（scripts/token-diff.mjs，32 项）
  - [x] 三铁律逐条勾验：①置信度三层（D3）②语义色+图标+文字（D3 三态卡）③解释中心（D4 收口）
  - [x] 原 3 页功能不丢：对话→D1、会话列表→总览、决策卡→D3 底座
  - [x] npm run build 通过；pytest 212 全绿、ruff 0（纯前端改动，后端零回归）
  - [~] 双视口自检：390×844/360×780 媒体查询与触控 ≥44px 已落地并过 CSS/构建检查；
    截图式逐页自检需浏览器环境，作为遗留建议由主理人/人工复检
- **commit**：0176ee1。
