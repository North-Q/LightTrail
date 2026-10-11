# U5 最小结果反馈离线验收

日期：2026-10-11（Asia/Shanghai）。范围：本人已采纳最小设计并授权依序实施U5-1至U5-5；只做本地离线开发与验收。**U5本批完成；U4现场效用仍无法判读，未全部完成。**

## 实现与资格

- [契约](../../../src/lighttrail/contracts/feedback.py)：出门、观察、结果及本人原因独立；非observed时结果/范围/结果原因必须null，observed时结果和范围必填。not_went不阻止本人在其他地点观察；不把not_seen当作天象全局未出现。
- [仓库](../../../src/lighttrail/infra/feedback.py)：每用户＋session/result独立JSON，单进程共享锁内重读/CAS/最近mutation去重；同目录唯一临时文件、flush/fsync、有限替换重试、os.replace，失败保留旧文档。GET不建目录或文件，无读取缓存。
- [裁决](../../../src/lighttrail/application/feedback.py)：修改反馈原子取消旧确认、转needs_review；本人重新保存候选绑定当前反馈版本后才可单独确认。经验撤回清空正文/确认时间、保留最小字段及撤回时间；新候选新ID。反馈撤回清空反馈和经验，保留版本墓碑；旧CAS不能复活。
- [API](../../../src/lighttrail/api/feedback.py)：六个JSON端点沿既有本机写边界；先核验已落盘会话归属，再读取反馈。SessionManager.read_saved绕开缓存/get/restore，legacy按原映射处理、指纹固定v1字段；源改变/缺失时只读派生experience_effective=false，编辑/确认409，允许撤回。
- [D3面板](../../../frontend/src/components/FeedbackPanel.tsx)：仅关联已保存版本，生成开始后关闭旧反馈入口；草稿只在组件内存。明确保存失败、响应未确认、冲突；未知状态只读核对，不自动重试。独立保存候选/本人确认/撤回，范围切换明确清空确认，离开/切版本有未保存提醒。RequestScope与按目标重挂载拒绝旧回包；停止等待不确认服务端取消。

经验仅本地回看/手动引用，memory_written=false，不调用events/semantic写端口、不参与Agent注入。photo_refs首版仅空数组，非空422，无U5照片上传入口。未引入CaptureEvent、迁移、新供应商或通用语义解析。

## 本批实际门禁

| 门禁 | 结果与证据 |
|---|---|
| 全量pytest | **729 passed**，新增U5 **24**；最终66.45秒。隔离full-final.xml与full-final-network.json。 |
| 最终相关pytest | **76 passed**，含反馈、U3、权限、架构、前端契约；最后类型注解/异步探针调整后通过。related-final.xml。 |
| 新增回归 | 24 passed；包含两个独立Python进程（active/withdrawn）恢复，模型与外部尝试0，原JSON字节不变。backend-process.xml。 |
| Ruff | 本批9个Python文件check通过；6个新文件format --check通过。既有app/session/composition只检查，不对全仓历史格式强制重排。 |
| smoke / 分层 / L2 | 21/21；3 kept / 0 broken；合成12/12管线＋10/10工具。未录制或改写cassette。 |
| 前端 | npm run build通过；npm run test:u2-1的8项等待/SSE测试通过；离线OpenAPI生成供客户端类型使用。 |
| 真实浏览器 | Chrome/Edge CDP驱动真实D3及完整SPA，连接本机生产API，未替换fetch结果；8组生命周期、关闭浏览器后新服务/缓存恢复、桌面及390px真实移动视口通过。 |
| 外调与保护 | 全部Python门禁外部连接0、非回环尝试0；浏览器页面外部请求0。实际源/反馈原子失败测试均保留旧文件；旧公开证据及无关源文件保护检查见本批检查JSON。真实隔离材料不读取、不哈希、不复制、不回放。 |

浏览器原始合成HTML/PNG/summary.json位于Git忽略目录：`.pytest-run-u5-implementation/20261011/browser-6567df5e-e0e1-4425-9c8f-4a93237bbfd0/`。最新公开摘要见[u5-checks.json](u5-checks.json)。关闭旧浏览器及旧服务进程，启动新Python进程；组件及完整SPA恢复的反馈写请求均0，model/weather/maps/photos/memory均0。GET计数允许增加，不等于生成。公开API仅提供last_mutation_id用于核对，内部请求摘要不下发。

全部本批命令从项目根执行，前端命令另在frontend目录：

