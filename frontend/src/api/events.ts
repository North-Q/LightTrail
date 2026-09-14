/**
 * 前端契约门面（B4-1/B4-2）：后端契约类型一律从 `generated.ts` 取，本文件不再手抄字段。
 *
 * 真源链路：`src/lighttrail/contracts/*`（pydantic）→ FastAPI OpenAPI → openapi-typescript
 *          → `generated.ts`。契约变了就跑 `npm run gen:api`；
 *          `gen:api && git diff --exit-code` 是漂移门禁（未重新生成即失败）。
 */

import type { components, operations } from "./generated";

/** 决策卡片（后端 contracts.models.DecisionCard）。 */
export type DecisionCard = components["schemas"]["DecisionCard"];
/** 置信度明细（后端规则推导，前端只渲染）。 */
export type ConfidenceDetail = components["schemas"]["ConfidenceDetail"];
/** 一条决策依据。 */
export type Source = components["schemas"]["Source"];
/** 一条参数建议。 */
export type ParamSuggestion = components["schemas"]["ParamSuggestion"];
/** 一个推荐机位。 */
export type LocationSuggestion = components["schemas"]["LocationSuggestion"];
/** 用户档案（GET/PUT /api/profile）。 */
export type ProfilePayload = components["schemas"]["ProfilePayload"];

/**
 * SSE 事件负载判别联合（后端 contracts.events.SSEEventPayload）。
 *
 * 直接取 `/api/chat` 的 200 响应 schema：三个 SSE 端点共用同一份判别联合，
 * 事件类型增删会同时体现在这里，不靠人工同步。
 */
export type SSEEvent = operations["chat_api_chat_post"]["responses"][200]["content"]["text/event-stream"];

/** SSE 事件类型字面量联合（queued / step / tool_call / …）。 */
export type SSEEventType = SSEEvent["type"];

// ------ 前端本地 UI 类型（非后端契约，前端自用） ------

/** 轨迹面板条目（trace 即 UI：右侧实时滚动）。 */
export interface TraceItem {
  kind: "step" | "tool_call" | "tool_result";
  name: string;
  detail: string;
  tag?: string;
}

/** 会话列表项（本地记录；详情从后端 /api/sessions/{id} 拉取）。 */
export interface SessionMeta {
  id: string;
  title: string;
  created: string;
}

/** 会话详情（GET /api/sessions/{id}）。 */
export interface SessionDetail {
  session: {
    session_id: string;
    history: { role: string; content: string }[];
    workspace?: { last_card?: DecisionCard; last_trace?: unknown };
  };
  trace?: unknown;
}