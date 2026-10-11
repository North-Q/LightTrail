# U2-3 本地权限与数据边界验收

日期：2026-10-07。基线 main / 4b04b81 + 已有未提交内容及 U1/U2-1/U2-2；155 个已有文件先记录 SHA-256，未 reset、stash、提交或发布。本批仅合成档案/图像、假 Key、内存 HTTP 与离线模型替身；没有真实 API、个人数据外发、安装依赖或多智能体。

## 实际反例与修复

| 核查 | 修复前证据 | 修复及行为预期 |
|---|---|---|
| 写来源 | 7 种越界条件均能 PUT 档案：公网 Origin、null、未允许本机端口、伪本地域名、异常 Host、非本机连接及无 Origin cross-site | 来源边界在所有路由之前检查本机实际客户端/Host；写请求再检查 Origin/Fetch-Site/Referer；拒绝为 403，档案不变。4 种正常本机前端/同源/无 Origin 流程保留。 |
| 凭据 | 非 sk 前缀假 LLM Key 与百度 AK 出现在 Settings repr；SDK 400 原异常链含假 Key；作用域故障经日志/SSE 原样返回 Key | 四类服务端凭据均登记及过滤；repr/str 隐藏；日志在 handler 前净化异常栈；trace 名称、嵌套键值和摘要先过滤再截断；API JSON/文本/SSE 按完整帧净化。HTTP 400/401/403 与 synthetic request_id 保留。 |
| 文件 | 已存在、可解码的未授权 PNG 被照片工具读取并发起离线模型调用，最终仅因 cassette 不存在失败 | 入口授权确切路径，生产上下文携带能力；模型默认无能力，读取 EXIF/图像前拒绝。单文件授权不能覆盖父目录、相邻文件或变化后的规范目标。 |
| 正常照片 | 原上传有大小/MIME 约束及自选临时文件，这些控制保留 | 上传文件名不选择本地路径，后缀依据允许 MIME；线程结束后 finally 删除。生产注册表可分析授权的合成 JPEG，发送 JPEG data URL 和选定 EXIF；既有正常上传管线仍通过。 |
| 数据说明 | 仅局部地图提示，复盘“不会自动保存”可能让人误认为会话也未保留 | D1–D4/地图/CLI 在发送前说明内容、接收方、用途；M1 明确当前档案删除、历史会话、浏览器和供应商副本的不同边界。实际目录与尚未覆盖部分见 [数据边界](../../DATA-BOUNDARIES.md)。 |

初轮系统 pytest 临时目录 ACL 拒绝导致 15 个 setup error，不作为反例成立证据；改用工作区专用 basetemp 后，本批初始边界测试 **12 failed / 5 passed**。最终边界与 SSE 新增 **31 passed**。

小北明确确认：**允许公开 AMAP_JS_API_KEY 标识，服务端密钥全部隐藏**。公开标识仍由 `/api/map/config` 下发，不登记为服务器秘密；LLM Key、高德 Web Key、jscode、百度 AK 隐藏。测试公开标识与另一测试私有假 Key 的子串重叠曾导致误掩码，改用独立合成值；确切值过滤可能对包含相同文本的正常内容脱敏，不能作为一般个人数据匿名化保证。

本机规则：Host 为 localhost/127.0.0.1/[::1] 可带合法端口；实际连接必须 loopback，不信任 Forwarded 头。写 Origin 为 HTTP 本机 5173/4173/8000 或当前同源，其他跨端口不自动允许。无 Origin 本机程序正常，跨站 Fetch-Site 和异源 Referer 拒绝；重复 Host/Origin 拒绝。CORS 有限配置仅提供浏览器读取规则，不替代写校验。它不隔离本机恶意程序或被控制的允许来源，不适用于公网认证。

## SSE 独立核查

不是用重跑通过宣称修复。新增确定性反例分开验证两种故障：

1. **仅 step 缺失**：截住事件桥的跨线程投递，线程完成通知正常；旧桥已安排 step 回调，但 worker 直接发送 done，消费端在 done 后结束。完整旧响应只有 done。新桥保留待发事件并在终态前 flush；当前完整响应为 step → done，延迟回调不重复投递。此用例没有 card，不把“本来不应有 card”误记为丢失。
2. **结束状态缺失**：正常计算/文本结束后，保存会话注入 OSError。原 chat/decide 将保存放在 try 外，worker 异常退出，只有 queued/step，没有 error/done，连接在核查时限内无法结束。已逐块记录所有实际发送内容，见 [修复前响应](u2-3-sse-before.json)；不存在可以冒称“完整正常结束”的响应。新代码将保存纳入异常边界，所有相关流 finally 发 done；保存失败明确 error → done，不能伪装成功保存。

新保存故障测试的 decide 请求使用已知超工具集关键词，不调用模型，因此保存故障独立于模型错误；正常 card 交付由原 API/U1/生产管线回归验证。reverse 的同类保存边界同步收紧，review 原 finally 保留，并加入事件 flush。完整修复后及延迟前后帧见 [SSE 响应归档](u2-3-sse-responses.json)。

