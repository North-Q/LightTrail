# LightTrail 前端

React + TypeScript + Vite。六个页面：#/home、#/d1、#/d2、#/d3、#/d4、#/m1。当前边界见 [FEATURES](../docs/FEATURES.md)，设计见 [FRONTEND-SPEC](../docs/design/FRONTEND-SPEC.md)。

## 开发与验证

在项目根目录运行 npm --prefix frontend ci，再运行 npm --prefix frontend run dev -- --host 127.0.0.1；API 默认由开发代理连接 127.0.0.1:8000，启动见 [README](../README.md)。依赖只装项目中。

| 命令（npm --prefix frontend run …） | 前置与用途 |
|---|---|
| gen:api | 本地 Python 依赖可用；导出 OpenAPI 后生成 src/api/generated.ts |
| build | TypeScript 检查与 Vite 构建 |
| check:ui | 先启动 Vite；六页路由及壳层检查 |
| test:r0-6 / test:s1-5 | 使用支持 experimental-strip-types 的 Node；组件辅助逻辑 |
| check:r0-6-ui / check:s1-3-ui / check:s1-5-ui | 按 scripts 内地址启动 Vite；合成浏览器 fixture 检查，不接真实服务 |
| check:u1-ui | 先按 [U1 验收记录](../docs/audit/2026-10-06/u1-verification.md) 导出生产 API 离线样本并启动 Vite；检查失败降级、补齐重查、冻结与历史标签 |
| test:u2-1 / check:u2-1-ui | SSE/生命周期辅助检查；启动 Vite 后运行 D1–D4 延迟替身，见 [U2-1 验收记录](../docs/audit/2026-10-06/u2-1-verification.md) |

共享 API 类型由生成物提供，事件门面不手抄另一份契约。U2-1 拦截停止/卸载后的旧事件、缓存与 finally，释放 SSE reader；停止等待不确认服务端或供应商已取消，也不撤销写入。U3 接入实际文字结果及计划的版本恢复和续问，合成检查不表示真实供应商全链路通过。

## 运行边界

VITE_API_BASE 仅部分封装使用，页面仍有直接 /api；优先同源开发，独立后端地址未全量接线。preview/build 不会自动提供生产 API 部署，公开托管另行批准。

正常页面无数据时显示空态/错误/补充动作；演示 fixture 与冻结计划清楚标注。数字不能把模型自报来源包装成校准概率。地图由用户主动加载，无地图不影响列表，不用假地图掩盖失败。

## U3 保存与恢复的专用离线验收

使用项目后端虚拟环境启动 `python -m evals.u3_preview --data-root .pytest-run-u3/manual-acceptance --port 8783`，在本目录设置 `LIGHTTRAIL_API_TARGET=http://127.0.0.1:8783` 并启动 Vite 4173 端口。打开 `/tests/u3.html`，或运行 `npm run check:u3-ui`。此入口使用合成数据、隔离浏览器存储和离线生产 API，不需要真实 Key；自动检查关闭第一个浏览器后重建服务及会话缓存，第二个浏览器恢复实际卡、消息和计划。

完整步骤及数据边界见 [U3 验收记录](../docs/audit/2026-10-07/u3-verification.md)。检查使用本机 Chrome/Edge 和 Node 原生 WebSocket，本次 Node 25.8.2；产品安装版本矩阵仍属于后续 U6，未在本批扩展。完成 U3 后停止，不进入 U4/U5/U6。
