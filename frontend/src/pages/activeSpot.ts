/** 本次活动只保存机位 ID；精确坐标不进入浏览器存储或 URL。 */

const ACTIVE_SPOT_KEY = "lt.active_spot_id";

export function getActiveSpotId(): string {
  try {
    return sessionStorage.getItem(ACTIVE_SPOT_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setActiveSpotId(id: string): void {
  try {
    if (id) {
      sessionStorage.setItem(ACTIVE_SPOT_KEY, id);
    } else {
      sessionStorage.removeItem(ACTIVE_SPOT_KEY);
    }
  } catch {
    // 浏览器禁用会话存储时，页面仍可手动选择机位。
  }
}