以上确认了可复现的同类投递竞态，并修复确定性的保存异常缺 done；**未声称追溯所有历史偶发缺项的唯一根因**。取消响应等待依旧不能终止已运行同步线程或供应商请求；不改变 U2-1 取消能力边界。

## 门禁与保护

| 检查 | 结果 |
|---|---|
| 全量 pytest | **579 passed**；新增 31 项 |
| 本批与 U1/U2-1/U2-2 相关 | **133 passed**；涵盖来源、四类假 Key、正常/越界照片、SSE、失败降级、时效、取消/次数、生产规格和数值 |
| Ruff | check / format --check，**158 个 Python 文件**通过 |
| smoke / 分层 | **21/21**；**3 kept / 0 broken**，87 files / 238 dependencies |
| L2 合成 | 管线 **12/12**、工具 **10/10**、**0 真实 LLM** |
| 契约 | 新增 DataBoundaryInfo 接收方元数据，沿原 OpenAPI→TS 生成；重复生成 SHA-256 相同 |
| 前端 | TypeScript/Vite build；辅助 **3/3 + 2/2**，SSE/请求生命周期 **8/8**，壳层 **7/7** |
| 浏览器 | U2-3 **1440×900、390×844** 发送说明/接收方/删除边界无横向溢出；U1 与 U2-1 原护栏通过 |
| 文档 / 工作区 | 本地链接、状态一致性、已有文件哈希及未授权改动核对见 [检查记录](u2-3-document-checks.json) |

全量过程中修正新增 ASGI 类型的未声明直接依赖（改用 FastAPI 公开接口与标准库类型，未加依赖）、两项旧照片测试的明确文件授权、测试公开标识与私有假 Key 重叠，以及净化非重试 ValueError 时保留原类型。底层许可、SDK/外层次数和阶段超时实现不变；原预算测试仍通过。旧 HTTP 测试的模拟 Host 统一从 test 改为 localhost，不为测试添加生产权限后门。

## 人工离线验收

在 `D:\Project\LightTrail` 执行，均不需要真实 Key：

```powershell
.venv\Scripts\python.exe -m pytest tests/test_local_boundaries.py tests/test_sse_delivery.py -v --basetemp=.pytest-run-u2-3/manual -o cache_dir=.pytest-run-u2-3/cache
.venv\Scripts\python.exe -m pytest tests/test_decision_evidence.py tests/test_weather_validity.py tests/test_request_budget.py tests/test_u2_2_calculations.py -q --basetemp=.pytest-run-u2-3/manual-guards -o cache_dir=.pytest-run-u2-3/cache
```

1. `untrusted_cannot_write` 必须 403 且档案不变；`local_flows_stay_available` 必须 200。不用对真实档案发测试写入。
2. 凭据测试应保留 synthetic 状态/编号，repr、日志、trace、异常格式化和 API/SSE 中无假私有 Key；公开 SDK 标识仍可用。授权图片通过生产注册表；父目录/相邻/无能力读取被拒。
3. `delayed_trace` 必须 step 在 done 前且迟到回调不重复；`save_error` 必须 error 后一个 done，无持续等待。
4. 前端用专用离线 Vite，后端代理设为不可用本机端口，页面请求全部由合成检查替身处理：

```powershell
cd frontend
$env:LIGHTTRAIL_API_TARGET='http://127.0.0.1:1'
npm run dev -- --host 127.0.0.1 --port 5177 --strictPort
```

打开 `http://127.0.0.1:5177/tests/u2-3.html`，应显示 PASS，初始停在合成 M1 页面；可用顶部 D1–D4/M1 按钮切换查看发送前说明与“本地数据与删除说明”。运行自动检查可在另一个终端设置 `$env:LIGHTTRAIL_UI_BASE='http://127.0.0.1:5177'` 后执行 `npm run check:u2-3-ui`，它会逐页验证 D1/D3 发送前、D1/D4 选图前、规划/地图接收方及 M1 删除说明。此合成入口不读取真实浏览器历史，也不发送真实照片；如原样启动正常页面，**不要为验收选择个人照片或真实发送**。

正常部署前端使用允许的 5173/4173 或 API 同源；5177 仅为本批静态合成验收端口，不因本次检查被加入生产允许来源。CLI 本地文件需 `--photo PATH`，模型自行指定其他路径不获许可。

## 剩余边界与停止点

没有登录/公网认证、完整本地清理 UI、事件单条删除或供应商撤回。强制退出可能留下临时照片，文件检查与读取仍存在本机进程竞态；规范目标变化用 resolver 替身验证，未将其冒充 Windows 实际符号链接集成验证。供应商保留/删除与真实费用、独立安装和真实拍摄尚未验收；U2-2 的计算近似与 U3 的结果恢复缺口保留。真实 API、个人数据外发与发布需另行确认。本批完成后暂停，不进入 U3。
