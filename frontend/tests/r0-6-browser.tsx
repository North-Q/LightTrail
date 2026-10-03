/** 在真实浏览器中回归 D2 请求体与 D4 用户确认写入。 */

import { createElement } from "react";
import { createRoot } from "react-dom/client";
import { SourceProvider } from "../src/context/SourceContext";
import { D2Page } from "../src/pages/D2Page";
import { D3Page } from "../src/pages/D3Page";
import { D4Page } from "../src/pages/D4Page";

const calls: Array<{ url: string; body: string }> = [];
let converted = 0;
const result = document.getElementById("r0-6-result")!;
const root = createRoot(document.getElementById("root")!);

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

async function waitFor(check: () => boolean): Promise<void> {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (check()) return;
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  throw new Error("页面状态等待超时");
}

function changeValue(element: HTMLInputElement | HTMLSelectElement, value: string): void {
  const prototype = element instanceof HTMLInputElement
    ? HTMLInputElement.prototype : HTMLSelectElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, "value")!.set!.call(element, value);
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
}

window.fetch = (async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
  const url = String(input);
  if (url === "/api/profile") {
    return Response.json({
      common_locations: [],
      favorite_spots: [],
    });
  }
  if (url === "/api/favorite-spots") {
    return Response.json({
      items: [
        { id: "spot-east", name: "东岸", latitude: 31.1, longitude: 121.4, coordinate_system: "WGS84", coordinate_state: "ready", tz_offset: "+08:00", direction_deg: 90, subject_types: ["日出"], source: "用户记录" },
        { id: "spot-west", name: "西岸", latitude: 30.9, longitude: 121.9, coordinate_system: "WGS84", coordinate_state: "ready", tz_offset: "+08:00", direction_deg: 270, subject_types: ["日落"], source: "现场勘察" },
        { id: "spot-unknown", name: "旧机位", latitude: 31.2, longitude: 121.5, coordinate_system: null, coordinate_state: "unknown_system", tz_offset: "+08:00", source: "旧档案" },
      ],
      skipped: [],
    });
  }
  calls.push({ url, body: typeof init?.body === "string" ? init.body : "" });
  if (url === "/api/spot-plans") {
    const request = JSON.parse(String(init?.body)) as Record<string, unknown>;
    return Response.json({
      status: "needs_data", request, selected_date: request.target_date,
      site: null, direction: null, arrival_window: null, setup_window: null,
      shoot_window: null, departure_window: null, focal_length_mm: null,
      gear: [], parameters: [],
      risks: [{ code: "weather_unverified", severity: "medium", detail: "云量尚未核验" }],
      evidence_refs: [],
    });
  }
  if (url === "/api/map/convert") {
    const ids = (JSON.parse(String(init?.body)) as { spot_ids: string[] }).spot_ids;
    assert(ids.every((id) => id !== "spot-unknown"), "未知坐标系机位被发送给转换接口");
    assert(!String(init?.body).includes("longitude"), "浏览器提交了精确坐标");
    converted += ids.length;
    return Response.json({ positions: ids.map((id) => id === "spot-east"
      ? { longitude: 121.4, latitude: 31.1 } : { longitude: 121.9, latitude: 30.9 }) });
  }
  if (url === "/api/map/config") return Response.json({ available: false, js_key: null });
  if (url === "/api/photos/review") {
    return new Response(
      'event: card\ndata: {"card":{"conclusion":"需要调整曝光","evidence":[],"params":[]}}\n\n'
        + 'event: done\ndata: {"session_id":"test"}\n\n',
      { headers: { "Content-Type": "text/event-stream" } },
    );
  }
  if (url === "/api/events") return Response.json({ event_id: "test-event" });
  throw new Error(`意外请求：${url}`);
}) as typeof fetch;

