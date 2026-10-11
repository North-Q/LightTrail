import { saveFixturePlan, readFixturePlan } from "./saved-plan-fixture";
/** 浏览器内检查分享 URL、脱敏文件与冻结回放，不连接任何外部服务。 */

import { createElement } from "react";
import { createRoot } from "react-dom/client";
import App from "../src/App";
import { buildPlanHash } from "../src/pages/planUrl";

const result = document.getElementById("s1-5-result")!;
const root = createRoot(document.getElementById("root")!);
const calls: string[] = [];
let downloaded: Blob | null = null;
let stage = "初始化";
const createObjectURL = URL.createObjectURL.bind(URL);
URL.createObjectURL = (object: Blob | MediaSource): string => {
  if (object instanceof Blob) downloaded = object;
  return createObjectURL(object);
};
const anchorClick = HTMLAnchorElement.prototype.click;
HTMLAnchorElement.prototype.click = function click(): void {
  if (!this.download) anchorClick.call(this);
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

const request = {
  subject_type: "日落", target_date: "2026-10-03", tz_offset: "+08:00",
  spot: { name: "东岸", site_id: "spot-east" },
};
const livePlan = {
  status: "needs_data", request, selected_date: "2026-10-03",
  site: { name: "东岸", site_id: "spot-east", point: { longitude: 121.4, latitude: 31.1, coordinate_system: "WGS84" }, source: "用户记录" },
  direction: null, arrival_window: null, setup_window: null, shoot_window: null,
  departure_window: null, focal_length_mm: null, gear: [], parameters: [],
  risks: [{ code: "weather_unverified", severity: "medium", detail: "天气待核验" }],
  evidence_refs: [],
};
const archive = {
  schema_version: 1, format: "lighttrail.spot-plan", privacy: "coordinates_removed",
  request,
  plan: {
    status: "needs_data", selected_date: "2026-10-03",
    site: { name: "东岸", site_id: "spot-east", coordinate_system: "WGS84", source: "用户记录" },
    direction: null, arrival_window: null, setup_window: null, shoot_window: null,
    departure_window: null, focal_length_mm: null, gear: [], parameters: [],
    risks: [{ code: "weather_unverified", severity: "medium", detail: "天气待核验" }],
    evidence_refs: [],
  },
};

window.fetch = (async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
  const url = String(input);
  const savedResponse = readFixturePlan(url);
  if (savedResponse) return savedResponse;
  calls.push(url);
  if (url === "/api/favorite-spots") return Response.json({
    items: [
      { id: "spot-east", name: "东岸", latitude: 31.1, longitude: 121.4, coordinate_system: "WGS84", coordinate_state: "ready", tz_offset: "+08:00", source: "用户记录" },
      { id: "spot-west", name: "西岸", latitude: 30.9, longitude: 121.9, coordinate_system: "WGS84", coordinate_state: "ready", tz_offset: "+08:00", source: "用户记录" },
    ], skipped: [],
  });
  if (url === "/api/saved-spot-plans") {
    const sent = JSON.parse(String(init?.body)).request as typeof request;
    assert(sent.spot.site_id === "spot-east", "恢复后使用了错误机位");
    return saveFixturePlan(livePlan);
  }
  if (url === "/api/spot-plans/archive") return Response.json(archive);
  if (url === "/api/spot-plans/replay") return Response.json(archive);
  throw new Error(`回放期间出现意外请求：${url}`);
}) as typeof fetch;

async function run(): Promise<void> {
  window.location.hash = buildPlanHash({ subject: "日落", date: "2026-10-03", spotId: "spot-east" });
  root.render(createElement(App));
  stage = "URL 恢复";
  await waitFor(() => document.querySelectorAll<HTMLSelectElement>(".d2-selection-fields select")[1]?.value === "spot-east");
  assert(document.querySelector<HTMLInputElement>('input[type="date"]')?.value === "2026-10-03", "URL 日期未恢复");
  assert(!calls.includes("/api/saved-spot-plans"), "仅打开分享链接就重新计算了计划");
  document.querySelector<HTMLButtonElement>(".page-head button")!.click();
  stage = "计划生成";
  await waitFor(() => Boolean(document.querySelector(".d2-plan")));
  const exportButton = [...document.querySelectorAll<HTMLButtonElement>(".d2-plan-actions button")]
    .find((button) => button.textContent?.includes("导出"))!;
  exportButton.click();
  stage = "脱敏导出";
  await waitFor(() => downloaded !== null);
  const exported = await downloaded!.text();
  assert(!exported.includes("latitude") && !exported.includes("longitude"), "导出文件含结构化坐标");
  assert(!exported.includes("121.4") && !exported.includes("31.1"), "导出文件含精确坐标");

  window.location.hash = buildPlanHash({ subject: "日落", date: "2026-10-03", spotId: "foreign-spot" });
  stage = "跨用户 ID 拒绝";
  await waitFor(() => document.body.textContent?.includes("不属于当前用户") ?? false);
  assert(document.querySelectorAll<HTMLSelectElement>(".d2-selection-fields select")[1]?.value === "", "跨用户机位被恢复为当前选择");

  const fileInput = document.querySelector<HTMLInputElement>('input[aria-label="选择计划 JSON 文件"]')!;
  const files = new DataTransfer();
  files.items.add(new File([JSON.stringify(archive)], "plan.json", { type: "application/json" }));
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "files")!.set!.call(fileInput, files.files);
  const plansBeforeReplay = calls.filter((url) => url === "/api/saved-spot-plans").length;
  fileInput.dispatchEvent(new Event("change", { bubbles: true }));
  stage = "冻结回放";
  await waitFor(() => document.body.textContent?.includes("冻结回放") ?? false);
  assert(calls.filter((url) => url === "/api/saved-spot-plans").length === plansBeforeReplay, "冻结回放重新调用了规划接口");
  assert(!calls.includes("/api/map/config") && !calls.includes("/api/map/convert"), "冻结回放触发地图服务");
  result.textContent = "PASS: URL 恢复、跨用户 ID 拒绝、脱敏导出与冻结回放";
}

void run().catch((error: unknown) => {
  result.textContent = `FAIL: ${error instanceof Error ? error.message : String(error)}`;
});
