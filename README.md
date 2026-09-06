# LightTrail · 光迹

面向摄影场景的 AI 拍摄决策引擎（Agent），以 **Web 应用**形态呈现，计划开源（MIT）。

> 不是「查数据」，而是「给决策」——沿「灵感 → 规划 → 决策 → 复盘」的拍摄旅程，综合多源数据 + 个性化记忆，直接回答「该不该出门、几点去、去哪拍、带什么、用什么参数」。

## 核心功能

- **一句话出方案**：模糊意图（「这周拍银河」）→ 自动综合月相/天气/方位/光污染，直接给方案
- **照片反推方案**：丢一张参考大片 → 反推拍摄条件、机位与器材，生成复刻计划
- **机位 × 天象匹配**：按「题材朝向 × 光污染 × 可达性」匹配推荐机位
- **多机位赶场调度**：火烧云 → 蓝调 → 星空，时间 × 空间 × 光线调度
- **临场赌注决策**：火烧云该不该出门、再等多久，动态概率判断
- **拍摄参数推荐**：曝光三角、星空 500/NPF 法则、长曝光 ND 换算
- **照片智能分析**：多模态构图/曝光/色彩分析 + 可执行处方
- **个性化记忆**：记住你的器材、偏好、常拍点，越用越懂你
- **可解释性**：每个决策附依据、来源与置信度——且**过程实时可见**（SSE 流式轨迹面板）

## 技术方向

- **Agent 框架**：基于学校大模型开放平台（华东师大开发者平台，OpenAI 兼容 API）
  - Base URL：`https://chat.ecnu.edu.cn/open/api/v1`
  - 主模型：`ecnu-max`（1M 上下文深度推理）/ `ecnu-plus`（工具调用 + 多模态）
- **架构形态**：FastAPI 薄服务层 + SSE 事件流 + SPA 前端（「trace 即 UI」），CLI 双入口保留
  - 代码化管线（确定性编排）+ ReAct 自由对话双模混合
  - 四层记忆（短期/档案/事件/语义）+ 配额感知路由（QuotaLedger）
  - 结构化输出契约（pydantic 自愈重试）+ 三层评估体系（工具 pytest / 黄金用例集 / LLM-as-judge）
- **语言**：Python（近期主用）
- 探索大模型在垂直场景（摄影）中的应用方法
- 架构设计详见 [`docs/architecture.md`](docs/architecture.md)（v2.0）

## 目录结构

```
LightTrail/
├── docs/                # PRD v0.2 / 架构 v2.0 / 开发路线图 v2.0 / 设计原型
├── src/lighttrail/      # 源码（agent / llm / tools / orchestror / memory / api）
│   └── tools/           # 12 个已注册工具（曝光/天文/天气/机位匹配）
├── evals/               # 三层评估体系（golden 用例集 / judge）
├── frontend/            # Web UI（Vite + React + TS，规划中 E7-5）
└── README.md
```

## 当前状态

- [x] 项目初始化
- [x] Agent 骨架（多轮对话 + 工具调用最小链路）
- [x] 需求文档 v0.2（[docs/PRD-v0.2.md](docs/PRD-v0.2.md)）
- [x] 决策主线工具层：**12 个工具已注册**（参数推荐 / 天文查询 / 天气 / 火烧云评分 / 机位×天象匹配）
- [x] UI 高保真原型（[docs/design/delivery/lighttrail-prototype.html](docs/design/delivery/lighttrail-prototype.html)，6 页 SPA）
- [ ] 记忆与可解释性地基（路线图阶段二，E3+E4）
- [ ] 决策编排（一句话方案 / 计划 / 临场赌注，路线图阶段三，E5）
- [ ] 多模态与差异化（照片分析 / 反推 / 语义记忆，阶段四，E6）
- [ ] Web 服务层（FastAPI + SSE + 前端，阶段五，E7）
- [ ] 评估体系（阶段六，E8）
- [ ] 开源发布

开发进度与任务拆解见 [docs/DEVELOPMENT-ROADMAP.md](docs/DEVELOPMENT-ROADMAP.md)（v2.0，任务编号 E1-1 … E8-2）。