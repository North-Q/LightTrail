/** 实际保存结果的只读恢复与版本续问；浏览器只存稳定指针。 */
import { useEffect, useRef, useState } from "react";
import { getJSON } from "./client";
import { useRequestScope } from "./useRequestScope";
import type { components } from "./generated";
import type { SSEEvent, SessionDetail } from "./events";

export type SavedResult = components["schemas"]["SavedResult"];
export type SavedSession = SessionDetail["session"];
export type ResultPointer = { session_id: string; result_id: string };

export function useSavedResults(kind: string, onRestore: (result: SavedResult, historical: boolean) => void) {
  const scope = useRequestScope();
  const callback = useRef(onRestore);
  callback.current = onRestore;
  const [pointer, setPointer] = useState<ResultPointer>({ session_id: "", result_id: "" });
  const [results, setResults] = useState<SavedResult[]>([]);
  const [active, setActive] = useState<SavedResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState("");
  const key = `lt.saved.${kind}`;

  async function restore(target: ResultPointer, historical = true): Promise<void> {
    const controller = scope.begin();
    setLoading(true);
    try {
      const path = `/api/sessions/${encodeURIComponent(target.session_id)}/results/${encodeURIComponent(target.result_id)}`;
      const result = await getJSON<SavedResult>(path, controller.signal);
      if (!scope.isCurrent(controller)) return;
      setActive(result); setPointer(target);
      callback.current(result, historical);
      setStatus(historical ? `历史回看 / 待更新 · 原始保存时间 ${result.created_at || "未记录"}` : "已保存实际结果，可关闭页面后恢复。");
      try { localStorage.setItem(key, JSON.stringify(target)); } catch { setStatus("结果已保存在服务端；浏览器指针保存失败，请从历史列表恢复。"); }
      if (!historical) {
        try { const items = await refresh(controller.signal); if (scope.isCurrent(controller)) setResults(items); } catch { /* 已落盘结果不因索引失败丢失。 */ }
      }
    } catch (error) {
      if (scope.isCurrent(controller)) {
        setPointer({ session_id: "", result_id: "" }); setActive(null);
        setStatus(`恢复失败，未重新查询：${error instanceof Error ? error.message : String(error)}`);
      }
    } finally { scope.finish(controller, () => setLoading(false)); }
  }

  async function refresh(signal?: AbortSignal): Promise<SavedResult[]> {
    const sessions = await getJSON<SavedSession[]>("/api/sessions", signal);
    return Array.isArray(sessions) ? sessions.flatMap(session => session.results ?? []).filter(result =>
      kind === "chat" ? ["chat", "decision"].includes(result.kind) : kind === "decision" ? ["decision", "spot_plan"].includes(result.kind) : result.kind === kind,
    ) : [];
  }

  useEffect(() => {
    const controller = scope.begin();
    void (async () => {
      try {
        const items = await refresh(controller.signal);
        if (!scope.isCurrent(controller)) return;
        setResults(items);
        let target: ResultPointer | null = null;
        const params = new URLSearchParams(window.location.hash.split("?")[1] ?? "");
        if (params.get("session") && params.get("result")) target = { session_id: params.get("session")!, result_id: params.get("result")! };
        else {
          try { target = JSON.parse(localStorage.getItem(key) ?? "null") as ResultPointer | null; } catch { /* 损坏指针不发查询。 */ }
          if (!target && kind === "chat") {
            const legacy = localStorage.getItem("lt.session.id");
            const matches = items.filter(item => item.session_id === legacy);
            const last = matches[matches.length - 1];
            if (last) target = { session_id: last.session_id, result_id: last.result_id };
          }
        }
        if (kind === "spot_plan" && target && !params.has("result") && (params.has("spot") || params.has("date") || params.has("subject"))) {
          const previous = items.find(item => item.session_id === target?.session_id && item.result_id === target?.result_id)?.plan;
          if (previous && ((params.has("spot") && params.get("spot") !== previous.request.spot?.site_id)
            || (params.has("date") && params.get("date") !== previous.request.target_date)
            || (params.has("subject") && params.get("subject") !== previous.request.subject_type))) target = null;
        }
        scope.finish(controller, () => setLoading(false));
        if (target?.session_id && target.result_id) await restore(target);
      } catch {
        if (scope.isCurrent(controller)) setStatus("历史列表读取失败；未自动重新查询。");
        scope.finish(controller, () => setLoading(false));
      }
    })();
    return () => scope.stop();
  }, []);

  function confirm(event: SSEEvent): void {
    if (event.type !== "done") return;
    if (event.saved === false) { setStatus("保存失败，未确认已保存；请重试。"); return; }
    if (!event.saved || !event.result_id || !event.session_id) { setStatus("此响应没有结果版本保存确认；可从会话历史核查。"); return; }
    const target = { session_id: event.session_id, result_id: event.result_id };
    setPointer(target);
    try { localStorage.setItem(key, JSON.stringify(target)); } catch { /* 服务端索引仍可恢复结果。 */ }
    void restore(target, false);
  }

  function clear(): void {
    scope.stop(); setLoading(false); setPointer({ session_id: "", result_id: "" }); setActive(null); setStatus("");
    try { localStorage.removeItem(key); } catch { /* 历史仍可从服务端读取。 */ }
  }

  function start(): void { scope.stop(); setLoading(false); setStatus("正在生成，尚未确认本次结果已保存。"); }

  return { pointer, results, active, loading, status, restore, confirm, clear, start };
}
