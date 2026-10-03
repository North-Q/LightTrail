# LightTrail 代码与文档综合审查报告

**审查日期**：2026-10-03
**审查基线**：`git HEAD 9f7beac`（C1 批次）+ 工作区未提交改动 117 文件 / +6604 行 / -1789 行
**审查范围**：src（83 文件 14991 行）、tests（58 文件 9441 行）、frontend/src（31 文件 5645 行）、evals、docs
**审查依据**：`docs/CODE-REVIEW.md`（同日建立）
**门禁实测**：`ruff check .` 通过；`ruff format --check` **3 文件不符合**

---

## 摘要

| 级别 | 数量 | 说明 |
|---|---:|---|
| 🔴 Blocker | 6 | 含 1 项版本控制风险、3 项并发/资源缺陷、2 项发布前必修 |
| 🟡 Suggestion | 14 | 契约缺口、重复样板、可维护性债、文档漂移 |
| 💭 Nit | 9 | 风格、命名、文档小缺口 |

**总体评价**：光迹的架构重建（B0–B7）与真实性收口（R0）成效显著——分层门禁、契约单一真源、用户命名空间隔离、确定性评分这些「地基」做得比同类个人项目扎实得多，历史上报出的死锁三 bug、工具反向依赖、前端伪造数据都已确认修复。**当前的主要风险不在代码质量，而在工程流程**：R0+S1 两批成果（6604 行）全部悬在未提交状态，且格式门禁、并发测试覆盖存在系统性缺口。代码本身没有发现「必须推倒重来」级别的问题，这与路线图 §1.3 的判断一致。

**本次审查的局限（必须声明）**：原计划分派 4 个并行代理分别审查工具层、记忆/基础设施层、前端、测试体系，四者均因 Token 频率限制（429）失败、无产出。以下发现中，工具层、记忆层、编排层、API 层、配置层、S1 规划服务由审查者逐文件细读得出（含行号证据）；**前端页面逻辑、AST 架构门禁实现、evals 断言强度、tests 逐用例质量未经细读**，相关结论基于文档声明与抽样，标 ❓ 待补。这四个模块的补充审查建议在额度恢复后（2026-10-04 02:43 后）独立进行。

---

## 🔴 Blocker

### B-1 版本控制失效：R0 与 S1 两批成果全部未提交

**证据**：`git status --short` 显示 117 个文件改动、+6604/-1789 行；`git log` 最新提交为 `9f7beac feat(C1): 增加多交通赶场决策`。

**影响**：R0-1～R0-8（统一装配、用户命名空间、日期语义、语义评估 v2、工程门禁恢复）、S1-1～S1-5（PlanningRequest/SpotPlan 契约、机位 CRUD、M1 编辑器、D2 结构化计划、快照导出回放）两批已通过闸门验收的成果，**全部没有版本历史**。任何一次误操作、磁盘故障或强制切换分支都会造成不可逆丢失；同时无法用 `git bisect` 定位回归、无法为本批审查提供稳定的 diff 基线。

**建议**：
1. 立即在本地按批次拆分提交（R0 一批、S1 一批，或按原子任务更细），commit 信息沿用项目中文规范；本地 commit 不需要小北授权；
2. 将「批次闸门通过后必须 commit」写入 `docs/CODE-REVIEW.md` §4.2 与路线图统一完成定义（本次已在 CODE-REVIEW.md 中固化）；
3. push/PR 仍按既有规则等待小北授权。

### B-2 `MemoryManager._state()` 无锁共享字典（多线程）

**位置**：`src/lighttrail/memory/manager.py:405-434`

**证据**：`self._users` 是普通 dict（`__init__`:89），`_state()` 先查后写（410-433）之间没有任何锁。而 Web 路径下 `_state()` 实际在 worker 线程中执行（`api/routes.py` 的 `asyncio.to_thread` 分支）。

**影响**：两个并发请求可能同时通过 `if user_id in self._users` 检查，各自构造一套 `_UserMemory`；后写者覆盖先写者，导致**两个线程持不同 `EventStore`/`SemanticStore` 实例操作同一个 SQLite 文件**。轻则重复初始化、重则该用户的机位更新丢失（`_write_spots` 在各自的内存视图上做读改写）。当前单用户本地部署触发概率低，但这是「本地正常、上线出错」的典型隐患。

