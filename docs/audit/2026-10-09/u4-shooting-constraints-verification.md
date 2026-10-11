# U4 通勤、布置时间与器材前提最小离线修复验收

日期：2026-10-09（+08:00）。小北明确授权本批仅修复拍摄建议的通勤、布置时间、器材前提和出发前到期提示。**本批离线修复完成，U4 实际拍摄效用仍待本人判读，不能标记整批 U4 完成。** 无真实模型、天气、地图、照片、续问或重查；不进入 U5/U6、不提交或发布，完成文档与交接后停止。

原[真实轮次](u4-real-verification.md)、[晨暮验收](u4-event-fix-verification.md)和[SDK验收](u4-sdk-lifecycle-verification.md)按原事实保留。唯一真实轮次仍为 ECNU 1 / Open-Meteo 1、无重试，固定目标 2026-10-10、+08:00；没有修改旧卡、原清理异常、退出码 1 或原有效期。旧预算余量不构成另一轮授权。

## 修复前反例与最终行为

新增测试先经实际 `LiveDecisionPipeline`、本地真实 Astral、完整合成天气和离线模型复现 **5 failed**：错误备选、未知三脚架、未知 ND、14mm 未知最大光圈、出发前到期均未被约束。原红阶段 XML 和网络计数位于忽略目录，见[脱敏红阶段摘要](u4-shooting-constraints-offline/red-summary.json)。没有使用个人请求或坐标重放。

| 输入或模型反例 | 修复后可观察结果 |
|---|---|
| 最早 04:00 出发，单程 60 分钟、布置 10 分钟；模型称 04:32 开拍 | wait/low/needs_data，撤下违规文案与参数；给出 05:00 到达、05:10 布置完成的绝对时间。主窗口及各备选分别核验，不自动移动窗口或换日。 |
| 05:00 到达，05:09 开拍；04:10 出发却 05:00 到达 | 分别违反布置、通勤间隔，撤下相关建议。结论、时间文案、机位理由和参数均检查；模型自报降级标记不能绕过。 |
| 三脚架未知、未携带、可能带或假设有；1–4 秒/长曝建议 | 不以机身、稳定性或摄影偏好证明三脚架已具备；撤下依赖参数并要求确认。秒级曝光采用保守支撑前提，非手持安全阈值校准。 |
| ND 未确认；只确认带 ND8，却建议 ND1000 | ND 可用性和型号分别核验，其他型号不能代替。 |
| 14mm 仅确认焦距，却推荐 f/2.8 或裸数字光圈 2.8 | 不猜最大光圈；24–105mm F4 不证明独立 14mm 的光圈。24mm/f8 可使用唯一覆盖该焦距的 F4 变焦镜头；f2.8 超出 F4 时降级。 |
| 本轮说 14mm 最大光圈未知，旧档案有 F2.8；变光圈范围 F4.5–5.6 | 本轮未知声明覆盖旧档案；变光圈范围不按整段焦距套用最小 f 值，保持待确认。 |
| 本人明确带三脚架、ND8，确认 14mm 最大光圈 f2.8，时效合格 | 合法参数和 05:10 后的行程保留，数据合格时可 go，未一律拒绝有器材前提的拍摄。 |
| 21:36:28 获取的天气，六小时截止次日 03:36:28，早于计划出发 | 明确“出发前需手动更新”，撤下可立即执行的 go；有效期仍为原 03:36:28，模型/工具调用次数不增加。 |
| 读取已保存旧卡 | API 按原 ID 原样恢复，不重新套用新护栏改写旧卡；页面展示原截止时间，并提示出发前手动更新、回看不续期、不会自动查询。原 04:32 错误仍作为历史事实保留。 |

## 实现边界

`domain/shooting_constraints.py` 从**本轮原文**和本人镜头档案提取前提，保留 null；不采用模型提供的器材可用性或旧轮次声明。支持明确中文/数字时长、复合小时与分钟、简单中文/数字钟点；明确出发时间固定在已核验目标日与时区。冲突、未明确或只有通勤而无布置时长会要求确认，不按零分钟处理。档案结构、公开 API 契约和会话格式未增加字段。

`orchestrator/pipelines.py` 在综合前将中文键“拍摄前提”加入完整管线数据和有界 JSON 摘要，并写明顺序及未知器材限制；异常收口也从本轮事实重建。`orchestrator/constraints.py` 在既有 `qualify_card` 后核验主方案及独立备选，生成确定性行程提示；违规卡清除模型建议的时间、参数、备选和机位理由，保留实际目标、来源、获取/评估/截止时间。既有关键数据、晨暮、远期、语义错配和综合失败降级继续生效。

到期与计划出发按绝对时间比较，等于出发时也视为需更新；不会借规划时间、保存或回看延长天气新鲜度。`frontend/src/components/DecisionCard.tsx` 只读展示 `valid_until`；当截止早于拍摄窗口时提前提示出发前手动更新，兼容旧卡没有结构化通勤字段的情况。页面提示不证明旧卡出发时间已被新规则认证，也不触发请求。

本批采用保守规则，**不是通用自然语言行程与器材语义证明**。多事件同一句且未分隔、未支持的时长、镜头归属不唯一、未知/变光圈规格会要求确认或撤下建议；不会自动推导真实道路耗时、ND 曝光参数、开放/遮挡条件或现场可用性。未知支撑条件的条件式长曝也会暂缓，不将“若有”升级为已具备。自由对话、照片反推/复盘与结构化赶场不在本批扩展范围。

## 门禁与证据

