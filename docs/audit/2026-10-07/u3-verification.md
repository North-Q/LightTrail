# U3 保存并继续一次计划验收

日期：2026-10-07。基线 main / 4b04b81 + 190 个已有未提交文件；先记录 SHA-256，未 reset、stash、提交或发布。本批限定 A03，以“今晚火烧云是否值得去”为场景，使用合成收藏、固定评估时间和离线模型/数据替身；没有真实服务或个人数据外发，没有新增框架、数据库、认证系统或 CaptureEvent。U1/U2 原验收记录保留。

## 复现与修复证据

| 实际缺口 | 对照 / 回归 | 修复后的行为 |
|---|---|---|
| decide 创建新编排器，不加载已存历史 | 生产装配对照：同一隔离目录已保存唯一历史标识，旧入口方式 `create_scope → plan` 的 Fake 消息没有标识；加载后消息确实包含它。完整 API 续问另断言原请求及整段实际助手答复，不仅检查 session ID | 意图、综合及 Planner 的实际模型请求携带所选版本历史；明确旧数据仅作背景，当前建议仍受实际工具资格护栏约束 |
| 只有最近卡及消息，没有可寻址版本；D3 丢会话 ID；D2 只有输入/内存计划 | 原会话序列化及有效旧记录没有 results；初轮 legacy 回归 KeyError。初轮生成 fixture 的收藏字段/201 断言错误已修正，不当作产品故障证据 | SessionRecord 增加兼容的结果数组；保存实际文本、卡、完整消息、管线数据/分数与 SpotPlan，稳定 UUID、版本号、父版本和原时间可以恢复 |
| 新结果覆盖旧结果，普通旧入口过时保存可擦除新版本 | `test_legacy_save_cannot_erase_versions` 修复前确定性失败；八个同会话并发保存及真实 API 共享会话分别验证 | 单进程结果锁合并不可变版本，再经原临时文件与原子替换落盘；旧入口更新工作区不删除旧版本 |
| 浏览器恢复、重查和输入选择混淆 | 真正退出浏览器，重建应用服务及会话缓存，再用同一浏览器和隔离目录恢复；完整 SPA 历史链接另测 | D1/D3 恢复实际卡/完整消息，D3 进入服务端历史；D2 冻结原计划，可带计划历史继续询问。新选择 URL 不被旧选择覆盖，重新查询明确生成新版本 |
| 失败终态未确认保存、旧记录与身份边界 | 注入磁盘错误、读取缺字段 legacy 会话、alice/bob 不同 RequestContext、不存在的版本 | SSE 明确 error → done(saved=false)，无成功卡或 result_id；计划保存失败返回明确 HTTP 500；404 不暴露他人是否有记录，列表只含当前用户，旧时间缺失不补造 |

生成后的收藏名称与坐标修改、随后删除均不改变原快照。跨到次日重建时原目标仍为 2026-10-06，显式重查“今晚”才生成 2026-10-07 的新结果；旧时间、资格、依据和缺失说明保持原值。正常卡与全部数据失败后的 wait/low/needs_data 均有重启恢复回归。

实际离线服务器联调还发现 U2-3 的日志过滤清空 args 与 Uvicorn AccessFormatter 的五参数约定不兼容。仅为该访问日志保留五个已净化参数；真实 formatter 可输出定位信息，假私有 Key 不在记录或格式化输出中。既有其他日志、trace、错误响应过滤及公开 JS SDK 标识例外保留。

## 存储与接口选择

沿既有 SessionRecord / SessionManager / sessions JSON / SpotPlan / snapshot_context 实现，不建设更大的事件模型。SavedResult 保存 `session_id/result_id/version/parent_result_id/kind/created_at/request/text/card/plan/history/pipeline/trace`。恢复的权威数据是服务端实际快照，浏览器只新增稳定指针。GET 只读原数据，不访问当前收藏或更新有效期；返回 private/no-store。旧记录只把原内容映射为固定 legacy ID，不能找回此前已经被覆盖的历史版本。

文字流保存成功后才发送成功结果及 done 的版本确认。续问选择特定父版本，显式 requery 清除该次旧对话背景后重新查询；旧版本仍可读取。D2 的新保存入口包装原计划生成路径，原纯计算入口与脱敏归档/回放契约保留。历史页面及发送说明明确原时间、数据不足、坐标快照与保留边界。

## 工程门禁

| 检查 | 结果 |
|---|---|
| 全量 pytest | **591 passed**；本批新增 **12** 项 |
| 本批、会话及 U1/U2 相关回归 | **179 passed**；包含真实共享会话、读历史零调用、数值/时效、许可/预算、凭据/文件边界与 SSE |
| Ruff | check / format --check，**160 个 Python 文件**通过 |
| smoke / 分层 | **21/21**；**3 kept / 0 broken**，87 files / 240 dependencies |
| L2 合成 | 管线 **12/12**、工具 **10/10**，**0 真实 LLM** |
| OpenAPI → TS | 版本及保存确认字段生成；重复生成 SHA-256 一致，见 [保护及门禁记录](u3-document-checks.json) |
| 前端 | TypeScript/Vite build；辅助 **3 + 2 + 8**；壳层 **7/7** |
| 既有浏览器护栏 | U1、U2-1、U2-3 双视口、R0-6、S1-3、S1-5 及 S1 gate 通过；规划替身适配新保存封装，原请求/导出/回放断言保留 |
| U3 浏览器 | 真实 D3/D2 生成 → 退出 Chrome → 同目录重建应用 → 第二个 Chrome 恢复；Fake 上下文、重查、失败提示通过；完整 SPA 稳定 ID 恢复与移动视口无横向溢出 |
| 文档 / 工作区 | 相关本地链接、当前状态、基线哈希及 U1/U2 核心不变检查，见 [检查记录](u3-document-checks.json) |

