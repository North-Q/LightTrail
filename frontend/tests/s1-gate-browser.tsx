/** 合成机位全链路：M1 新建、D2 地图/计划、URL 恢复及冻结文件导入。 */

import { createElement } from "react";
import { createRoot } from "react-dom/client";
import App from "../src/App";

interface Spot {
  id: string;
  name: string;
  latitude: number;
  longitude: number;
  coordinate_system: "WGS84";
  coordinate_state: "ready";
  tz_offset: string;
  direction_deg: number | null;
  subject_types: string[];
  notes: string;
  source: string;
}

const result = document.getElementById("s1-gate-result")!;
const root = createRoot(document.getElementById("root")!);
const spots: Spot[] = [];
const markerClicks = new Map<string, () => void>();
let archive: Record<string, unknown> | null = null;
let planCalls = 0;
let stage = "M1 新建";
const action = {
  direction: { bearing_deg: 270, reason: "日落方位" },
  arrival_window: { start: "2026-10-03T16:00:00+08:00", end: "2026-10-03T16:15:00+08:00" },
  setup_window: { start: "2026-10-03T16:15:00+08:00", end: "2026-10-03T16:30:00+08:00" },
  shoot_window: { start: "2026-10-03T16:30:00+08:00", end: "2026-10-03T18:30:00+08:00" },
  departure_window: { start: "2026-10-03T18:30:00+08:00", end: "2026-10-03T18:45:00+08:00" },
  focal_length_mm: 35, gear: ["相机", "三脚架"],
  parameters: [{ name: "光圈", value: "f/8", reason: "风光景深" }],
  risks: [{ code: "weather_unverified", severity: "medium", detail: "出发前核验天气" }],
  evidence_refs: [{ source: "sun_times", field: "sunset", observed_at: null, valid_until: null }],
};

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(message);
}

async function waitFor(check: () => boolean): Promise<void> {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    if (check()) return;
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  throw new Error(`${stage}：页面状态等待超时`);
}

function changeValue(element: HTMLInputElement | HTMLSelectElement, value: string): void {
  const prototype = element instanceof HTMLInputElement
    ? HTMLInputElement.prototype : HTMLSelectElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, "value")!.set!.call(element, value);
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
}

const anchorClick = HTMLAnchorElement.prototype.click;
HTMLAnchorElement.prototype.click = function click(): void {
  if (!this.download) anchorClick.call(this);
};

window.fetch = (async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
  const url = String(input);
  const method = init?.method ?? "GET";
  if (url === "/api/profile") return Response.json({
    camera_body: "", lenses: [], preferences: [], common_locations: [],
    skill_level: "", favorite_spots: [],
  });
  if (url === "/api/favorite-spots" && method === "GET") {
    return Response.json({ items: spots, skipped: [] });
  }
  if (url === "/api/favorite-spots" && method === "POST") {
    const body = JSON.parse(String(init?.body)) as Omit<Spot, "id" | "coordinate_state">;
    spots.push({ ...body, id: "created-spot", coordinate_state: "ready" });
    return Response.json(spots[0], { status: 201 });
  }
  if (url === "/api/map/convert") {
    const body = JSON.parse(String(init?.body)) as { spot_ids: string[] };
    assert(body.spot_ids.join() === "created-spot", "地图转换未按新建机位 ID 请求");
    assert(!String(init?.body).includes("latitude"), "地图请求体包含精确坐标");
    return Response.json({ positions: [{ longitude: 121.99, latitude: 31.12 }] });
  }
  if (url === "/api/spot-plans") {
    planCalls += 1;
    const request = JSON.parse(String(init?.body)) as Record<string, unknown>;
    assert((request.spot as { site_id: string }).site_id === "created-spot", "计划使用了错误机位");
    return Response.json({
      status: "ready", request, selected_date: request.target_date,
      site: { name: "东滩", site_id: "created-spot", point: { latitude: 31.123456, longitude: 121.987654, coordinate_system: "WGS84" }, source: "用户实测" },
      ...action,
    });
  }
  if (url === "/api/spot-plans/archive") {
    const plan = JSON.parse(String(init?.body)) as { request: Record<string, unknown> };
    archive = {
      schema_version: 1, format: "lighttrail.spot-plan", privacy: "coordinates_removed",
      request: plan.request,
      plan: {
        status: "ready", selected_date: plan.request.target_date,
        site: { name: "东滩", site_id: "created-spot", coordinate_system: "WGS84", source: "用户实测" },
        ...action,
      },
    };
    return Response.json(archive);
  }
  if (url === "/api/spot-plans/replay") return Response.json(archive);
  throw new Error(`全链路出现意外请求：${method} ${url}`);
}) as typeof fetch;

