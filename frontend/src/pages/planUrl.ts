/** D2 分享链接只记录题材、拍摄日期和机位 ID，不携带坐标或计划正文。 */

export interface PlanUrlState {
  subject: string;
  date: string;
  spotId: string;
}

export const PLAN_SUBJECTS = ["日落", "日出", "火烧云", "晚霞", "银河", "星空", "蓝调", "夜景"] as const;

function validDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

export function parsePlanUrl(hash: string): { state: PlanUrlState; error: string } {
  const defaults: PlanUrlState = { subject: "日落", date: "", spotId: "" };
  if (!hash.startsWith("#/d2?")) return { state: defaults, error: "" };
  const params = new URLSearchParams(hash.slice("#/d2?".length));
  const subject = params.get("subject") ?? defaults.subject;
  const date = params.get("date") ?? "";
  const spotId = params.get("spot") ?? "";
  if (!PLAN_SUBJECTS.includes(subject as (typeof PLAN_SUBJECTS)[number])) {
    return { state: defaults, error: "链接中的拍摄题材无效。" };
  }
  if (date && !validDate(date)) {
    return { state: defaults, error: "链接中的拍摄日期无效。" };
  }
  if (spotId.length > 128 || /\s/.test(spotId)) {
    return { state: defaults, error: "链接中的机位 ID 无效。" };
  }
  return { state: { subject, date, spotId }, error: "" };
}

export function buildPlanHash(state: PlanUrlState): string {
  const params = new URLSearchParams();
  params.set("subject", state.subject);
  if (state.date) params.set("date", state.date);
  if (state.spotId) params.set("spot", state.spotId);
  return `#/d2?${params}`;
}

export function replacePlanUrl(state: PlanUrlState): void {
  window.history.replaceState(
    null, "", `${window.location.pathname}${window.location.search}${buildPlanHash(state)}`,
  );
}
