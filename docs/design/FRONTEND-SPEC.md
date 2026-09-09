# LightTrail（光迹）· 前端对齐设计规范（FRONTEND-SPEC）

**版本**：v1.0 ｜ **日期**：2026-09-09 ｜ **作者**：架构师 高见远（经小北审核）
**唯一视觉真源**：`docs/design/delivery/lighttrail-prototype.html`（129KB 单文件，断网可开；**只读，不改**）
**现状基线**：`frontend/` 已有 3 页（chat/sessions/card），E7-5 交付，212 测试全绿

---

## 1. 权威声明

1. **`lighttrail-prototype.html` 是唯一视觉真源**。所有页面结构、组件形态、交互、设计令牌只从它提取，**不新造色值/间距/字体**。
2. **令牌提取**：以 html `<style>:root{...}` 为准（§3 已提取为全集字典）。前端 `styles.css` 现有令牌与真源**有漂移**（如 `--bg #12121c` ≠ 真源 `--bg-base #0A0D14`、`--good #7fe0a3` ≠ `--semantic-go #3FCF8E`），需整体重对齐为真源取值。
3. **可解释性三铁律是验收硬门禁**（见 §6），不是可选项。
4. **后端不动**：E7-3 五端点 + SSE 8 事件已覆盖设计稿 6 页 90% 数据需求；新增板块（天象时间线、机位地图、偏好芯片、相似历史、处方列表等）用**结构正确的 Fake 数据渲染并标注「示例数据」**（可解释性诚实），不为此造后端。
5. **原版归档**：设计文档/原型 v1.1 已备份至 `docs/design/archive/`（`DESIGN-OVERVIEW-v1.1.md` / `交付说明-v1.1.md` / `lighttrail-prototype-v1.1.html`），不删。

---

## 2. 6 页信息架构与路由表

| # | 页面 | 建议 hash | 设计稿核心内容 | 关键组件 |
|---|------|-----------|----------------|----------|
| 1 | 旅程总览 | `#/home` | 今日决策速览（火烧云环图 + CTA）、四阶段入口卡、最近计划、记忆摘要、下一窗口预告 | Ring、StageCard、TodayCard |
| 2 | D1 灵感 | `#/d1` | 自然语言输入 + 示例 chips、参考图上传（反推）、方案卡 A/B/C（含依据链） | PromptBox、UploadZone、PlanCard |
| 3 | D2 规划 | `#/d2` | 机位列表（评分/朝向/距离 + 静态地图）、天象时间线（sky-band）、月相、银河可见窗口、赶场时间轴 | SpotCard、SkyTimeline、RouteTimeline |
| 4 | D3 决策 | `#/d3` | 三态大卡 + 倒计时 + 现场模式、概率依据条、置信度三层、曝光三角联动、「为什么这么判断」四步推理、相似历史 | VerdictCard、ConfidencePanel、ProbBars、ExposureTriangle、WhyPanel |
| 5 | D4 复盘 | `#/d4` | 批量上传区、4 维度分析（曝光/构图/色彩/时间）、可执行处方（高/中/低） | BatchUpload、AnalysisGrid、PrescriptionList |
| 6 | M1 记忆 | `#/m1` | 器材档案（可编辑）、事件历史（EXIF 摘要）、语义偏好芯片 | GearCard、EventList、PreferenceChips |

> 全局：顶栏 6 项导航 + 移动端汉堡抽屉；页脚「数据与依据 · 解释中心」模态（全局可开）。

### 现有 3 页去向（决策已定：融合，不删）

| 现有页 | 去向 |
|---|---|
| ChatPage（对话） | 融合进 D1 灵感页的输入 + 全站保留「对话」视图（可经顶栏辅助入口或总览页进入）——D1 的自然语言输入即自由对话入口 |
| SessionsPage（会话列表） | 并入旅程总览页「最近计划 / 会话历史」区（或保留辅助入口） |
| CardPage（决策卡片） | 升级为 D3 决策页的核心（DecisionCard 组件保留为底座，升级三态/置信度三层） |

---

## 3. 设计令牌全集（从真源 :root 精确提取）

### 3.1 基底（四级 + 双边框）

