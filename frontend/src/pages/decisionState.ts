/** 决策时效只读判断：保留服务端时间，存储或刷新均不能为旧结果续期。 */
import type { DecisionCard } from "../api/events";

export function decisionState(card: DecisionCard, now = Date.now()): "needs_data" | "stale" | "ready" {
  if (card.data_status === "needs_data") return "needs_data";
  const expires = Date.parse(card.valid_until ?? "");
  const updated = Date.parse(card.updated_at ?? "");
  const offset = /^([+-])(\d{2}):(\d{2})$/.exec(card.tz_offset ?? "");
  if (!offset || !Number.isFinite(expires) || !Number.isFinite(updated) || expires <= now || updated > now) return "stale";
  const minutes = (Number(offset[2]) * 60 + Number(offset[3])) * (offset[1] === "+" ? 1 : -1);
  const today = new Date(now + minutes * 60_000).toISOString().slice(0, 10);
  if (card.target_date !== today || card.data_status !== "ready") return "stale";
  if (card.window_end && Date.parse(card.window_end) <= now) return "stale";
  return "ready";
}