**建议**：参考同项目 `SessionManager` 的成熟做法（`api/session.py:174` 用 `threading.Lock` 保护缓存字典），给 `_users` 加锁，或改用 `dict.setdefault` 的原子性语义。

### B-3 照片上传先读全量入内存、后校验大小

**位置**：`src/lighttrail/api/routes.py:444-448`（复盘）、`477-481`（反推）

**证据**：
```python
content = await file.read()          # 先把整个文件读进内存
if len(content) > _MAX_PHOTO_BYTES:  # 之后才知道超限
    raise HTTPException(status_code=413, ...)
```

**影响**：`_MAX_PHOTO_BYTES` 名义为 10MB，但校验发生在读入之后，防线形同虚设——攻击者上传 2GB 文件即可让单进程服务内存耗尽。当前仅本机访问，风险受限；`docs/architecture.md` 已声明「不得对公网开放」，但这不构成不修的理由。

**建议**：改为流式读取并按累计长度提前中断；或用 `UploadFile.size` 先做前置判断，再分块读入。两个端点应共用同一实现（见 S-6）。

### B-4 `ruff format` 未被门禁执行，3 个文件不符合格式标准

**证据**：实测 `ruff format --check src/lighttrail/config.py src/lighttrail/api/session.py src/lighttrail/composition.py` → **3 files would be reformatted**（config.py:185 缺空行、composition.py 尾行无换行、session.py 有多余空行）。`docs/devlog.md`（2026-10-01 条目）也记录了「config.py 的额外全文件格式检查仍发现既有排版差异，未为单行变更批量改写」。

**影响**：项目声明的 ruff 门禁只覆盖 `ruff check`（lint 规则），`ruff format`（排版）实际未纳入，导致格式标准名义存在、实际漂移。审查者已实测确认，不是历史遗留的猜测。

**建议**：① 一次性执行 `ruff format` 统一排版并提交；② 将 `ruff format --check` 加入门禁命令与 `docs/AGENT-ROADMAP.md` §11 统一完成定义第 4 条。

### B-5 四管线重复实现配额护栏，与 Orchestrator 双份判定

**位置**：`src/lighttrail/orchestrator/pipelines.py:579-609`（`Pipeline.run`）与 `src/lighttrail/orchestrator/orchestrator.py:277-308`（`Orchestrator.plan`）

**证据**：两处出现几乎逐字相同的两段逻辑——`quota.check()` 失败返回 `quota_exhausted` 卡片、`quota.degrade(RouteIntent.DEEP_REASONING, router)` 返回 `quota_high_water` 卡片（`conclusion`/`verdict`/`confidence` 完全一致）。四管线经 `plan()` 调用时会被判定两次。

**影响**：两条独立维护路径，改一处漏一处即产生「同一请求行为不一致」的静默分歧；也让「配额策略」出现两个事实真源。属于架构性的正确性风险（不是风格问题）。

**建议**：把配额判定收敛到单一位置——建议全部上移到 `Orchestrator.plan()` 入口，`Pipeline.run` 只保留 `finalize_card`；或反之让 `plan()` 委托给管线。二者只留其一。

### B-6 事件接口 `timestamp` 无格式校验（P1 前置）

**位置**：`src/lighttrail/api/routes.py:168`（模型定义）、`898`（使用）

**证据**：`EventCreatePayload.timestamp: str = Field(default="")`，服务端直接 `timestamp = payload.timestamp or datetime.now(...)` 后写库，不做 ISO 8601 解析。

**影响**：任意字符串（含超长文本、非法日期）都会被持久化为事件时间戳，污染 D4 复盘与后续语义提炼的时间轴；日历/检索一旦解析该字段即可能抛错。

**建议**：改为 `datetime | None` 类型，或加 `field_validator` 校验 ISO 8601 且带时区（项目已有 `_require_timezone` 可复用）。标 **P1 前置**：公开部署前必须收口。

---

## 🟡 Suggestion

### S-1 chase-plans 按机位「名称」匹配档案，与 S1 的稳定 ID 语义不一致

**位置**：`src/lighttrail/api/routes.py:918-933`

