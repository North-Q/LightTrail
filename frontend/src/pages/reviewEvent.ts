/** D4 事件写入门禁：用户必须明确选择实际结果并确认。 */

export type ReviewOutcome = "" | "success" | "fail";

/** 决定是否允许当前复盘卡写入一条事件。 */
export function canWriteReviewEvent(
  hasCard: boolean,
  location: string,
  outcome: ReviewOutcome,
  saving: boolean,
  saved: boolean,
): boolean {
  return hasCard && Boolean(location.trim()) && Boolean(outcome) && !saving && !saved;
}
