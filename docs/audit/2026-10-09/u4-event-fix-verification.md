# U4 晨／暮事件与证据窗口修复验收

日期：2026-10-09。限定目标：2026-10-10、+08:00，日出前蓝调持续拍摄至日出后。本记录证明代码修复和离线门禁；[本轮真实技术验收](u4-real-verification.md)单独记录。**U4 未全部完成，现场效用待本人判读；停止于本批，不进入 U5/U6。**

## 复现及最小修复

原[晨间预检](u4-morning-preflight.md)经过生产装配、API、真实 SDK 内存 HTTP，证明晨间请求的综合摘要仅包含目标日 15–20 时天气，Fake 晨间 go/high 仍被保存为 ready/go。并非只改关键词即可解决：题材、天文事件、天气取样和模型窗口必须一致。新增回归初轮 8 failed / 6 passed；最终新增 15 项全部通过。

沿既有契约与模块增加可缺省的 Intent.solar_event / solar_window；原请求决定 sunrise / sunset / ambiguous 及 blue / blue_through_event，模型不能覆盖原文。纯规则 domain/solar_window.py 根据实际太阳事件的 ISO 时刻、目标日期和固定时区建立拍摄窗口，贯穿采集、机位匹配、综合摘要与完整证据资格。未指定晨／暮的蓝调请求不猜日落；事件不存在和计算失败保留原状态。

晨间蓝调加日出选晨间蓝调开始至晨间黄金时刻结束，不采集或引用日落火烧云评分。蓝调／日出是太阳高度角与事件窗口，不是火烧云概率；此批没有新增朝霞预测模型。晨间不会因日落评分“高”而取得资格。晚间火烧云和蓝调的既有路径保留。

综合摘要先按目标天文窗口筛选，再序列化完整 JSON；核心小时样本优先，附前后小时，最多 7 条，工具数据摘要仍最多 12000 字符，省略路径、条数和必要摘要可追踪。日摘要中的傍晚序列不混入晨间摘要。绝对时间、日期、时区、时效、缺失说明保留；完整原始结果继续保存到管线和 U3 历史，资格校验读取完整实际证据，不能依赖摘要或模型自报来源。

最终资格额外核验从窗口开始整点到结束向上取整的每个小时，云量、能见度、降水概率、风速须为有效数值。只有下午数据、晨间缺样本或 null、目标事件错误、窗口不明，均不能 ready/go。模型窗口须使用与目标窗口一致的绝对时间；组合场景还须覆盖蓝调并跨越日出。错误晚间窗口或只从日出开始的 Fake 建议均降级；综合前预检不把尚不存在的模型卡窗口当作错误。

## 数值与生产路径证据

以下仅为合成机位（31.0, 121.0），不是本人机位。经 build_application → /api/decide → 天气真实解析 / Astral → SDK MockTransport → U1 → SessionManager：

| 项目 | 修复前 | 修复后合成例 |
|---|---|---|
| 意图事件 | 蓝调默认日落 | sunrise、blue_through_event |
| 天文依据 | 傍晚事件 | 日出 05:55，晨间蓝调 05:35–05:40，目标窗口 05:35–06:30 |
| 发送小时天气 | 15:00–20:00 | 04:00–08:00，共 5 条，均含 +08:00 |
| 只有晚间数据 | 可接受 ready/go | wait / low / needs_data |
| Fake 晚间卡／缺晨间样本／跳过蓝调 | 未逐窗检查 | 明确缺失或错配，禁止 go |
| 原结果恢复 | 原错配卡仍可恢复 | 修复后原卡与完整快照一致，恢复增量调用 0，不续期 |

可审阅证据：[实际 SDK 请求体](u4-event-fix-offline/normal/supplier-requests.json)、[完整综合 JSON](u4-event-fix-offline/normal/synthesis-summary.json)、[原结果](u4-event-fix-offline/normal/original-result.json)、[同目录重建恢复](u4-event-fix-offline/normal/restored-result.json)、[完整 SSE](u4-event-fix-offline/normal/response.sse.txt)。

反例保留完整请求、卡、恢复与 SSE：[仅晚间](u4-event-fix-offline/afternoon_only/summary.json)、[缺晨间样本](u4-event-fix-offline/missing_sample/summary.json)、[空值](u4-event-fix-offline/null_sample/summary.json)、[模型晚间错配](u4-event-fix-offline/wrong_model/summary.json)、[跳过蓝调](u4-event-fix-offline/skips_blue/summary.json)、[太阳事件不存在](u4-event-fix-offline/event_missing/summary.json)、[太阳计算失败](u4-event-fix-offline/event_failed/summary.json)、[晚间回归](u4-event-fix-offline/evening/summary.json)。另覆盖关键词顺序、UTC 跨日/+14:00、错误绝对事件日期；历史旧 Intent 缺新增字段仍可解析。

## 工程门禁与保护

| 门禁 | 实际结果 |
|---|---|
| 全量 pytest | 631 passed，47.01 秒；新增事件窗口回归 15 项 |
| Ruff | 本批 9 个 Python 文件检查、格式检查通过 |
| smoke／分层 | 21/21；3 kept / 0 broken |
| L2 | 管线 12/12、工具 10/10，真实模型 0 |
| 前端 | build、停止等待 8 项通过；契约重生成哈希不变 |
| 浏览器 | U1、U2-1、U2-3 双视口、U3 真实组件与完整 SPA 恢复通过 |
| 精确授权输入离线预演 | 内存模型 1 / 天气 1；恢复 0；4096 输出上限实际进入 SDK body |

[门禁数据](u4-event-fix-offline/gates.json)、[L2 结果](u4-event-fix-offline/l2-final.json)、[预演摘要](u4-event-fix-offline/rehearsal-summary.json)。L2 的日出案例移除不适用的日落评分预期；未指定晨／暮蓝调的预期改为 needs_data，没有重录供应商 cassette。测试完整原始日志保留于 Git 忽略的 `.pytest-run-u4-event-fix/20261009/`。

开始前记录 577 个已有非忽略文件 SHA-256，保留先前 U1–U4 未提交内容；未 reset、stash、commit、push 或发布。仅修改本批模块、相应 L2 预期与状态/验收文档；原有快照和数据不删除。最终保护与文档检查见[收口记录](u4-event-fix-offline/closeout-checks.json)。

## 人工验收和剩余边界

1. 使用隔离合成收藏，D3 输入明确的 ISO 日期、+08:00、「日出前蓝调一直拍到日出后」。检查目标事件、日期、拍摄窗口、获取时间和缺失说明；合成 SDK body 中不应出现选取晚间天气的规则。
2. 使用仅晚间数据或错误晚间 Fake 卡，检查页面为数据不足，不能因为 go/high 被接受；检查缺失理由包含实际晨间证据／窗口问题。
3. 按保存的 session/result ID 进入历史，关闭浏览器、重建应用后仍看到原卡与消息；卡片显示历史，获取时间、有效期不变，无模型／天气／地图增量。
4. 本轮真实结果只回看，不能点击续问、重查、地图或照片；人工步骤及真实结果边界见[真实验收](u4-real-verification.md)。

蓝调和黄金时刻沿用 5 分钟太阳高度角采样，不是精确边界；固定偏移不自动处理夏令时，高纬窗口仍沿用既有算法，此批不替换 Astral。目标窗口超过 3 小时或缺事件时保守降级，未提供任意长时段计划。逐窗护栏不校验所有自由文本通勤、器材和备选方案；真实轮次暴露的 SDK 清理生命周期异常及模型备选时间冲突均单独记录，**本批未修复、未以重跑代替修复**。
