/** 真实组件离线验收：档案、收藏、失败、补齐重查与旧卡刷新。 */
import { createElement } from "react";
import { createRoot } from "react-dom/client";
import "../src/styles.css";
import { SourceProvider } from "../src/context/SourceContext";
import { M1Page } from "../src/pages/M1Page";
import { D3Page } from "../src/pages/D3Page";
import { HomePage } from "../src/pages/HomePage";
import type { DecisionCard } from "../src/api/events";
import { decisionState } from "../src/pages/decisionState";

const fixedNow = Date.parse("2026-10-06T08:00:00+00:00");
// 演示与检查均隔离浏览器存储，不读写用户的页面历史。
const originalGet = Storage.prototype.getItem;
const originalSet = Storage.prototype.setItem;
const originalRemove = Storage.prototype.removeItem;
Storage.prototype.getItem = function (key: string): string | null { return originalGet.call(this, `u1-demo.${key}`); };
Storage.prototype.setItem = function (key: string, value: string): void { originalSet.call(this, `u1-demo.${key}`, value); };
Storage.prototype.removeItem = function (key: string): void { originalRemove.call(this, `u1-demo.${key}`); };
let now = fixedNow;
Date.now = () => now;
const root = createRoot(document.getElementById("root")!);
const result = document.getElementById("u1-result")!;
const calls: string[] = [];
let complete = false;
let profile = { camera_body: "", lenses: [], preferences: [], common_locations: [], skill_level: "", favorite_spots: [] };
let spots: Record<string, unknown>[] = [];
let failed: DecisionCard;
let ready: DecisionCard;

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}
async function waitFor(check: () => boolean): Promise<void> {
  for (let i = 0; i < 150; i++) {
    if (check()) return;
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  throw new Error("页面状态等待超时");
}
function changeValue(element: HTMLInputElement | HTMLSelectElement, value: string): void {
  const prototype = element instanceof HTMLInputElement ? HTMLInputElement.prototype : HTMLSelectElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, "value")!.set!.call(element, value);
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
}
function button(text: string): HTMLButtonElement {
  const found = [...document.querySelectorAll<HTMLButtonElement>("button")].find((item) => item.textContent?.trim() === text);
  assert(found, `缺少按钮：${text}`);
  return found;
}
function mount(component: typeof M1Page | typeof D3Page | typeof HomePage): void {
  root.render(createElement(SourceProvider, null, createElement(component)));
}
const nativeFetch = window.fetch.bind(window);
window.fetch = (async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
  const url = String(input);
  if (url === "/api/data-boundaries") return Response.json({ llm_receiver: "offline-model.invalid" });
  calls.push(url);
  if (url === "/api/profile") {
    if (init?.method === "PUT") profile = JSON.parse(String(init.body));
    return Response.json(profile);
  }
  if (url === "/api/favorite-spots") {
    if (init?.method === "POST") {
      const spot = { ...JSON.parse(String(init.body)), id: "synthetic-west", coordinate_state: "ready" };
      spots = [spot];
      return Response.json(spot);
    }
    return Response.json({ items: spots, skipped: [] });
  }
  if (url === "/api/decide") {
    assert(spots.length === 1 && profile.camera_body === "合成相机", "决策前未完成档案与收藏");
    const card = complete ? ready : failed;
    return new Response(`event: card\ndata: ${JSON.stringify({ type: "card", card, session_id: "synthetic" })}\n\nevent: done\ndata: {"type":"done","session_id":"synthetic"}\n\n`, { headers: { "Content-Type": "text/event-stream" } });
  }
  throw new Error(`意外请求：${url}`);
}) as typeof fetch;

