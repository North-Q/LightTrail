# U4 SDK 生命周期最小离线修复验收

日期：2026-10-09（+08:00）。本批由小北明确授权，仅修复 SDK 生命周期并离线验证，完成后停止。没有追加真实模型、天气、地图、照片、续问或重查，没有重跑已结束轮次、换日期、提交或发布，不进入 U5/U6。**SDK 生命周期修复通过离线验收，U4 现场效用仍待本人判读，整批 U4 未完成。**

## 先复现，再修复

原[真实轮次](u4-real-verification.md)保留事实：结果已保存，生成脚本清理 SDK 时抛 RuntimeError，退出码 1；现场未保存完整异常栈。[原离线探针](u4-event-fix-offline/cleanup-probe-redacted.json)记录 Event loop is closed。本批不改写原结果或原异常证据。

新增 `tests/test_sdk_lifecycle.py::test_sync_runtime_saved_result_closes_real_keepalive` 经真实生产链路 `build_application → AgentRuntime.complete → LightTrailModel → ChatClientProvider → ChatClient.acall → OpenAI SDK → 本机 HTTP/1.1 keepalive → SessionManager.save_result → ApplicationServices.close` 复现：保存版本可重新读取且内容相等，随后清理抛 **RuntimeError: Event loop is closed**。修复前该回归 **1 failed**，完整栈指向 Runtime 临时循环与 SDK/httpx 连接清理；脱敏结论见[复现摘要](u4-sdk-lifecycle-offline/red-summary.json)。这证明本机生产链路存在该缺陷，不把本批栈冒充原真实现场栈。

确认的代码原因：`runtime.agent._run_sync` 每次执行 `asyncio.run`，模型调用结束即销毁循环；`ChatClient` 的 SDK 连接池按 loop 缓存并跨调用保存。旧 `aclose` 将 `not loop.is_running()` 当成可以在当前循环关闭的条件，访问已经关闭的原 loop 时失败。旧实现还在实际关闭前清空句柄并设置 closed，后续 close 可能误报成功。

## 最小修复与资源归属

- `infra/async_runner.py` 提取已有同步门面的持久执行器；`runtime/agent.py` 和 `adapters/llm/client.py` 的同步门面共用它。Runtime 不再逐次创建、销毁承载 SDK 池的临时循环，连续补全、推理和 chat 可复用同一真实 keepalive。
- 共享执行器归进程持有；应用关闭自己的 SDK 池和知识索引，不终止其他应用仍可使用的共享循环。它不保存用户消息、结果或单独限流预算，不新增供应商路由或另一套模型调用实现。
- 异步入口继续在宿主 loop 执行；宿主必须先结束请求，再在自身 loop 停止前 `await services.aclose()`。CLI 原有 finally/close 和 FastAPI 原有 lifespan/aclose 资源归属保留；外部注入客户端/知识由注入方关闭。
- `ChatClient.aclose` 仅在当前所属 loop 直接关闭，其他仍运行的所属 loop 通过线程安全调度关闭。已停止/销毁的 loop 明确报 LLMError，不跨 loop 兜底；失败池保留句柄，成功关闭后才移除。关闭锁只串行化资源清理，不替代唯一的模型限流器。SDK 清理异常经过既有凭据过滤后显式报告，未吞掉异常。
- `composition.py` 补充同步/异步资源归属说明。限流、重试判定/三次尝试、SDK 重试零、阶段超时、输出上限、usage、凭据过滤和历史存储逻辑没有改写。

异步调用的正确边界如下，不能将 close 放在 asyncio.run 返回、所属循环已销毁之后：

```python
async def host() -> None:
    services = build_application(settings)
    try:
        await services.create_scope(context).runtime.acomplete("请求")
        # 在此保存实际结果。
    finally:
        await services.aclose()

asyncio.run(host())
```

同步宿主沿 `try/finally → services.close()` 使用 Runtime 同步入口。裸 `asyncio.run(client.acall(...))` 后再 close 属于提前销毁异步宿主的错误用法，本批不承诺复活已销毁 loop；它现在明确失败并保留句柄，不能被当成正常释放成功。

## 真实连接池验证

新增 9 项回归使用假 Key、回环随机端口、HTTP/1.1 和真实 SDK 默认网络传输，不用 MockTransport 代替生命周期。服务端不记录认证头，并等待连接 EOF/处理器退出，不能只靠 SDK is_closed 标志证明释放。

覆盖：生产同步生成后保存/关闭；同步补全、推理、chat 重复复用；异步宿主重复调用和归属关闭；同步/异步混合池分别关闭；HTTP 400 一次与 503 三次后清理；真实传输取消后的许可/连接释放；实际关闭前注入的清理失败可见、凭据过滤和句柄保留；暂停的所属循环禁止跨 loop 清理，原循环恢复后正确关闭。对提前销毁的宿主只承诺明确报错，不保证任意底层部分清理失败可以自动恢复。

独立修复后探针实际发送 **5 次本机合成模型请求，2 条 keepalive 连接**：同步补全/推理/chat 共 3 次复用一条，异步宿主 2 次复用一条；两池均在原 loop 关闭，服务端确认 **2/2** 连接退出，重复关闭成功、cleanup_error=null。两个合成版本保存前后字节不变，再由独立进程只读恢复 **2/2**，内容完全相等、保存文件写入 0、模型/天气/地图调用 0。见[探针摘要](u4-sdk-lifecycle-offline/probe-summary.json)、[独立恢复](u4-sdk-lifecycle-offline/restore-summary.json)。这些是新合成数据，不是原真实轮次的再次生成或恢复。

