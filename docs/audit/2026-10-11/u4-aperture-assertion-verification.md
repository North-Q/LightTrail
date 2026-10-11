# U4 最大光圈条件声明误确认窄修复验收

日期：2026-10-11（+08:00）。本批仅修复本轮“如果／若14mm最大光圈为f2.8”被错误认证为已确认规格的问题；先经实际临场管线离线复现，再修复。条件、否定、不确定和冲突声明保持未知，明确且一致的肯定正例保留。未扩展通用语义解析。

本人明确补充：**本人未出门，无独立现场观察，现场效用无法判读；U4未全部完成。** 原[真实生成与恢复](../2026-10-09/u4-real-verification.md)、原卡、有效期、清理异常及退出码1保留；原[约束验收](../2026-10-09/u4-shooting-constraints-verification.md)、[SDK验收](../2026-10-09/u4-sdk-lifecycle-verification.md)和[晨暮验收](../2026-10-09/u4-event-fix-verification.md)保留。本批无真实调用，不补跑或换日期，不进入U5/U6，不提交或发布，完成回归、文档和HANDOFF后停止。

## 实际管线复现

执行实际 `LiveDecisionPipeline.run → 本地天文/合成天气采集 → 本轮拍摄前提 → 有界综合摘要 → 离线模型 → 完整证据资格 → 最终拍摄约束`。目标仍为合成2026-10-10晨间窗口，使用固定合成机位和隔离记忆，没有读取或重放个人请求、照片或精确坐标。

离线模型故意返回14mm/f2.8的go卡。修复前独立合成探针确认两种直接条件句均被放行，而不只是辅助函数的提取值异常：

| 原文规格声明 | 修复前完整前提及最终卡 | 修复后完整前提及最终卡 |
|---|---|---|
| 如果14mm最大光圈为f2.8，就使用f2.8 | 最大光圈2.8；go/ready；保留14mm、f/2.8参数 | 最大光圈null；wait/needs_data；参数为空 |
| 若14mm最大光圈为f2.8，就使用f2.8 | 最大光圈2.8；go/ready；保留14mm、f/2.8参数 | 最大光圈null；wait/needs_data；参数为空 |

每个探针均仅一次离线综合，无补查、重试或真实发送。新增34项边界回归修复前 **23 failed / 11 passed**：不确定声明和明确肯定部分已正常，条件、否定以及冲突覆盖存在错误。原红阶段XML及日志保留在忽略目录；[脱敏门禁与探针摘要](u4-aperture-assertion-offline/gates.json)。

## 窄修复与可观察边界

仅修改 `src/lighttrail/domain/shooting_constraints.py` 中本轮最大光圈提取，增加局部 `_request_aperture_limits`。沿已有中文/英文逗号、句号、分号及换行边界处理涉及光圈的分句，按焦段汇总已有“焦段＋最大光圈＋f值”规格表达；不从模型输出认证事实。

- “如果／若／假设／可能”、未确认等标记或“不是／并非／不为／不能确认”等否定标记使相关焦段保持null，优先于旧档案和同轮肯定值。
- 同焦段明确声明多个不同最大光圈时保持null，不按最后一条覆盖；相反顺序结果相同。
- 明确且一致的“已确认14mm最大光圈为f2.8”“14mm最大光圈是f/2.8”“14mm最大光圈：F2.8”及重复一致声明保留2.8，合法参数可go/ready。24–105mm F4的原归属不受影响。
- 未改动档案规格解析、管线装配、器材/行程其他规则、公开契约、历史读取或前端。规则只核验已有有限规格表达；跨分句条件作用域、省略焦段的指代、更复杂的修辞和纠正语义不在本批扩展范围，不宣称通用语义解析。

新增 `tests/test_shooting_constraints.py` 34项实际临场管线用例：15种条件/否定/不确定/冲突声明分别搭配未知与已知旧档案，共30项；4项明确肯定与重复一致正例。断言完整ctx前提、实际综合prompt及最终卡，确认依赖参数撤下且有最大光圈待确认原因。生产变化仅一个文件。

## 实际验证

本批该文件 **65 passed**，相关回归 **238 passed**；Ruff check及format --check两个变更文件通过，smoke **21/21**，分层 **3 kept / 0 broken**。相关回归包括天气/晨暮资格、原编排端到端、合成新旧结果保存恢复、SDK生命周期、输出及调用预算、并发取消、本机权限与SSE；原有效期和恢复零模型工具/写入断言保留。

所有执行使用项目`.venv`与本批隔离驱动，在import产品/API/pytest前设置假Key、空地图凭据、独立数据目录和禁用代理，并阻断非回环socket.connect/connect_ex与业务DNS。最终门禁及探针 **非回环尝试0、真实供应商发送0**。未安装依赖、录制cassette或读取原真实结果正文。