| 令牌 | 值 | 用途 |
|---|---|---|
| `--bg-base` | `#0A0D14` | 页面基底 |
| `--bg-sunken` | `#070910` | 沉底（更深，如 footer/深层面板） |
| `--bg-elevated` | `#111722` | 卡片/浮层 |
| `--bg-overlay` | `#182130` | 遮罩/模态 |
| `--border-subtle` | `rgba(255,255,255,.06)` | 发丝边框 |
| `--border-default` | `rgba(255,255,255,.10)` | 常规边框 |

### 3.2 文本（三级，WCAG AA）

| 令牌 | 值 | 对比度 |
|---|---|---|
| `--text-primary` | `#E8ECF4` | — |
| `--text-secondary` | `#9AA7B8` | — |
| `--text-muted` | `#7382A0` | bg-base 上 5.0:1 / elevated 上 4.6:1 ✓ |

### 3.3 点缀 + 品牌

| 令牌 | 值 |
|---|---|
| `--accent-amber` | `#E8A23B` |
| `--amber-hover` | `#F0B45C` |
| `--amber-glow` | `rgba(232,162,59,.30)` |
| `--accent-blue` | `#5F8DF2` |
| `--accent-blue-soft` | `#7CA6FF` |
| `--blue-glow` | `rgba(95,141,242,.28)` |
| `--dusk-gradient` | `linear-gradient(120deg,#E8A23B,#C06B4A 45%,#5B6EDB)` |

### 3.4 语义（三色 + bg 变体；**永不单靠颜色，须配图标+文字**）

| 令牌 | 值 | 语义 |
|---|---|---|
| `--semantic-go` | `#3FCF8E` | 去 / 成功 |
| `--semantic-wait` | `#E9C46A` | 再等等 / 提示 |
| `--semantic-risk` | `#EF6A5F` | 放弃 / 风险 |
| `--go-bg` | `rgba(63,207,142,.14)` | |
| `--wait-bg` | `rgba(233,196,106,.14)` | |
| `--risk-bg` | `rgba(239,106,95,.14)` | |

> ⚠️ 前端现状漂移核对：`--good #7fe0a3` → 应 `#3FCF8E`；`--bad #ef7a7a` → 应 `#EF6A5F`；`--warn` 应并入 `--semantic-wait #E9C46A`。

### 3.5 字体三栈

| 角色 | 栈 |
|---|---|
| `--font-ui` | `Inter,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif` |
| `--font-display` | `"Fraunces","Songti SC","Noto Serif SC",Georgia,serif`（标题/置信度主值） |
| `--font-data` | `ui-monospace,"SF Mono","Cascadia Code","JetBrains Mono",Consolas,monospace`（数字/日期/参数） |

### 3.6 间距 / 圆角 / 动效

- 间距（4px 基准）：`--sp-1:4px; --sp-2:8px; --sp-3:12px; --sp-4:16px; --sp-5:24px; --sp-6:32px; --sp-7:48px; --sp-8:64px`
- 圆角：`--r-sm:6px; --r-md:10px; --r-lg:14px; --r-full:999px`
- 动效：`--ease:cubic-bezier(.2,0,0,1); --t-fast:150ms; --t-panel:240ms; --t-max:300ms`（动效 ≤300ms，含 `prefers-reduced-motion` 降级）

### 3.7 天象带（sky-band，仅时间线与品牌时刻）

```
--sky-band: linear-gradient(90deg, #0d1322 0%, #101a2e 8%, #1b2a47 14%, #33405e 26%,
  #2c3a54 44%, #5B6EDB 52%, #C06B4A 62%, #E8A23B 66%, #C06B4A 70%, #7a4f8f 76%,
  #1a2240 82%, #0d1322 92%)
```

---

## 4. 关键组件规格（从真源提取）

### 4.1 三态决策卡（D3 核心）
- `.state-btn.go/wait/risk` + `.is-active` 高亮（inset 描边 + glow）
- 三要素齐备：**语义色 + 图标（SVG 圆形勾/时钟/三角）+ 文字**（铁律②）
- 现场模式（`.decision-panel.field-mode`）：切换为水平大按钮布局，倒计时 20→24px、参数卡 flex 横滑（scroll-snap + min-width 280px）

### 4.2 置信度三层（铁律①，D3 面板）
- **主值**（font-display 大号，如 72%）+ **区间条**（`.interval-scale` 含 `.interval-range` 主值定位 `.interval-marker`）+ **依据列表**
- 禁止单一数字/单一徽标

### 4.3 概率依据条（D3）
- `.prob-fill.go/amber/blue` 三色条，对应 go / 等候 / 提示

