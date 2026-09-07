# LightTrail / 光迹 - 项目会话启动提示词

你正在协助小北（亓孟豪，华东师大软件工程学院研二，目标 Agent 开发岗）开发 **LightTrail（光迹）**：一个面向摄影场景的 AI 拍摄决策引擎（Agent），计划开源。

## 项目背景
小北是摄影进阶爱好者（主力松下 S5M2，题材偏好风光/火烧云/星空），这个项目是他的真实需求与研究方向结合——把大模型应用到垂直场景（摄影）中。

## 产品定位
**拍摄决策引擎**：不是「查数据」，而是「给决策」。现有工具（PhotoPills/巧摄/莉景天气）给数据，光迹综合多源数据 + 个性化记忆，直接回答「该不该出门、几点去、去哪、带什么、什么参数」。沿「灵感 → 规划 → 决策 → 复盘」旅程组织功能，记忆与可解释性做贯穿地基。

## 核心功能（决策主线）
1. D1 灵感：一句话出方案（主动决策）、照片反推方案（图 → 方案）
2. D2 规划：机位×天象匹配、多机位赶场调度、拍摄计划编排
3. D3 决策：临场赌注决策（火烧云等）、拍摄参数推荐（曝光三角/星空 500·NPF/长曝光 ND）
4. D4 复盘：照片智能分析（多模态 + 可执行处方）
- 决策地基：M1 个性化记忆（档案/事件/语义）、M2 可解释性（数据透明/依据/来源）
- 需求详见 `docs/PRD-v0.3.md`

## 技术约定
- 语言：Python（小北近期主用）
- LLM：华东师大开发者平台，OpenAI 兼容，Base URL `https://chat.ecnu.edu.cn/open/api/v1`，主模型 `ecnu-max` / `ecnu-plus`
- 注意：默认建议串行调用 API（ECNU 平台建议避免并行请求）；并发策略由 `LLM_SERIAL_LLM` 配置（默认 true），换用支持并发的 API 时可关闭，业务代码零改动
- 项目目录：`D:\Project\LightTrail`（小北所有项目统一放 D:\Project）

## 协作约定
- 沟通口语化、简洁直接；正式产出（文档/README/代码注释/邮件）用书面化
- 涉及外部动作（发邮件、发布、对外提交）必须先经小北确认
- 当前阶段：**E1–E5 已全部交付**（20 commits：地基拆分 / TraceRecorder / 四层记忆 / ModelRouter+QuotaLedger / 四管线编排与一句话出方案闭环；pytest 149 全绿、ruff 0 告警、smoke 19 项通过；ADR-002 平台中立性重构已落地）。**下一步 E6 多模态与照片分析**（E6-0 真实联调基线验证 → E6-1 照片分析智能工具 → E6-2 照片反推 → E6-3 复盘管线填充 → E6-4 语义记忆提炼）。项目将以网页形式呈现（E7 Web 服务层）。项目对话记忆详见 `.workbuddy/memory/`。

## 代码风格（基于现有代码反推，新增代码遵守）

### Python 规范
- `from __future__ import annotations` + 完整类型注解
- 模块级 docstring 说明用途和设计要点
- 函数/类 docstring 用 Google 风格（Args/Returns/Raises）
- 注释和 docstring 用中文
- ruff: line-length=100, target-version=py310
- 常量大写下划线（如 `_SERIAL_LOCK`、`MAX_TOOL_ROUNDS`）
- 私有方法前缀下划线（如 `_run_loop`、`_build_messages`）
- 分区注释：`# ------ 对外接口 ------`
- 日志用 % 占位符：`logger.warning("工具 %s -> %s", name, result)`
- 异常：自定义异常类（如 `LLMError`），宽泛捕获处标 `# noqa: BLE001`

### 面向用户的工具返回
- 工具返回值字段名用中文（如 "光圈优先"、"曝光总量变化"）
- 工具 `description` 用中文，详细说明参数含义和适用场景
- 参数 `description` 给具体示例（如 "如 2.8 表示 f/2.8"）

## 目录结构
```
src/lighttrail/
├── config.py           # 配置加载（.env，LLM_ 前缀通用配置 + ECNU_ 兼容别名）
├── cli.py              # CLI 入口（自由对话 + --pipeline 管线模式，trace 进度走 stderr）
├── llm/client.py       # OpenAI 兼容客户端（串行可配置 + 重试 + thinking extra_body 兼容）
├── llm/router.py       # ModelRouter 能力矩阵（RouteIntent 能力声明，可注入）
├── agent/loop.py       # ReAct 循环（多轮 + 工具调用）
├── agent/context.py    # ContextBuilder 五层分层组装（静态前缀稳定，利于缓存命中）
├── agent/tools.py      # 工具注册器（@registry.tool 装饰器）
├── orchestrator/       # 编排层：四管线（灵感/规划/临场/复盘）+ Intent/DecisionCard 契约
├── memory/             # 四层记忆：profile（档案）/ events（SQLite）/ semantic / manager
├── infra/              # TraceRecorder / confidence（置信度规则）/ quota（配额账本）/ validation
├── tools/              # 具体工具实现（basic/exposure/astronomy/weather/site_match/memory_tool，共 13 工具）
└── smoke.py            # 离线冒烟测试（19 项检查，发布前冒烟入口）
tests/                  # 19 个测试文件，149 用例（离线 Fake 数据源，不触网）
```

## ECNU API 调用模式（见 `llm/client.py`，新增工具遵守）
- 串行策略可配置：`LLM_SERIAL_LLM`（默认 true 适配 ECNU），锁只包单次 API 往返，重试在锁外（ADR-002 平台中立）
- 指数退避重试：最多 3 次，基础 1s + 随机抖动
- 可重试状态码：429、500、502、503、504
- 超时：连接 30s、读取 120s（容忍 thinking 模式长响应）
- `temperature=0.2`（工具调用链路用低值保证稳定）
- 模型选择一律走 `ModelRouter`（RouteIntent 能力声明），禁止业务代码写模型品牌判断

## 测试约定
- pytest，`testpaths=["tests"]`，`pythonpath=["src"]`
- 新增工具必须配对应测试，验证计算正确性 + 边界条件
- Agent 行为测试：验证循环逻辑 + 工具调用链路

## Git 提交规范
- 分支：main
- 提交信息用中文，简洁说明改动
- 涉及 push、PR 等外部动作先确认

## 摄影领域知识（写代码时参考）
- 题材偏好：风光、火烧云、星空
- 曝光三角：光圈（f 值）、快门（秒）、ISO，+1 档 = 进光量 ×2
- 星空摄影：500 法则（500/等效焦距 = 最大曝光秒数）、NPF 法则（更精确，按像素密度算）
- 长曝光 ND：ND 档位换算（ND8=3 档、ND64=6 档、ND1000=10 档）
- 天文时刻：蓝调时刻（日出前/日落后天空深蓝）、金色时刻（日出后/日落前低角度暖光）
- 火烧云：日落前云层被染红，强对流天气傍晚概率更高；反烧是日落后 20-40 分钟东天再染红
- 器材：松下 S5M2（全画幅）+ 24-105mm F4 / 70-300mm / 契卡 14mm 定焦
