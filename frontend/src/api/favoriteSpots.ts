/** 收藏机位 API：类型直接取自 OpenAPI 生成契约。 */

import type { components } from "./generated";

export type FavoriteSpotInput = components["schemas"]["FavoriteSpotInput"];
export type FavoriteSpotRecord = components["schemas"]["FavoriteSpotRecord"];
export type FavoriteSpotList = components["schemas"]["FavoriteSpotListPayload"];

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

async function requestJson<T>(method: string, path: string, body?: FavoriteSpotInput): Promise<T> {
  const response = await fetch(BASE + path, {
    method,
    ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
  });
  if (!response.ok) {
    throw new Error(`机位操作失败（${response.status}）`);
  }
  return (await response.json()) as T;
}

export function listFavoriteSpots(): Promise<FavoriteSpotList> {
  return requestJson<FavoriteSpotList>("GET", "/api/favorite-spots");
}

export function createFavoriteSpot(input: FavoriteSpotInput): Promise<FavoriteSpotRecord> {
  return requestJson<FavoriteSpotRecord>("POST", "/api/favorite-spots", input);
}

export function updateFavoriteSpot(id: string, input: FavoriteSpotInput): Promise<FavoriteSpotRecord> {
  return requestJson<FavoriteSpotRecord>("PUT", `/api/favorite-spots/${encodeURIComponent(id)}`, input);
}

export async function deleteFavoriteSpot(id: string): Promise<void> {
  await requestJson<{ deleted_id: string }>("DELETE", `/api/favorite-spots/${encodeURIComponent(id)}`);
}
