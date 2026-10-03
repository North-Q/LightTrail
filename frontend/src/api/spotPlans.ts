/** 结构化单机位计划 API：输入输出均取自 OpenAPI 生成契约。 */

import type { components } from "./generated";

export type PlanningRequest = components["schemas"]["PlanningRequest"];
export type SpotPlan = components["schemas"]["SpotPlan"];
export type PlanArchive = components["schemas"]["PlanArchive"];

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

export async function createSpotPlan(input: PlanningRequest): Promise<SpotPlan> {
  const response = await fetch(BASE + "/api/spot-plans", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`计划生成失败（${response.status}）`);
  }
  return (await response.json()) as SpotPlan;
}

async function postPlanJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`计划文件校验失败（${response.status}）`);
  return (await response.json()) as T;
}

export function exportPlanArchive(plan: SpotPlan): Promise<PlanArchive> {
  return postPlanJson<PlanArchive>("/api/spot-plans/archive", plan);
}

export function replayPlanArchive(payload: unknown): Promise<PlanArchive> {
  return postPlanJson<PlanArchive>("/api/spot-plans/replay", payload);
}