[浏览器过程与计数](u3-browser-evidence.json)记录页面恢复阶段为 0 次模型/数据调用；随后完整 SPA 只读恢复前后均为 5 次模型、12 次数据工具，增量为零。这些是 Fake 调用计数，不是供应商请求。实际落盘版本与模型收到的上下文见 [合成结果和上下文](u3-api-evidence.json)。页面使用生产路由和真实组件，没有替换 fetch 响应或手写 U3 决策卡。

恢复后的完整 SPA 截图：[桌面](u3-restored-desktop.png)、[移动](u3-restored-mobile.png)。原目标为 2026-10-06；实际保存时间是本轮落盘时间，二者没有混为实时评估。

## 人工离线验收

先用两个终端启动专用替身服务和 Vite；不要使用真实后端，不需要 Key：

```powershell
cd D:\Project\LightTrail
.venv\Scripts\python.exe -m evals.u3_preview --data-root .pytest-run-u3/manual-acceptance --port 8783
```

```powershell
cd D:\Project\LightTrail\frontend
$env:LIGHTTRAIL_API_TARGET='http://127.0.0.1:8783'
npm run dev -- --host 127.0.0.1 --port 4173 --strictPort
```

打开 [专用验收页](http://127.0.0.1:4173/tests/u3.html)。它使用独立的 `u3-demo.` 浏览器存储前缀和项目 `.pytest-run-u3` 数据目录，不读取个人档案；模型地址固定不可用本机端口，地图凭据显式为空，数据与模型均为离线替身。只创建一次“合成收藏”；重复添加同名收藏会导致目标机位不明确，先指定机位或改用新的隔离目录，不要清理真实数据。

1. 点击“合成收藏”“数据失败”，在真实决策组件输入“今晚火烧云是否值得去”。必须显示数据不足和缺失原因，不能因为 Fake 输出 go/high 而建议出发；保存状态须明确确认。
2. 关闭页面或退出浏览器，停止该专用 Python 服务；用**同一隔离目录**再次启动。返回验收页，恢复原卡、原消息和原目标日期，标记历史/待更新。点击“规划页”也可生成、重启恢复实际计划；恢复不能请求模型、天气或地图。
3. 输入“那我需要带什么？”并提交。到 [Fake 实际上下文及计数](http://127.0.0.1:8783/api/_offline/metrics)检查新消息包含原请求与原答复。D2 可通过保存版本的“继续询问此计划”在完整 SPA 继续；服务器同样带入所选计划历史。
4. 点击“数据完整”，再点“重新查询原请求（新版本）”。应产生新版本；历史下拉仍可选择旧卡，原数据不足说明和时间不能消失。
5. 点击“模拟保存失败”，再提交。只能显示保存失败，不能显示“已保存实际结果”；回到“数据完整”解除合成故障后重试。不要用这个开关操作真实服务。

自动检查会建立新的合成子目录和专用 Chrome profile，关闭第一进程后重建服务实例；不会删除前轮证据：

```powershell
cd D:\Project\LightTrail\frontend
npm run check:u3-ui
```

后端独立复核：

```powershell
cd D:\Project\LightTrail
.venv\Scripts\python.exe -m pytest tests/test_saved_results.py -v --basetemp=.pytest-run-u3/manual-tests -o cache_dir=.pytest-run-u3/cache
```

完整 SPA 可从首页会话历史的“恢复 vN 并继续”进入稳定链接，也可使用 `/#/d3?session=<会话ID>&result=<结果ID>`；链接不带坐标。日常页面只读服务端已有版本，不会自动采集天气。

## 剩余边界

本批证明本地离线保存、恢复及历史接线，不证明真实火烧云质量或用户实拍效果。模型可能错误理解续问，仍须 U4 的真实场景许可与验收。当前单进程 JSON 存储不解决多 worker/跨设备同步；完整历史随版本重复保存，暂无分页、压缩、大小限制或自动删除。旧记录此前覆盖掉的内容无法恢复；照片和赶场入口尚未全部进入结果版本索引，导入冻结 JSON 不自动成为新保存版本。不能把这里的最小父版本关联宣称为完整 CaptureEvent。

快照可能含坐标，删除收藏不会删除历史；清理本地会话、浏览器及导出/备份需分别核对。清理本地数据不能删除供应商副本。默认 _local 与服务端 RequestContext 隔离不是公网身份认证；页面停止只撤销等待和回写资格，底层 worker 仍可能完成并保存。进一步数据流见 [DATA-BOUNDARIES](../../DATA-BOUNDARIES.md)。

U3 完成后停止；**未进入 U4/U5/U6**，未调用真实服务、提交或发布。