**说明**：`/api/chase-plans` 用 `item.name` 在 `valid_favorite_spots()` 中查档案，重名即拒绝（919 行有去重检查）。而 S1 全链路已确立「机位以稳定 ID 为准」的语义（`/api/spot-plans` 用 `site_id`，`/api/map/convert` 用 `spot_ids`）。

**影响**：同一系统内两套机位标识约定并存；改名后赶场请求即失效，跨用户重名也无法区分。属架构一致性问题。

**建议**：`ChaseSitePayload` 改用 `site_id`，与 S1 契约统一；赶场属实验能力（README 已标注「实验」），可排在 O1-2/O1-3 一并修正。

### S-2 四个 SSE 端点约 60 行样板重复 4 次

**位置**：`src/lighttrail/api/routes.py:305-348`（chat）、`365-415`（decide）、`503-559`（review）、`574-625`（reverse）

**说明**：`queue 创建 → recorder/bridge.attach → run 闭包 → worker → stream → _stream_response` 六段结构四次近乎逐字复制；`worker` 的异常处理、`DoneEvent` 发送、`bridge.detach()` 各自手写。

**影响**：任一端的生命周期修复（如断连清理、错误脱敏）需同步改四处，遗漏即产生不一致——项目已有的「D4 临时文件竞态」教训正是这类问题的实例。

**建议**：提取 `_run_sse_worker(deps, session, context, work_fn)` 工厂，端点只提供各自的业务闭包与事件映射。

### S-3 多个端点返回裸 `dict`，契约未进 OpenAPI

**位置**：`src/lighttrail/api/routes.py:891-907`（`/api/events`）、`910-959`（`/api/chase-plans`）、`962-973`（`/api/sessions/{id}`）、`732-755`（DELETE 机位）

**说明**：这些端点无 `response_model`，返回 `dict[str, Any]`。而项目既有的「契约单一真源、OpenAPI 生成前端类型」原则要求响应有 Pydantic 契约。

**影响**：前端消费这些端点时只能手写类型或 `any`，正是历史上「契约双份手写」问题的温床。

**建议**：为四个响应补 Pydantic 模型（`EventCreatedPayload`/`ChasePlanResponse`/`SessionPayload`/`DeletedSpotPayload`），进 components 后由 `gen:api` 统一生成。

### S-4 机位机型识别硬编码在工具层

**位置**：`src/lighttrail/tools/exposure.py:233-242`（`_camera_key`）

**证据**：
```python
if "S5M2" in normalized:
    return "S5M2"
if "A7C2" in normalized:
    return "A7C2"
if "NIKONFE" in normalized or normalized == "FE":
    return "Nikon_FE"
```

**影响**：① 与 ADR-002「业务代码不写品牌判断」的精神冲突（虽非模型品牌，但仍是硬编码的设备判断）；② 新增机型必须改代码，与知识库 `camera_specs` 表「可扩展」的设计意图相悖；③ 用户器材配置中的其他机型静默拿不到 NPF 计算。

**建议**：把机型名 → 知识键的映射移入 `camera_specs` 知识表的别名字段，工具只做归一化查询。

### S-5 `_values_equivalent` 对分数快门与小数秒会误报差异

**位置**：`src/lighttrail/orchestrator/pipelines.py:760-765`

**证据**：用 `re.findall(r"\d+\.?\d*", text)` 抽数字后比较。计划参数「1/125」抽出 `['1','125']`，而 EXIF 实拍若为小数秒「0.008」抽出 `['0.008']`，判定不等。

**影响**：D4 复盘会把「等效但表示不同」的快门误报为参数偏差，给用户错误的复盘结论（这正是 R0-7 强调的「语义正确性」问题）。

**建议**：先把 `a/b` 形式与小数秒统一归一为秒值再比较。

### S-6 `datasource` 必填但恒传 `None`，用 `type: ignore` 掩盖

**位置**：`src/lighttrail/composition.py:122`、`293`

**证据**：`ToolContext(datasource=None,  # type: ignore[arg-type] - 数据源接入保持现有工具实现)`——契约声明必填，装配根两处都传 None 并压制类型检查。

**影响**：契约与实现不符；任何按契约定类型读取 `ctx.datasource` 的工具会在运行期拿到 None。

