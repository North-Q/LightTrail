/** API 客户端：fetch 封装 + SSE 封装（E7-5，事件分发到页面状态）。 */

import type { SessionDetail, SSEEvent } from "./events";
import { pipeSSE } from "./sse";

/** 后端基址：默认走 Vite 代理（/api → 127.0.0.1:8766），可被环境变量覆盖。 */
const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

/** 读取本地元数据，支持页面离开时中止等待。 */
export async function getJSON<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(BASE + url, { signal });
  if (!response.ok) throw new Error(`读取失败（${response.status}）`);
  return response.json() as Promise<T>;
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
  await pipeSSE(resp, onEvent, signal);
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
  await pipeSSE(resp, onEvent, signal);
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
  resultId = "",
  requery = false,
): Promise<void> {
  return postSSE("/api/chat", { message, session_id: sessionId, result_id: resultId, requery }, onEvent, signal);
}

/** 发送一句话决策。 */
export function sendDecide(
  request: string,
  sessionId: string,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
  resultId = "",
  requery = false,
): Promise<void> {
  return postSSE("/api/decide", { request, session_id: sessionId, result_id: resultId, requery }, onEvent, signal);
}

/** 拉取会话详情（历史 + 工作区，含最近决策卡）。 */
export async function getSession(sessionId: string, signal?: AbortSignal): Promise<SessionDetail> {
  const resp = await fetch(BASE + `/api/sessions/${encodeURIComponent(sessionId)}`, { signal, cache: "no-store" });
  if (!resp.ok) {
    throw new Error(`会话不存在（${resp.status}）`);
  }
  return (await resp.json()) as SessionDetail;
}

/** 发送普通 JSON 请求；用于不需要 SSE 的确定性领域接口。 */
export async function postJson<T>(url: string, body: Record<string, unknown>, signal?: AbortSignal): Promise<T> {
  const resp = await fetch(BASE + url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok) {
    throw new Error(`请求失败（${resp.status}）：${(await resp.text()).slice(0, 200)}`);
  }
  return (await resp.json()) as T;
}