最终全量 **671 passed（新增 31）**；相关 **204 passed**。新增回归在 `tests/test_shooting_constraints.py`；旧正常长曝夹具仅在 `test_orchestrator.py` 与 `test_pipeline_e2e.py` 明确加入携带三脚架声明，原数据/远期/流程断言保留，没有放宽生产护栏。[门禁摘要](u4-shooting-constraints-offline/gates.json)。

- 本批三个生产 Python 文件与新增测试 Ruff check、format --check 通过。
- smoke 21/21、分层 3 kept / 0 broken、L2 合成 12/12 + 10/10；未录制或改写 cassette。
- 前端 `npm --prefix frontend run build`、停止等待 8 项通过；真实 React 组件 SSR 验证历史到期提示、原字段不变及晚到期对照，[组件摘要](u4-shooting-constraints-offline/card-render-summary.json)。未重跑浏览器，也未打开原真实 D3 页面。
- 生产 `build_application → /api/decide → SSE → SessionManager` 保存受约束新卡，同目录重建后 GET 完全相等；另保存含原类错误的合成旧卡，重建后仍原样。恢复各为模型/工具 0、会话文件写入 0、有效期不变，见[恢复摘要](u4-shooting-constraints-offline/restore-summary.json)。这是合成验收，未读取原真实结果正文。

项目 `.venv`，Python 3.13.14、OpenAI SDK 3.1.0、httpx 0.28.1、pytest 9.1.1；未安装依赖。隔离驱动在 import 产品/API/pytest 前设置假 LLM/ECNU Key、空地图凭据、独立数据目录与禁用代理，拦截 socket.connect/connect_ex、getaddrinfo，拒绝非回环连接及业务 DNS。所有最终门禁非回环尝试 0、真实供应商发送 0。Node 检查仅使用本地依赖和合成卡，SSR 无 API 服务或浏览器。

实际命令（项目根执行；复跑须换新目录，不覆盖已有证据）：

```powershell
./.venv/Scripts/python.exe -X utf8 .pytest-run-u4-shooting-constraints/20261009/run_offline.py pytest -q tests/test_shooting_constraints.py tests/test_solar_event_window.py tests/test_decision_evidence.py tests/test_weather_validity.py tests/test_orchestrator.py tests/test_pipeline_e2e.py tests/test_saved_results.py tests/test_sdk_lifecycle.py tests/test_u4_controls.py tests/test_request_budget.py tests/test_client_concurrency.py tests/test_local_boundaries.py tests/test_sse_delivery.py --basetemp=.pytest-run-u4-shooting-constraints/20261009/related-final/tmp -o cache_dir=.pytest-run-u4-shooting-constraints/20261009/related-final/cache --junitxml=.pytest-run-u4-shooting-constraints/20261009/related-final.xml
./.venv/Scripts/python.exe -X utf8 .pytest-run-u4-shooting-constraints/20261009/run_offline.py pytest -q --basetemp=.pytest-run-u4-shooting-constraints/20261009/closeout/tmp -o cache_dir=.pytest-run-u4-shooting-constraints/20261009/closeout/cache --junitxml=.pytest-run-u4-shooting-constraints/20261009/closeout.xml
./.venv/Scripts/ruff.exe check src/lighttrail/domain/shooting_constraints.py src/lighttrail/orchestrator/constraints.py src/lighttrail/orchestrator/pipelines.py tests/test_shooting_constraints.py
./.venv/Scripts/ruff.exe format --check src/lighttrail/domain/shooting_constraints.py src/lighttrail/orchestrator/constraints.py src/lighttrail/orchestrator/pipelines.py tests/test_shooting_constraints.py
./.venv/Scripts/python.exe -X utf8 .pytest-run-u4-shooting-constraints/20261009/run_offline.py smoke
./.venv/Scripts/python.exe -X utf8 .pytest-run-u4-shooting-constraints/20261009/run_offline.py lint
./.venv/Scripts/python.exe -X utf8 .pytest-run-u4-shooting-constraints/20261009/run_offline.py l2
node .pytest-run-u4-shooting-constraints/20261009/card-render.mjs
npm --prefix frontend run build
npm --prefix frontend run test:u2-1
```

初轮相关回归发现失败来源：全数据失败卡不应无条件增加“本人前提”来源，已改为只为明确前提记录；旧长曝正常夹具未声明三脚架，已补全；远期等既有固定降级结论优先保留。修正后全量通过。最初默认执行沙箱初始化失败，使用项目范围的受审查命令执行；收口辅助脚本曾使用无效编码名 utf8-sig，修正为 utf-8-sig 后保护检查通过。这些环境/辅助脚本失败不属于产品反例。

未验证真实供应商执行、实景命中、器材实际携带、其他 Python/SDK 版本或独立安装；原本应用估算 4.2297 credits、缓存未知、确定总费用 null、供应商思考/扣费/保留边界不变。

## 保护与停止

本批开始保存 650 个非忽略文件 SHA-256；另对旧晨暮/真实验收、晨间预检、SDK 隔离目录及 data 作字节哈希，仅检查隐私文件字节，不解析、回显或复制到公开材料。完整日志、XML、驱动和合成卡在 Git 忽略的 `.pytest-run-u4-shooting-constraints/20261009/`；公开记录只有合成/脱敏摘要。保护、文档链接、当前状态一致性、分支与暂存核对见[收口检查](u4-shooting-constraints-offline/closeout-checks.json)。

分支 main / HEAD 4b04b81，开始 177 条 Git 状态记录、暂存 0；保留所有既有未提交改动、历史归档和 `%SystemDrive%/` 来源未明目录，不 reset/stash、清理、提交或发布。旧生成标记、原卡和原真实轮次保留；恢复没有新增取数或续期。本批完成后停止，现场效用由本人判读，后续任务仍以路线图及本人新范围为准。
