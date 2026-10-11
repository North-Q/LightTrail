/** U5普通JSON协议；错误区分服务端拒绝与响应未确认。 */
import type { components } from "./generated";
import type { ResultPointer } from "./savedResults";

export type FeedbackView = components["schemas"]["FeedbackView"];
export type RawFeedback = components["schemas"]["RawFeedback"];
export type ExperienceKind = components["schemas"]["LocalExperience"]["kind"];
const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

export class FeedbackRequestError extends Error {
  constructor(public readonly status: number | null, message: string) { super(message); }
}

export function feedbackPath(target: ResultPointer): string {
  return `/api/sessions/${encodeURIComponent(target.session_id)}/results/${encodeURIComponent(target.result_id)}/feedback`;
}

export async function requestFeedback(target: ResultPointer, signal: AbortSignal,
  method: "GET" | "PUT" | "POST" = "GET", suffix = "", body?: Record<string, unknown>): Promise<FeedbackView> {
  let response: Response;
  try {
    response = await fetch(BASE + feedbackPath(target) + suffix, {
      method, signal, cache: "no-store",
      ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
    });
  } catch { throw new FeedbackRequestError(null, "响应未确认；可只读核对保存状态。停止等待不能确认服务端写入已取消。"); }
  if (!response.ok) {
    const message = response.status === 409 ? "记录或原结果已改变；请只读刷新后核对，未覆盖新版本。"
      : response.status === 500 ? "保存失败，未确认已保存；原记录保留。"
      : response.status === 503 ? "记录不可读或格式不支持；未覆盖旧记录。"
      : response.status === 404 ? "结果不存在或不属于当前用户。" : "字段或确认资格不符合要求；未保存。";
    throw new FeedbackRequestError(response.status, message);
  }
  try { return await response.json() as FeedbackView; }
  catch { throw new FeedbackRequestError(null, "响应未确认；请只读核对保存状态。"); }
}