async function run(): Promise<void> {
  root.render(createElement(SourceProvider, null, createElement(D2Page)));
  await waitFor(() => Boolean(document.querySelector('select option[value="spot-west"]')));
  const date = document.querySelector<HTMLInputElement>('input[type="date"]')!;
  const selects = document.querySelectorAll<HTMLSelectElement>("select");
  changeValue(date, "2026-10-03");
  changeValue(selects[0], "火烧云");
  changeValue(selects[1], "spot-west");
  await waitFor(() => !document.querySelector<HTMLButtonElement>(".page-head button")!.disabled);
  document.querySelector<HTMLButtonElement>(".page-head button")!.click();
  await waitFor(() => calls.some((call) => call.url === "/api/spot-plans"));
  const request = JSON.parse(calls.find((call) => call.url === "/api/spot-plans")!.body) as Record<string, unknown>;
  assert(request.target_date === "2026-10-03" && request.subject_type === "火烧云", "D2 请求丢失日期或题材");
  assert((request.spot as { site_id: string; name: string }).site_id === "spot-west", "D2 请求未使用当前机位 ID");
  assert(request.tz_offset === "+08:00", "D2 请求未使用机位时区");
  await waitFor(() => document.body.textContent?.includes("云量尚未核验") ?? false);
  assert(!calls.some((call) => call.url === "/api/map/config"), "D2 未经点击读取地图配置");
  const markerClicks = new Map<string, () => void>();
  let centered = 0;
  window.AMap = {
    Map: class {
      destroy(): void { /* 浏览器检查中的离线地图替身。 */ }
      setCenter(): void { centered += 1; }
      setFitView(): void { /* 检查标记总数即可。 */ }
    },
    Marker: class {
      private title: string;
      constructor(options: { title: string }) {
        assert(options.title !== "旧机位", "未知坐标系被标在地图上");
        this.title = options.title;
      }
      on(_event: string, callback: () => void): void { markerClicks.set(this.title, callback); }
    },
  } as unknown as typeof window.AMap;
  document.querySelector<HTMLButtonElement>(".d2-map-consent button")!.click();
  await waitFor(() => document.querySelector<HTMLDivElement>(".d2-map-canvas")?.getAttribute("aria-label")?.includes("2 个标记") ?? false);
  assert(converted === 2, "地图转换了未知坐标系机位，或漏掉已确认机位");
  markerClicks.get("东岸")?.();
  await waitFor(() => document.querySelectorAll<HTMLSelectElement>(".d2-selection-fields select")[1]?.value === "spot-east");
  await waitFor(() => document.querySelector(".d2-map-details")?.textContent?.includes("用户记录") ?? false);
  assert(document.querySelector(".d2-map-details")?.textContent?.includes("朝向：90°"), "地图侧未显示机位朝向");
  assert(document.querySelector(".d2-map-details")?.textContent?.includes("日出"), "地图侧未显示机位题材");
  const centerBeforeListClick = centered;
  document.querySelectorAll<HTMLButtonElement>(".d2-spot")[1]!.click();
  await waitFor(() => document.querySelectorAll<HTMLSelectElement>(".d2-selection-fields select")[1]?.value === "spot-west");
  await waitFor(() => centered > centerBeforeListClick);
  document.querySelector<HTMLButtonElement>(".d2-map-head button")!.click();
  await waitFor(() => Boolean(document.querySelector(".d2-map-consent button")));
  window.AMap = undefined;
  document.querySelector<HTMLButtonElement>(".d2-map-consent button")!.click();
  await waitFor(() => document.body.textContent?.includes("地图服务尚未配置") ?? false);
  assert(document.querySelectorAll<HTMLButtonElement>(".d2-spot:not(:disabled)").length === 2, "地图失败后机位列表不可操作");
  assert(document.querySelector<HTMLButtonElement>(".d2-spot:disabled")?.textContent?.includes("坐标系待确认"), "未知坐标系缺少禁用原因");

  root.render(createElement(SourceProvider, null, createElement(D3Page)));
  await waitFor(() => Boolean(document.querySelector('input[aria-label="ISO"]')));
  const initialEv = [...document.querySelectorAll(".param-card")]
    .find((item) => item.textContent?.includes("EV 守恒"))!.textContent;
  changeValue(document.querySelector<HTMLInputElement>('input[aria-label="ISO"]')!, "200");
  await waitFor(() => document.body.textContent?.includes("1/500s") ?? false);
  const adjustedEv = [...document.querySelectorAll(".param-card")]
    .find((item) => item.textContent?.includes("EV 守恒"))!.textContent;
  assert(initialEv === adjustedEv, "D3 ISO 滑块破坏等效曝光");

  root.render(createElement(SourceProvider, null, createElement(D4Page)));
  await waitFor(() => Boolean(document.querySelector<HTMLInputElement>('#d4-uploads')));
  const fileInput = document.querySelector<HTMLInputElement>('#d4-uploads')!;
  const files = new DataTransfer();
  const onePixelPng = Uint8Array.from(
    atob("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="),
    (byte) => byte.charCodeAt(0),
  );
  files.items.add(new File([onePixelPng], "sample.png", { type: "image/png" }));
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "files")!.set!.call(fileInput, files.files);
  fileInput.dispatchEvent(new Event("change", { bubbles: true }));
  await waitFor(() => Boolean(document.querySelector('#event-outcome')));
  await waitFor(() => document.querySelector<HTMLImageElement>(".d4-preview-image")?.naturalWidth === 1);
  const preview = document.querySelector<HTMLImageElement>(".d4-preview-image")!;
  assert(preview.src.startsWith("blob:"), "D4 没有使用本地照片预览 URL");
  assert(preview.alt.includes("sample.png"), "D4 照片预览缺少文件名");
  assert(document.querySelectorAll('#event-location').length === 1, "D4 地点字段 id 重复");
  assert(document.querySelectorAll('#event-subject').length === 1, "D4 题材字段 id 重复");
  assert(document.querySelectorAll('#event-outcome').length === 1, "D4 结果字段 id 重复");
  assert(document.body.textContent!.includes("已分析 1 张"), "D4 上传计数未更新");
  const save = [...document.querySelectorAll<HTMLButtonElement>("button")]
    .find((button) => button.textContent?.includes("确认写入事件记忆"))!;
  assert(save.disabled, "D4 未填写地点和结果时允许写入");
  changeValue(document.querySelector<HTMLInputElement>('#event-location')!, "西岸");
  assert(save.disabled, "D4 未选择结果时允许写入");
  assert(!calls.some((call) => call.url === "/api/events"), "D4 上传时自动写入事件");
  changeValue(document.querySelector<HTMLSelectElement>('#event-outcome')!, "fail");
  await waitFor(() => !save.disabled);
  save.click();
  await waitFor(() => calls.some((call) => call.url === "/api/events"));
  const eventCalls = calls.filter((call) => call.url === "/api/events");
  assert(JSON.parse(eventCalls[0].body).outcome === "fail", "D4 未提交用户选择的结果");
  await waitFor(() => save.disabled);
  save.click();
  assert(calls.filter((call) => call.url === "/api/events").length === 1, "D4 重复写入事件");
  result.textContent = "PASS: D2 当前选择、D3 曝光守恒与 D4 写入门禁";
}

void run().catch((error: unknown) => {
  result.textContent = `FAIL: ${error instanceof Error ? error.message : String(error)}`;
});