### 4.4 环图（总览/今日卡）
- SVG `.ring` + `.ring-bg` + `.ring-fill`（stroke-dashoffset 动画，过渡 240ms）

### 4.5 天象时间线（D2）
- `.sky-band`（height 96px，`background: var(--sky-band)`）+ `.sky-mark` 时间标记（日出/日落/蓝调/银河）
- 移动端横向滚动：`.sky-timeline .sky-band{min-width:680px}` + overflow-x

### 4.6 「为什么这么判断」面板（D3）
- 四步推理链（内联可折叠；桌面 grid 0fr→1fr 过渡；移动端 ≤820px 改底部抽屉 `#why-sheet`）
- 数据来源标签 + 置信度

### 4.7 曝光三角联动（D3）
- 光圈/快门/ISO 三滑块，拖动任一其余自动换算保持 EV（纯前端计算）

### 4.8 解释中心（全局页脚）
- 「数据与依据」按钮 → 模态，列本会话数据源清单（tool_result.data_source）

---

## 5. 组件 → API 数据映射表

> 后端 E7-3 五端点 + SSE 8 事件（queued/step/tool_call/tool_result/token/card/error/done）**已够用**。能接端点的接端点；端点和设计稿没有的板块用「示例数据」Fake 渲染。

| 设计稿板块 | 数据来源 | 现有 API |
|---|---|---|
| 今日决策速览 / 下一窗口预告 | /api/decide（card 事件）或 Fake | `decide` |
| D1 方案卡 A/B/C | /api/decide（灵感意图） | `decide` |
| D1 参考图上传反推 | /api/photos/review 或 Fake | `photos/review` |
| D2 机位列表 / 天象时间线 / 月相 / 银河窗口 | 工具结果经 tool_result 事件 / Fake | `decide` |
| D3 三态卡 + 倒计时 | card 事件（conclusion/time_window）+ 本地倒计时 | `decide` |
| D3 置信度三层 | card.evidence + confidence；区间条用本地规则化转换 | `decide` |
| D3 曝光三角联动 | 纯前端换算（EV 守恒） | — |
| D3 四步推理 | step 事件（TraceBridge 已推） | `decide` |
| D3 相似历史命中 | search_memory 工具结果或 Fake | `decide` |
| D4 批量上传 + 4 维分析 + 处方 | PhotoAnalysisReport（scene/composition/exposure/color/assessment/prescription/suggestions） | `photos/review` |
| M1 器材档案 | GET/PUT `/api/profile` | `profile` |
| M1 事件历史 / 偏好芯片 | /api/sessions/{id}、/api/profile 或 Fake | `sessions`/`profile` |
| 会话历史回放 | /api/sessions/{id} | `sessions` |
| 解释中心 | 汇总 tool_result.data_source | `decide` |

---

## 6. 可解释性三铁律（验收硬门禁）

1. **置信度禁单一数字**：主值 + 区间条 + 依据列表 三层结构（对应 D3 ConfidencePanel）。
2. **语义色永不单靠颜色**：go/wait/risk 三色必配图标 + 文字（对应三态卡）。
3. **数据与依据·解释中心**：页脚模态集中展示本会话数据源清单（对应 DataCenterModal）。

三条在 E7-9（D3）与 E7-10（解释中心）验收中逐条勾验。

---

## 7. 移动端规范

- 双视口自检：390×844（iPhone）/ 360×780（Android）
- ≤560px：顶栏导航收进**汉堡抽屉**（`#main-nav.is-open` + 遮罩 + 滑入 ≤300ms），尾区收纳辅助入口
- ≤820px：D3「为什么」面板改**底部抽屉**（handle + 标题 + 关闭 + 滚动区）
- 触控目标 ≥44px（`.btn/.icon-btn/.chip` ≤820 补 min-height）
- 天象时间线横向滚动（细滚动条）；倒计时等数字用 `--font-data` tabular-nums

## 8. Fake 数据标注规范（诚实原则）

- 凡端点和设计稿没有的真实数据（天象时间线细节、机位地图、偏好芯片、相似历史、处方列表），一律用**结构正确的 Fake** 渲染，并在界面显眼处标注「示例数据」。
- 不许用假数据冒充真实 API 结果；可解释性诚实是产品底线。

---

*本规范为前端开发的唯一 spec。每轮前端改动以本文件 + 真源 html 为准，改完 `npm run build` 通过 + 双视口自检。*