async function run(): Promise<void> {
  sessionStorage.clear();
  window.location.hash = "#/m1";
  root.render(createElement(App));
  await waitFor(() => Boolean([...document.querySelectorAll<HTMLButtonElement>("button")]
    .find((button) => button.textContent?.trim() === "新增机位")));
  [...document.querySelectorAll<HTMLButtonElement>("button")]
    .find((button) => button.textContent?.trim() === "新增机位")!.click();
  await waitFor(() => Boolean(document.querySelector(".m1-spot-form")));
  const form = document.querySelector<HTMLFormElement>(".m1-spot-form")!;
  const required = form.querySelectorAll<HTMLInputElement>('input[required]');
  const numbers = form.querySelectorAll<HTMLInputElement>('input[type="number"]');
  changeValue(required[0], "东滩");
  changeValue(numbers[0], "31.123456");
  changeValue(numbers[1], "121.987654");
  changeValue(form.querySelector<HTMLSelectElement>("select")!, "WGS84");
  changeValue(required[1], "用户实测");
  form.querySelector<HTMLButtonElement>('button[type="submit"]')!.click();
  await waitFor(() => spots.length === 1 && Boolean(document.querySelector(".m1-spot-row")));
  const row = [...document.querySelectorAll(".m1-spot-row")].find((item) => item.textContent?.includes("东滩"))!;
  [...row.querySelectorAll<HTMLButtonElement>("button")]
    .find((button) => button.textContent?.trim() === "设为本次")!.click();
  assert(sessionStorage.getItem("lt.active_spot_id") === "created-spot", "M1 未交接机位 ID");

  stage = "D2 地图与计划";
  window.location.hash = "#/d2";
  await waitFor(() => document.querySelectorAll<HTMLSelectElement>(".d2-selection-fields select")[1]?.value === "created-spot");
  window.AMap = {
    Map: class { destroy(): void {} setCenter(): void {} setFitView(): void {} },
    Marker: class {
      on(_event: string, callback: () => void): void { markerClicks.set("东滩", callback); }
    },
  } as unknown as typeof window.AMap;
  document.querySelector<HTMLButtonElement>(".d2-map-consent button")!.click();
  await waitFor(() => document.querySelector(".d2-map-canvas")?.getAttribute("aria-label")?.includes("1 个标记") ?? false);
  changeValue(document.querySelector<HTMLInputElement>('input[type="date"]')!, "2026-10-03");
  markerClicks.get("东滩")?.();
  await waitFor(() => window.location.hash.includes("date=2026-10-03"));
  assert(document.querySelector<HTMLButtonElement>(".d2-spot.is-selected")?.textContent?.includes("东滩"), "地图和列表未定位同一机位");
  document.querySelector<HTMLButtonElement>(".page-head button")!.click();
  await waitFor(() => planCalls === 1 && Boolean(document.querySelector(".d2-plan")));
  assert(document.querySelector(".d2-plan")?.textContent?.includes("18:30"), "完整行动时间线未呈现");
  const exportButton = [...document.querySelectorAll<HTMLButtonElement>(".d2-plan-actions button")]
    .find((button) => button.textContent?.includes("导出"))!;
  exportButton.click();
  await waitFor(() => archive !== null);
  const savedHash = window.location.hash;

  stage = "刷新恢复";
  window.location.hash = "#/home";
  await waitFor(() => document.body.textContent?.includes("今日拍摄旅程") ?? false);
  window.location.hash = savedHash;
  await waitFor(() => document.querySelectorAll<HTMLSelectElement>(".d2-selection-fields select")[1]?.value === "created-spot");
  assert(document.querySelector<HTMLInputElement>('input[type="date"]')?.value === "2026-10-03", "刷新后日期未恢复");
  assert(planCalls === 1, "刷新分享链接时自动重新规划");

  stage = "导入回放";
  const fileInput = document.querySelector<HTMLInputElement>('input[aria-label="选择计划 JSON 文件"]')!;
  const files = new DataTransfer();
  files.items.add(new File([JSON.stringify(archive)], "plan.json", { type: "application/json" }));
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "files")!.set!.call(fileInput, files.files);
  fileInput.dispatchEvent(new Event("change", { bubbles: true }));
  await waitFor(() => document.body.textContent?.includes("冻结回放") ?? false);
  assert(planCalls === 1, "导入回放触发了重新规划");
  result.textContent = "PASS: M1 新建、地图/列表同点、计划、URL 恢复与冻结回放";
}

void run().catch((error: unknown) => {
  result.textContent = `FAIL: ${error instanceof Error ? error.message : String(error)}`;
});
