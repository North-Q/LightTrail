/** SSE 事件类型（与后端 src/lighttrail/api/events.py 映射保持一致，E7-4 共享 schema）。 */

export type SSEEventType =
  | "queued"
  | "step"
  | "tool_call"
  | "tool_result"
  | "token"
  | "card"
  | "error"
  | "done";

interface SSEEventBase {
  type: SSEEventType;
  session_id?: string;
  ts?: string;
}

/** 决策卡片（后端 orchestrator/schemas.py DecisionCard 的 JSON 形态）。 */
export interface DecisionCard {
  conclusion: string;
  evidence: { tool: string; field: string; confidence: string; note?: string }[];
  confidence: string;
  time_window?: string;
  locations?: { name: string; reason?: string }[];
  params?: { name: string; value: string; reason?: string }[];
  alternatives?: string[];
  degraded?: string;
}

export interface QueuedEvent extends SSEEventBase {
  type: "queued";
  position: number;
}

export interface StepEvent extends SSEEventBase {
  type: "step";
  name: string;
  input_summary?: string;
  output_summary?: string;
}

export interface ToolCallEvent extends SSEEventBase {
  type: "tool_call";
  name: string;
  arguments?: string;
}

export interface ToolResultEvent extends SSEEventBase {
  type: "tool_result";
  name: string;
  result?: string;
  data_source?: string;
  confidence?: string;
  field?: string;
  elapsed_ms?: number;
}

export interface TokenEvent extends SSEEventBase {
  type: "token";
  content: string;
}

export interface CardEvent extends SSEEventBase {
  type: "card";
  card: DecisionCard;
  text?: string;
}

export interface ErrorEvent extends SSEEventBase {
  type: "error";
  message: string;
}

export interface DoneEvent extends SSEEventBase {
  type: "done";
  text?: string;
}

export type SSEEvent =
  | QueuedEvent
  | StepEvent
  | ToolCallEvent
  | ToolResultEvent
  | TokenEvent
  | CardEvent
  | ErrorEvent
  | DoneEvent;

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
