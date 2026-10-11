/** 既有规划浏览器替身的协议适配；U3 主验收使用真实离线生产 API。 */
let saved: Record<string, unknown> | null = null;
export function saveFixturePlan(plan: Record<string, unknown>): Response {
  saved = { session_id: "fixture-plan", result_id: "fixture-version", version: 1, kind: "spot_plan",
    created_at: "2026-10-03T00:00:00Z", request: plan.request, plan, history: [], pipeline: {}, trace: {} };
  return Response.json(saved);
}
export function readFixturePlan(url: string): Response | null {
  if (url === "/api/sessions") return Response.json(saved ? [{ session_id: saved.session_id,
    history: [], pipeline: {}, workspace: {}, user_id: "_local", created_at: saved.created_at,
    updated_at: saved.created_at, results: [saved] }] : []);
  if (url.startsWith("/api/sessions/fixture-plan/results/")) return Response.json(saved);
  return null;
}