```powershell
./.venv/Scripts/python.exe -X utf8 .pytest-run-u5-implementation/20261011/offline.py pytest -q -p no:cacheprovider --basetemp=.pytest-run-u5-implementation/20261011/full-final-temp --junitxml=.pytest-run-u5-implementation/20261011/full-final.xml
./.venv/Scripts/python.exe -X utf8 .pytest-run-u5-implementation/20261011/offline.py pytest tests/test_result_feedback.py tests/test_saved_results.py tests/test_local_boundaries.py tests/test_architecture.py tests/test_frontend_contract.py -q -p no:cacheprovider --basetemp=.pytest-run-u5-implementation/20261011/related-final-temp --junitxml=.pytest-run-u5-implementation/20261011/related-final.xml
./.venv/Scripts/python.exe -X utf8 .pytest-run-u5-implementation/20261011/offline.py smoke
./.venv/Scripts/python.exe -X utf8 .pytest-run-u5-implementation/20261011/offline.py layers
./.venv/Scripts/python.exe -X utf8 .pytest-run-u5-implementation/20261011/offline.py l2
./.venv/Scripts/python.exe -X utf8 .pytest-run-u5-implementation/20261011/offline.py schema
./.venv/Scripts/python.exe -m ruff check src/lighttrail/contracts/feedback.py src/lighttrail/infra/feedback.py src/lighttrail/application/feedback.py src/lighttrail/api/feedback.py src/lighttrail/api/session.py src/lighttrail/api/app.py src/lighttrail/composition.py tests/test_result_feedback.py tests/u5_restore_probe.py
./.venv/Scripts/python.exe -m ruff format --check src/lighttrail/contracts/feedback.py src/lighttrail/infra/feedback.py src/lighttrail/application/feedback.py src/lighttrail/api/feedback.py tests/test_result_feedback.py tests/u5_restore_probe.py
```

```powershell
# frontend目录；本批合成服务8785及Vite4173已启动。
node node_modules/openapi-typescript/bin/cli.js .openapi.json -o src/api/generated.ts
npm run build
npm run test:u2-1
node scripts/u5-browser-check.mjs
```

## V01–V15实测矩阵

下表测试均在[本批pytest](../../../tests/test_result_feedback.py)或[真实组件脚本](../../../frontend/tests/u5-browser.tsx)中，不拿旧238项充作U5验收。

| 编号 | 实际覆盖 | 结果 |
|---|---|---|
| V01 | not_went/not_observed/null，原因可空；无自动经验；原会话及有效期不变；D3保存 | 通过 |
| V02 | 三种出门状态×unknown/not_observed；not_seen、结果原因和范围非法组合拒绝 | 通过 |
| V03 | not_went但本人别处observed，有独立范围；不会认证原机位 | 通过 |
| V04 | seen/not_seen/uncertain均合法，范围必填；地点/窗口未知页面提示不可判完整计划 | 通过 |
| V05 | 已确认经验编辑反馈后原子needs_review/confirmed_at=null；直接重确认409；重存draft后明确确认 | 通过 |
| V06 | 反馈撤回清空经验/正文，旧CAS409；独立进程墓碑恢复；最新版本重新填写无旧经验 | 通过 |
| V07 | 只撤回经验不改反馈，content/confirmed_at清空，withdrawn_at保留；修改反馈不复活 | 通过 |
| V08 | 他人GET、PUT、反馈/经验撤回、候选/确认全部404，仓库读写替身若被触及即失败 | 通过 |
| V09 | 新Python进程active/withdrawn只GET，模型/外部连接0，JSON不变；浏览器关闭及完整SPA恢复不增写入/外调；LRU中未保存会话不落盘 | 通过 |
| V10 | 首存目录写失败＋save/候选/confirm/反馈撤回/经验撤回替换失败；旧文档不变、saved=false；真实页面不假成功 | 通过 |
| V11 | 两线程同版本CAS只胜一个；相同最新mutation重放不增版、改正文409、后续变更后旧操作409；生产保存后损坏响应由页面只读核对 | 通过 |
| V12 | 旧无反馈读取/空撤回不写文件；legacy变源409/缺失可撤回；损坏及未知schema503且不覆盖 | 通过 |
| V13 | 生产保存响应延迟时切版本、停止等待、卸载；另一结果保持unknown空态，旧响应不能回写，无取消承诺 | 通过 |
| V14 | 原反馈不生候选；独立草稿/确认与反馈版本；未观察拒绝确认观察经验；记忆写端口若被调用即失败，文件与Agent注入字节/内容不变 | 通过 |
| V15 | 非空photo_refs及额外身份字段422，页面无file控件，无U5照片入口或图片调用 | 通过 |

## 失败迭代与验收边界

初次后端19通过/1失败，原因是故障替身未接受mkdir的kwargs；补正后通过。独立进程首次22通过/2失败，探针误阻断Windows事件循环socketpair回环；修为仅允许回环、仍阻断业务DNS/连接后24通过。生产代码未靠放行外网解决。

浏览器初轮因脚本未等按钮重新可用而误点，补为按组件状态等待；后续导航时已取消的CDP拦截ID引发驱动异常，只忽略已失效ID，其余异常仍失败。移动验收页补viewport，完整SPA检查innerWidth=390、一列且无横向溢出；截图前关闭平滑滚动并核对面板在可见范围，已实看最新PNG。以上失败材料保留于本批隔离目录，未计为通过。Ruff受限测试文件写入WinError5后，使用项目ruff标准输出生成apply_patch，最终静态门禁通过。

单进程本机部署；不保证多worker、多进程同时写、断电下所有文件系统耐久性、完整历史正文审计、自由文本事实核验或概率校准。仅本地当前记录撤回，不宣称清除本人备份、截图、导出或供应商副本。未验证公网/跨设备/部署、真实摄影效果、照片上传或经验自动参与未来建议。

本人未出门，无独立现场观察，现场效用无法判读；[原真实记录](../2026-10-09/u4-real-verification.md)、原卡03:36有效期、原清理异常/退出码1及[最新U4技术验收](u4-aperture-assertion-verification.md)均保留。唯一真实轮次仍模型1/天气1，本批0，不补跑、不换日期、不进入U6、不提交或发布。更新路线图与HANDOFF后停止。