async function run(): Promise<void> {
  // 卡片来自生产装配 API 的固定离线样本，验证与浏览器展示共用事实。
  const fixture = await nativeFetch("/tests/fixtures/u1-cards.json").then((response) => response.json());
  failed = fixture.failed;
  ready = fixture.ready;
  for (const key of ["lt.last_card", "lt.sessions", "lt.active_spot_id"]) {
    localStorage.removeItem(key);
    sessionStorage.removeItem(key);
  }
  if (new URLSearchParams(location.search).has("manual")) {
    const toolbar = document.createElement("div");
    for (const [label, action] of [
      ["1. 档案与收藏", () => mount(M1Page)],
      ["2. 今晚询问", () => mount(D3Page)],
      ["天气失败", () => { complete = false; now = fixedNow; result.textContent = "替身：所有数据失败。请在决策页重新查询。"; }],
      ["补齐数据", () => { complete = true; now = fixedNow; result.textContent = "替身：数据完整。请在决策页重新查询。"; }],
      ["冻结 7 小时后查看", () => { now = fixedNow + 7 * 3600_000; mount(HomePage); }],
    ] as const) {
      const control = document.createElement("button");
      control.textContent = label;
      control.className = "btn btn-secondary";
      control.onclick = action;
      toolbar.append(control);
    }
    result.after(toolbar);
    result.textContent = "手动离线验收：先保存档案，新增名称为「合成西岸」、经纬度 0、WGS84、+08:00 的收藏，再询问今晚火烧云。";
    mount(M1Page);
    return;
  }
  mount(M1Page);
  await waitFor(() => Boolean(document.querySelector("#camera")));
  changeValue(document.querySelector<HTMLInputElement>("#camera")!, "合成相机");
  await new Promise((resolve) => setTimeout(resolve, 40));
  button("保存档案").click();
  await waitFor(() => profile.camera_body === "合成相机");
  button("新增机位").click();
  await waitFor(() => Boolean(document.querySelector(".m1-spot-form")));
  const form = document.querySelector<HTMLFormElement>(".m1-spot-form")!;
  const required = form.querySelectorAll<HTMLInputElement>("input[required]");
  const numbers = form.querySelectorAll<HTMLInputElement>('input[type="number"]');
  changeValue(required[0], "合成西岸");
  changeValue(required[1], "离线合成");
  changeValue(numbers[0], "0");
  changeValue(numbers[1], "0");
  changeValue(form.querySelector<HTMLSelectElement>("select")!, "WGS84");
  await new Promise((resolve) => setTimeout(resolve, 40));
  button("保存机位").click();
  await waitFor(() => spots.length === 1 && Boolean(document.querySelector(".m1-spot-row")));
  mount(D3Page);
  await waitFor(() => Boolean(document.querySelector('[aria-label="决策提问"]')));
  changeValue(document.querySelector<HTMLInputElement>('[aria-label="决策提问"]')!, "今晚火烧云是否值得去");
  await new Promise((resolve) => setTimeout(resolve, 40));
  button("决策").click();
  await waitFor(() => document.body.textContent?.includes("数据不足的原因") ?? false);
  assert(document.body.textContent?.includes("目标日期：2026-10-06"), "未展示目标日期");
  assert(!document.querySelector(".state-btn.go.is-active"), "失败卡显示出发");
  assert(document.querySelector<HTMLButtonElement>(".state-btn.go")!.disabled, "数据不足可手工伪装出发");
  assert(!document.querySelector(".interval-scale, .confidence-display"), "仍展示伪统计区间或概率");
  complete = true;
  button("决策").click();
  await waitFor(() => Boolean(document.querySelector(".state-btn.go.is-active")));
  assert(document.body.textContent?.includes("数据获取时间：2026-10-06T07:00:00+00:00"), "未展示实际获取时间");
  now += 7 * 3600_000;
  assert(decisionState(ready) === "stale", "冻结结果被误当新预报");
  // 修改存储时间、重挂首页均不得延长数据有效期。
  localStorage.setItem("lt.last_card", JSON.stringify({ card: ready, ts: now }));
  mount(HomePage);
  await waitFor(() => document.body.textContent?.includes("最近决策记录") ?? false);
  await waitFor(() => document.body.textContent?.includes("历史结果") ?? false);
  assert(!document.body.textContent?.includes("今日决策速览"), "旧卡仍冒充今日结果");
  mount(D3Page);
  await waitFor(() => Boolean(document.querySelector('[aria-label="决策提问"]')));
  mount(HomePage);
  await waitFor(() => document.body.textContent?.includes("历史结果") ?? false);
  assert(calls.every((url) => url.startsWith("/api/")), "出现外部请求");
  result.textContent = "PASS: U1 档案、合成收藏、失败降级、补齐重查、旧卡冻结及刷新检查";
}
void run().catch((error) => { result.textContent = `FAIL: ${error instanceof Error ? error.message : String(error)}`; });