## 实际门禁与离线边界

| 项目 | 本批结果 |
|---|---|
| 缺陷复现 | 保存成功后 1 failed，RuntimeError: Event loop is closed |
| 新增 keepalive 回归 | 9 passed，9.91 秒 |
| 相关回归 | 110 passed，23.46 秒 |
| 全量 pytest | 640 passed，48.66 秒，原 631 + 新增 9 |
| Ruff | 本批 5 个生产/测试 Python 文件 check、format --check 通过 |
| smoke | 21/21 |
| import-linter | 3 kept / 0 broken |
| L2 既有合成回放 | 管线 12/12、工具 10/10；没有录制 cassette |
| 独立 keepalive / 恢复 | 原循环关闭 2/2；新进程恢复 2/2、结果写入/业务外调 0 |

环境：项目 `.venv`，Python 3.13.14、OpenAI SDK 3.1.0、httpx 0.28.1、pytest 9.1.1。没有安装依赖。未验证其他 Python/SDK 版本、wheel 安装、前端构建或浏览器；本批不改 API 契约和前端，既有相关门禁仍是上批证据，不伪称重跑。

驱动位于 Git 忽略的 `.pytest-run-u4-sdk-lifecycle/20261009/run_offline.py`。在 import 产品/API/pytest 前设置假 LLM/ECNU Key、空地图凭据、隔离数据目录和禁用代理；供应商主机名仅供来源说明断言。socket.connect/connect_ex 和 getaddrinfo 禁止非回环网络与业务 DNS，本批最终各门禁/探针非回环尝试均为 **0**。真实供应商发送 **0**。见[门禁摘要](u4-sdk-lifecycle-offline/gates.json)。

实际验证命令（项目根目录执行；复跑须换新的隔离输出目录，不覆写本批证据）：

```powershell
./.venv/Scripts/python.exe -X utf8 .pytest-run-u4-sdk-lifecycle/20261009/run_offline.py pytest -q tests/test_sdk_lifecycle.py tests/test_client_concurrency.py tests/test_request_budget.py tests/test_u4_controls.py tests/test_local_boundaries.py tests/test_saved_results.py tests/test_sse_delivery.py tests/test_production_composition.py --basetemp=.pytest-run-u4-sdk-lifecycle/20261009/related-final/tmp -o cache_dir=.pytest-run-u4-sdk-lifecycle/20261009/related-final/cache --junitxml=.pytest-run-u4-sdk-lifecycle/20261009/related-final.xml
./.venv/Scripts/python.exe -X utf8 .pytest-run-u4-sdk-lifecycle/20261009/run_offline.py pytest -q --basetemp=.pytest-run-u4-sdk-lifecycle/20261009/full/tmp -o cache_dir=.pytest-run-u4-sdk-lifecycle/20261009/full/cache --junitxml=.pytest-run-u4-sdk-lifecycle/20261009/full.xml
./.venv/Scripts/ruff.exe check src/lighttrail/infra/async_runner.py src/lighttrail/runtime/agent.py src/lighttrail/adapters/llm/client.py src/lighttrail/composition.py tests/test_sdk_lifecycle.py
./.venv/Scripts/ruff.exe format --check src/lighttrail/infra/async_runner.py src/lighttrail/runtime/agent.py src/lighttrail/adapters/llm/client.py src/lighttrail/composition.py tests/test_sdk_lifecycle.py
./.venv/Scripts/python.exe -X utf8 .pytest-run-u4-sdk-lifecycle/20261009/run_offline.py smoke
./.venv/Scripts/python.exe -X utf8 .pytest-run-u4-sdk-lifecycle/20261009/run_offline.py lint
./.venv/Scripts/python.exe -X utf8 .pytest-run-u4-sdk-lifecycle/20261009/run_offline.py l2
./.venv/Scripts/python.exe -X utf8 .pytest-run-u4-sdk-lifecycle/20261009/run_offline.py probe
./.venv/Scripts/python.exe -X utf8 .pytest-run-u4-sdk-lifecycle/20261009/run_offline.py restore
```

隔离驱动的临时目录父路径、项目根定位和 import-linter 入口曾配置错误，分别留下 fixture/collection/工具启动失败日志；修正驱动后完成上表门禁，没有用这些环境失败冒充产品反例。日志/XML、驱动、合成请求与会话仅在新的 Git 忽略隔离目录，不覆盖原 `.pytest-run-u4-event-fix/`。

## 保护与停止

开始前记录 **642** 个当前非忽略文件的 SHA-256，并对既有事件修复、晨间预检、交接复核和 data 四组目录计算保护摘要。隐私材料仅用于字节哈希核对，不解析、回显或复制至公开证据。分支 main / 4b04b81，原 174 条 Git 状态记录、暂存 0；保护、链接与收口检查见[本批收口](u4-sdk-lifecycle-offline/closeout-checks.json)。不 reset、stash、清理、暂存或提交已有改动，保留 `%SystemDrive%/` 来源未明目录及历史归档移动。

剩余问题不在本批修复：原卡备选 04:32 与一小时通勤冲突，三脚架/ND/14mm 最大光圈未确认；原卡 2026-10-10 03:36:28（+08:00）到期早于出发；实际点位、遮挡、开放、天气与现场拍摄效用待本人判读。本应用 4.2297 credits 仍只是原轮次观测估算，缓存未知、确定总费用 null；供应商思考限制、扣费和保留未核验。

本批结束后停止，不自动领取后续修复或 U5/U6，不追加真实请求。已执行真实轮次仍为 ECNU 1 / Open-Meteo 1，无重试；旧上限剩余尝试不构成新一轮授权。
