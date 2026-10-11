/** 结构化单机位计划 API：输入输出均取自 OpenAPI 生成契约。 */

import type { components } from "./generated";
import type { ResultPointer, SavedResult } from "./savedResults";

export type PlanningRequest = components["schemas"]["PlanningRequest"];
export type SpotPlan = components["schemas"]["SpotPlan"];
export type PlanArchive = components["schemas"]["PlanArchive"];

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

export async function createSpotPlan(input: PlanningRequest, signal?: AbortSignal, pointer?: ResultPointer, onSaved?: (result: SavedResult) => void): Promise<SpotPlan> {
  const response = await fetch(BASE + (onSaved ? "/api/saved-spot-plans" : "/api/spot-plans"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(onSaved ? { request: input, ...pointer } : input),
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    throw new Error(`计划生成或保存失败（${response.status}），未确认已保存`);
  }
  const result = await response.json() as SpotPlan | SavedResult;
  if (onSaved) {
    const saved = result as SavedResult;
    if (!saved.plan) throw new Error("保存响应缺少实际计划");
    onSaved(saved);
    return saved.plan;
  }
  return result as SpotPlan;
}

async function postPlanJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
    signal,
  });
  if (!response.ok) throw new Error(`计划文件校验失败（${response.status}）`);
  return (await response.json()) as T;
}

export function exportPlanArchive(plan: SpotPlan, signal?: AbortSignal): Promise<PlanArchive> {
  return postPlanJson<PlanArchive>("/api/spot-plans/archive", plan, signal);
}

export function replayPlanArchive(payload: unknown, signal?: AbortSignal): Promise<PlanArchive> {
  return postPlanJson<PlanArchive>("/api/spot-plans/replay", payload, signal);
}
