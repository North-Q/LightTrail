/** D2 规划请求只使用用户本轮明确选择的日期、机位与题材。 */

export interface PlanSelection {
  date: string;
  spot: string;
  subject: string;
}

function coordinate(value: unknown): number | null {
  if (typeof value !== "number" && (typeof value !== "string" || !value.trim())) {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** 只允许明确标注 WGS84 的机位进入现有规划计算。 */
export function usableSpotName(spot: Record<string, unknown>): string | null {
  const name = spot["name"] ?? spot["名称"];
  const latitude = coordinate(spot["latitude"] ?? spot["纬度"]);
  const longitude = coordinate(spot["longitude"] ?? spot["经度"]);
  if (
    spot["coordinate_system"] !== "WGS84" ||
    typeof name !== "string" || !name.trim() || latitude === null || longitude === null ||
    latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180
  ) {
    return null;
  }
  return name.trim();
}

/** 构造与现有意图路由兼容的明确规划请求；缺少选择时拒绝提交。 */
export function buildPlanRequest(selection: PlanSelection): string | null {
  const { date, spot, subject } = selection;
  const target = new Date(`${date}T00:00:00Z`);
  if (
    !/^\d{4}-\d{2}-\d{2}$/.test(date) ||
    Number.isNaN(target.getTime()) ||
    target.toISOString().slice(0, 10) !== date ||
    !spot.trim() ||
    !subject.trim()
  ) {
    return null;
  }
  return `${date}去${spot.trim()}拍${subject.trim()}，请制定拍摄计划，列出该机位的天象与天气窗口。`;
}
