/** API 客户端：fetch 封装 + SSE 封装（E7-5，事件分发到页面状态）。 */

import type { DecisionCard, SSEEvent } from "./events";

/** 后端基址：默认走 Vite 代理（/api → 127.0.0.1:8765），可被环境变量覆盖。 */
const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

/** 从一帧 SSE 文本解析事件。 */
function parseFrame(frame: string): SSEEvent | null {
  let type = "";
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) {
      type = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trim());
    }
  }
  if (!type || dataLines.length === 0) {
    return null;
  }
  try {
    return { type, ...JSON.parse(dataLines.join("\n")) } as SSEEvent;
  } catch {
    return null;
  }
}

/** 流式读取 SSE 响应体并逐帧回调（供 JSON / multipart 两类 POST 复用）。 */
async function pipeSSE(resp: Response, onEvent: (event: SSEEvent) => void): Promise<void> {
  if (!resp.body) {
    throw new Error("响应无流式内容");
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    for (;;) {
      const index = buffer.indexOf("\n\n");
      if (index < 0) {
        break;
      }
      const frame = buffer.slice(0, index);
      buffer = buffer.slice(index + 2);
      const event = parseFrame(frame);
      if (event) {
        onEvent(event);
      }
    }
  }
}

/** POST JSON 并流式读取 SSE：onEvent 按到达顺序回调，AbortSignal 可中断。 */
export async function postSSE(
  url: string,
  body: Record<string, unknown>,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(BASE + url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok) {
    throw new Error(`请求失败（${resp.status}）：${(await resp.text()).slice(0, 200)}`);
  }
  await pipeSSE(resp, onEvent);
}

/** POST multipart（照片上传）并流式读取 SSE：用于 /api/photos/review 照片复盘/反推。 */
export async function postFormSSE(
  url: string,
  form: FormData,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(BASE + url, {
    method: "POST",
    body: form,
    signal,
  });
  if (!resp.ok) {
    throw new Error(`请求失败（${resp.status}）：${(await resp.text()).slice(0, 200)}`);
  }
  await pipeSSE(resp, onEvent);
}

/** EventSource 封装：对 GET 型 SSE 端点建立连接，返回断开函数。 */
export function connectSSE(url: string, onEvent: (event: SSEEvent) => void): () => void {
  const source = new EventSource(BASE + url);
  const types = ["queued", "step", "tool_call", "tool_result", "token", "card", "error", "done"];
  const handlers = types.map((name) => {
    source.addEventListener(name, (raw) => {
      const payload = (raw as MessageEvent).data as string;
      try {
        onEvent({ type: name as SSEEvent["type"], ...JSON.parse(payload) } as SSEEvent);
      } catch {
        // 忽略无法解析的帧
      }
    });
    return name;
  });
  return () => {
    for (const name of handlers) {
      source.removeEventListener(name, () => undefined);
    }
    source.close();
  };
}

/** 发送自由对话（ReAct）。 */
export function sendChat(
  message: string,
  sessionId: string,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return postSSE("/api/chat", { message, session_id: sessionId }, onEvent, signal);
}

/** 发送一句话决策。 */
export function sendDecide(
  request: string,
  sessionId: string,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return postSSE("/api/decide", { request, session_id: sessionId }, onEvent, signal);
}

/** 拉取会话详情（历史 + 工作区，含最近决策卡）。 */
export async function getSession(sessionId: string): Promise<{ session: SessionPayload }> {
  const resp = await fetch(BASE + `/api/sessions/${sessionId}`);
  if (!resp.ok) {
    throw new Error(`会话不存在（${resp.status}）`);
  }
  return (await resp.json()) as { session: SessionPayload };
}

interface SessionPayload {
  session_id: string;
  history: { role: string; content: string }[];
  workspace?: { last_card?: DecisionCard };
  user_id?: string;
  created_at?: string;
  updated_at?: string;
}