**建议**：把 `ToolContext.datasource` 改为 `Optional`，或从契约中移除，二者取一以消除 `type: ignore`。

### S-7 `build_application` 的 `registry`/`runtime` 回退分支可读环境变量，违反自身声明

**位置**：`src/lighttrail/composition.py:245`、`382`、`286`

**证据**：`factory = photo_client_factory or (lambda: build_client(load_settings()))`；`build_runtime` 与 `build_application` 的缺省分支会自行调 `build_knowledge(settings)` / `ModelRouter()`。而模块 docstring 明确声明「参数一律显式传入（不读全局单例、不读环境变量）」。

**影响**：装配根的「显式依赖」承诺在回退路径上不成立，依赖审计时可能漏看隐式引用。

**建议**：缺省参数改为必需参数并在调用点显式传入，或删除回退分支（生产路径本就总是显式传入）。

### S-8 `run_reverse` 先做昂贵多模态调用、后检查用户机位

**位置**：`src/lighttrail/orchestrator/orchestrator.py:440-451`

**证据**：`self._photo_reverse(image_path, note, equipment)`（多模态 LLM 调用）在 441 行执行后，442 行才检查 `_candidate_sites(...)` 是否为空；无机位时会直接返回 `missing_favorite_spot` 卡片，此前那次视觉调用**完全浪费**。

**影响**：新用户（尚无收藏机位）每次尝试照片反推都会白烧一次视觉 token。

**建议**：把 `_candidate_sites` 的空检查提前到多模态调用之前（与本次审查标准 §3.1「先检查前置条件，再做昂贵调用」一致）。

### S-9 `baidu_map_ak` 缺 `repr=False`，密钥可能进日志

**位置**：`src/lighttrail/config.py:147`

**证据**：`amap_web_service_key`（142）、`amap_js_api_key`（143）、`amap_security_js_code`（144）都设了 `repr=False`，唯独 `baidu_map_ak: str = Field(default="", validation_alias="BAIDU_MAP_AK")` 没有。且该项目使用 `%` 占位符日志时若误传整个 settings 对象，repr 会输出字段值。

**影响**：密钥泄露入日志的风险；与另外三个密钥字段的防护不一致。

**建议**：补 `repr=False`（一行）。

### S-10 ADR 编号链断裂：ADR-002 引用的 ADR-001 不存在

**位置**：`docs/adr/ADR-002-platform-neutrality.md:5`（`Supersedes: ADR-001 中「串行调用约束」一节`）

**证据**：`docs/adr/` 下仅有 ADR-002/003/004；`git log --all -- "docs/adr/ADR-001*"` 无记录，全仓 `find` 无该文件。ADR-001 从未进入版本库。

**影响**：决策链不完整——读者无法追溯「被取代的那条约束」的原始论证；而 ADR-001 恰恰是后来被推翻的「ECNU 串行约束」假前提（见工作记忆与该 ADR 正文），其缺失会让后来者重复踩坑。

**建议**：补写 ADR-001（据 ADR-002 的 Supersedes 内容与 devlog 的并发决策记录重建），或在 ADR-002 中把引用改为「已废弃前提，无独立文档」并说明去向。

### S-11 `pyproject.toml` 与 FastAPI app 版本号不一致

**位置**：`pyproject.toml:7`（`version = "0.1.0"`）vs `src/lighttrail/api/app.py:73`（`version="0.2.0"`）

**影响**：包版本与对外 OpenAPI 版本漂移，`pip show` 与 `/docs` 显示不同版本，发布时易混淆。

**建议**：确定单一真源（建议 app 从包元数据读取），或在两处同步并写明约定。

### S-12 README 未把 S1 标记为已完成

**位置**：`README.md:96-97`

**证据**：勾选列表停在「R0 真实性收口 ✓」，下一行是「S1–E1 产品闭环」未勾选；但 `docs/AGENT-ROADMAP.md` 明确记载 S1 闸门 2026-10-02 已通过，S1-1～S1-5 全部验收。

**影响**：README 是对外能力声明，读者会误判当前进度。

**建议**：拆分为「S1 结构化 SpotPlan ✓」与「D1–E1 完成中」，与路线图同步。

### S-13 `_add_shared_contracts` 在 `SpotPlan` 已存在时提前返回，不校验一致性

