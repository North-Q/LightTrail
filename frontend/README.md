# LightTrail · 光迹 前端（E7-5）

Vite + React + TypeScript 单页应用，对接后端 `/api`（FastAPI + SSE）。

## 核心页（首迭代，3 页）
- **对话**（`#/chat`）：自由对话（ReAct）与「一句话决策」（管线），SSE 流式渲染
  token / 工具事件，右侧轨迹面板实时滚动（trace 即 UI）；决策结果以 DecisionCard 渲染。
- **会话列表**（`#/sessions`）：本地记录的会话 → 从后端 `/api/sessions/{id}`
  拉取历史回放（消息 + 最近决策卡）。
- **决策卡片**（`#/card`）：从历史会话读取 `workspace.last_card` 结构化渲染
  （结论 / 置信度 / 机位 / 参数表 / 依据来源）。

Trace 时间线与设置页为第二迭代（路线图 E7-5 裁剪原则：首迭代严格 3 页）。

## 运行
```bash
# 1. 启动后端（项目根 .env 配好 API Key）
python -m uvicorn lighttrail.api.app:app --host 127.0.0.1 --port 8765

# 2. 启动前端（/api 已代理到 8765）
cd frontend
npm install
npm run dev        # http://localhost:5173
```

## 约定
- SSE 事件类型定义在 `src/api/events.ts`，与后端 `src/lighttrail/api/events.py`
  映射保持一致（E7-4 共享 schema：queued/step/tool_call/tool_result/token/
  card/error/done）。
- 设计令牌：`src/styles.css`（品牌时刻黄昏渐变 #E8A23B → #5F8DF2）。
- 校验：`npm run build`（tsc 严格模式 + vite 打包）。