实际命令（项目根；再次复跑需新建目录，避免覆盖本批证据）：

```powershell
./.venv/Scripts/python.exe -X utf8 .pytest-run-u4-aperture-assertion/20261011/run_offline.py pytest -q tests/test_shooting_constraints.py -k 'aperture_nonassertions or explicit_aperture_confirmation' --basetemp=.pytest-run-u4-aperture-assertion/20261011/red/tmp -o cache_dir=.pytest-run-u4-aperture-assertion/20261011/red/cache --junitxml=.pytest-run-u4-aperture-assertion/20261011/red.xml
./.venv/Scripts/python.exe -X utf8 .pytest-run-u4-aperture-assertion/20261011/run_offline.py probe red
# 上两项先于生产修复执行；不可在修复后将新的通过结果写入原红阶段。
./.venv/Scripts/python.exe -X utf8 .pytest-run-u4-aperture-assertion/20261011/run_offline.py pytest -q tests/test_shooting_constraints.py --basetemp=.pytest-run-u4-aperture-assertion/20261011/green/tmp -o cache_dir=.pytest-run-u4-aperture-assertion/20261011/green/cache --junitxml=.pytest-run-u4-aperture-assertion/20261011/green.xml
./.venv/Scripts/python.exe -X utf8 .pytest-run-u4-aperture-assertion/20261011/run_offline.py probe green
./.venv/Scripts/python.exe -X utf8 .pytest-run-u4-aperture-assertion/20261011/run_offline.py pytest -q tests/test_shooting_constraints.py tests/test_solar_event_window.py tests/test_decision_evidence.py tests/test_weather_validity.py tests/test_orchestrator.py tests/test_pipeline_e2e.py tests/test_saved_results.py tests/test_sdk_lifecycle.py tests/test_u4_controls.py tests/test_request_budget.py tests/test_client_concurrency.py tests/test_local_boundaries.py tests/test_sse_delivery.py --basetemp=.pytest-run-u4-aperture-assertion/20261011/related/tmp -o cache_dir=.pytest-run-u4-aperture-assertion/20261011/related/cache --junitxml=.pytest-run-u4-aperture-assertion/20261011/related.xml
./.venv/Scripts/ruff.exe check src/lighttrail/domain/shooting_constraints.py tests/test_shooting_constraints.py
./.venv/Scripts/ruff.exe format --check src/lighttrail/domain/shooting_constraints.py tests/test_shooting_constraints.py
./.venv/Scripts/python.exe -X utf8 .pytest-run-u4-aperture-assertion/20261011/run_offline.py smoke
./.venv/Scripts/python.exe -X utf8 .pytest-run-u4-aperture-assertion/20261011/run_offline.py lint
```

未重跑全量pytest、L2、前端构建、SSR、浏览器或原真实D3；前批671项全量、L2及前端验收仅属于前批，不将相关回归写为新全量验收。未验证供应商、独立现场观察、器材实际规格或携带、其他Python/SDK版本与独立安装。原本应用估算4.2297 credits、缓存未知、确定总费用null及供应商思考/扣费/保留边界不变。

首次Ruff指出`re.I`别名，改用`re.IGNORECASE`后通过；文档辅助脚本曾在TODO写入时报PermissionError，经apply_patch完成并继续其余文档更新。辅助链接检查首次在收口JSON尚未写入时失败，调整写入顺序后119个链接无失效。这些辅助失败不构成产品反例，未留下未完成动作。

## 文档、保护与停止

现场状态来源为本人本批陈述，未独立观察，不推断未出门原因或天气结果；不将未出门视为预测命中或失准。原真实记录只补当前现场状态，并明确原待填写步骤为历史技术记录，不生成后续观察任务。当前AGENTS、路线图、README、TODO、FEATURES、架构及HANDOFF同步该状态，详细历史技术证据保持原文。

分支main / HEAD 4b04b81、暂存0；本批开始未单独保存状态条目计数，前批177条不作为本次实测，收口180条；对3131个非忽略与旧隔离证据/data文件保存字节哈希，读取失败0。收口2476个旧隔离/data文件哈希不变，现有文件差异仅限11个授权文件。旧隐私材料仅哈希核对、不解析或公开复制。保留原生成标记、原卡、请求、响应、usage、SSE、截图及既有用户未提交改动；[收口检查](u4-aperture-assertion-offline/closeout-checks.json)记录允许的文件差异、旧隔离材料保护、链接和当前状态一致性。

本批窄修复、相关回归、路线图、验收和[HANDOFF](../../HANDOFF.md)完成后停止。无真实调用，不补跑或换日期，不进入U5/U6，不提交或发布。新会话先读AGENTS→路线图→HANDOFF→架构与本批证据，再核对工作区，只按本人新范围继续。