**位置**：`src/lighttrail/api/app.py:120-138`

**证据**：`if "SpotPlan" in schemas: return`——只做「存在即跳过」，不做内容比对，与紧邻的冲突检测逻辑（136-137 行 `raise ValueError`）语义矛盾。

**影响**：若某端点先行发布了不同形态的 `SpotPlan` schema，共享契约会被静默跳过，前端类型可能生成自非预期 schema。

**建议**：改为比对关键字段一致或显式记录跳过原因。

### S-14 `composition.py` 中 `import re` 位于函数体内

**位置**：`src/lighttrail/orchestrator/pipelines.py:762`（`_values_equivalent` 内 `import re`）

**说明**：模块顶部（16 行）已 `import re`，此处重复局部导入。项目 `tests/test_architecture.py` 的 AST 门禁专门检查函数内 import，应属漏网。

**建议**：删除局部 import；同时确认 AST 门禁是否真的覆盖到该文件（❓ 待验证，本次未细读该测试）。

---

## 💭 Nit

- **N-1** `api/routes.py:64` 从 orchestrator 导入私有函数 `_render_card`——跨层用私有符号，建议正式导出或移到公共渲染模块。
- **N-2** `orchestrator.py:206` docstring 仍写「迁移期亦接受旧 Agent 门面（TODO(B2-7)）」，但实现已只接受 `AgentRuntime`，注释过期。
- **N-3** `orchestrator.py:548-552` 用 `ValueError` 表达「LLM 路由置信度低」——业务性降级，异常语义不准（且会与真实的参数错误混淆）。
- **N-4** `memory/manager.py:115,247,260,322` 等接口接受 `RequestContext | dict | str` 三态重载并靠 `isinstance` 分支，历史上「误传 location 覆盖 timestamp」的隐患仍潜伏（253-254 行）。建议新代码只走 `RequestContext`。
- **N-5** `api/routes.py:446` 等错误信息混用中英文风格不统一（`"照片超过 10MB 上限"` vs `"该地图服务路径未开放"`），建议统一为书面中文。
- **N-6** `config.py` 中 `DEFAULT_MODEL = "ecnu-plus"` 等品牌默认值放在 config 顶层常量，注释已说明「可覆盖的部署默认值」，但建议集中在 adapters 层以强化 ADR-002 的分层意图。
- **N-7** `api/app.py:7-8` docstring「默认单进程 uvicorn 即最优（默认串行配置下 LLM 是全局瓶颈）」——并发已是纯配置（ADR-004），该论证已作废，建议更新。
- **N-8** `docs/architecture.md` 版本号 v3.2 与 `docs/AGENT-ROADMAP.md` v1.4 的「基线 2026-09-22 / 状态核对 2026-10-02」字段并存，建议统一「状态核对」日期口径。
- **N-9** `frontend/package.json`、`src/lighttrail/api/app.py`、`pyproject.toml` 三处版本字段无同步机制（与 S-11 同源）。

---

## 值得表扬的实现

1. **`api/session.py` 的并发修复质量高**：锁内只改内存、锁外落盘、深拷贝不外借、`os.replace` 原子替换 + Windows 占用退避重试。文件顶部 docstring（10-18 行）完整记录了三条并发约束及其原因，是「注释与实现一致」的正面样本。
2. **`domain/knowledge/fts.py` 的安全与实用性兼顾**：全参数化 SQL（无注入面），中文 trigram 检索 + 长问句有界扩召回 + `LIKE` 兜底三段式设计，且用 `threading.Lock` 串行化共享连接，注释解释了「为何需要兜底」。
3. **`application/spot_planner.py` 的诚实降级**：所有缺失事实（无坐标、坐标系不可用、题材未支持、天文结果日期/时区不匹配、朝向冲突、天气未核验）都返回带稳定 `risk.code` 的 `needs_data`，明确拒绝「让模型猜事实」；`_direction_conflict` 用 90° 阈值判视线背离，是真实的摄影领域判断。
4. **`tools/exposure.py` 的数值正确性**：等效曝光因子（`1/√(2^n)`、`2^n`）、500/NPF 法则、ND 档位表、正反向换算与「无需减光」「未知滤镜」等边界处理均复核无误，`_format_shutter` 的分数秒格式化符合摄影习惯。
5. **`config.py` 的密钥防护意识**：三个地图密钥 `repr=False`（虽有 B/S-9 的漏网），`has_*` 属性把占位值（`sk-your-`、`your-`）识别为未配置，避免占位符误触发真实调用。
6. **`orchestrator/pipelines.py` 的语义护栏 `_align_card`**：对模型输出的地点、题材、日期、时间窗口做结构化比对，冲突时清空证据并降级为保守卡片——这是把「不信模型自述」落成代码的范例，直接对应 R0-7 的评估原则。
7. **devlog 的证据密度**：每批条目附 pytest 数、OpenAPI SHA-256、闸门状态与「未验证边界」声明（如「合成测试不证明真实天气准确、零费用或公开部署许可」），诚实度远超一般项目日志。

---

## 本次未覆盖、建议补充审查的模块

原定 4 个并行审查代理因 Token 频率限制（429，2026-10-04 02:43 后重置）全部失败，以下未做行号级细读：

| 模块 | 建议审查重点 |
|---|---|
| `frontend/src/pages/*.tsx`、`components/` | D2/D3/D4 状态管理竞态、契约消费是否仍走生成类型、EV100 等效曝光不变量（R0-6 回归）、`vite.config.ts` 代理的 JS 密钥处理 |
| `tests/`（58 文件） | 断言强度、覆盖缺口（api/routes.py 1034 行、pipelines.py 799 行的测试对应）、AST 架构门禁的实际覆盖范围、夹具隔离 |
| `evals/` | L2 cassette 断言能否抓住「问 A 答 B」、judge 提示词质量、黄金用例代表性 |
| `tools/astronomy.py`（694 行） | 日出日落/蓝调金色时刻/月相/银河可见性的天文算法正确性——**本次唯一未读的大型计算模块，风险最高，优先补审** |
| `infra/trace.py`（499 行） | 历史「注释承诺 tokens 统计」问题的现状、置信度规则可解释性 |
| `adapters/llm/client.py`（449 行） | 历史「缓存承诺」现状、重试与限流器边界（ADR-004） |

**建议**：额度恢复后按上表分派独立审查，产出补审报告并合并进本报告；`tools/astronomy.py` 因涉及所有天文决策的正确性，建议优先。

---

## 修复优先级建议

| 顺序 | 项 | 理由 |
|---|---|---|
| 1 | **B-1**（提交） | 零成本、防不可逆丢失，且是其他审查工作的基线前提 |
| 2 | **B-2 / B-3**（并发与上传） | 真实缺陷，改动局部，风险收益比最高 |
| 3 | **B-5**（配额去重） | 架构性正确性，越晚改成本越高 |
| 4 | **B-4**（格式门禁） | 一次执行 + 门禁补丁，立即消除漂移 |
| 5 | **S-8 / S-9 / S-10**（小改动高收益） | 各为 1～数行，直接消除 token 浪费与密钥风险 |
| 6 | **B-6 / S-1 / S-3**（契约收口） | 归入 P1 与 O1 批次执行 |
| 7 | **S-2 / S-4 / S-5 / S-6 / S-7 / S-11～S-14** | 排期处理，可合批 |

---

## 与路线图的衔接建议

1. **在 S1 与 D1-1 之间插入一次「收口批次」**：修 B-1～B-5（约 1 个批次量），再进 D1-1。理由：D1 会大改决策契约，在契约改动前把工程门禁（提交纪律、格式门禁、配额单一真源）补齐，可避免带着已知缺陷扩大改动面。
2. **把 B-6、S-1、S-3 写入 `docs/AGENT-ROADMAP.md` P1-1 的输入清单**（当前 P1-1 只写了「坐标、照片、EXIF、Key 默认本地；日志脱敏；CORS 白名单」，应补「事件时间戳格式校验、端点响应契约完备」）。
3. **`docs/CODE-REVIEW.md` 已建立**，建议在路线图 §11 统一完成定义中引用它，替代散落的审查要求。

---

*本报告基于 2026-10-03 的工作区快照。工作区处于 117 文件未提交状态，报告中的行号以该快照为准；B-1 修复后应以 commit 为基线复核。*
