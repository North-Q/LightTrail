/** U2-1 真实组件延迟替身：主动停止、离开、迟到响应与新请求隔离。 */
import { createElement } from "react";
import { createRoot } from "react-dom/client";
import "../src/styles.css";
import { SourceProvider } from "../src/context/SourceContext";
import { D1Page } from "../src/pages/D1Page";
import { D2Page } from "../src/pages/D2Page";
import { D3Page } from "../src/pages/D3Page";
import { D4Page } from "../src/pages/D4Page";
import type { DecisionCard } from "../src/api/events";

const originalGet = Storage.prototype.getItem;
const originalSet = Storage.prototype.setItem;
const originalRemove = Storage.prototype.removeItem;
Storage.prototype.getItem = function (key: string): string | null { return originalGet.call(this, `u2-demo.${key}`); };
Storage.prototype.setItem = function (key: string, value: string): void { originalSet.call(this, `u2-demo.${key}`, value); };
Storage.prototype.removeItem = function (key: string): void { originalRemove.call(this, `u2-demo.${key}`); };
const root = createRoot(document.getElementById("root")!);
const result = document.getElementById("u2-result")!;
const requests: { signal?: AbortSignal | null; resolve: (response: Response) => void; url: string }[] = [];
let fixture: DecisionCard;
window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
  if (String(input) === "/api/sessions") return Promise.resolve(Response.json([]));
  if (String(input) === "/api/data-boundaries") return Promise.resolve(Response.json({ llm_receiver: "offline-model.invalid" }));
  if (String(input) === "/api/data-boundaries") return Promise.resolve(Response.json({ llm_receiver: "offline-model.invalid" }));
  if (String(input) === "/api/favorite-spots") return Promise.resolve(Response.json({ items: [{
    id: "u2-synthetic", name: "合成机位", latitude: 0, longitude: 0, coordinate_system: "WGS84", coordinate_state: "ready", tz_offset: "+08:00", source: "离线合成",
  }], skipped: [] }));
  return new Promise<Response>((resolve) => {
  // 故意忽略 abort，模拟底层已发送、迟到的响应。
  requests.push({ signal: init?.signal, resolve, url: String(input) });
  });
}) as typeof fetch;

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}
async function waitFor(check: () => boolean): Promise<void> {
  for (let i = 0; i < 100; i++) {
    if (check()) return;
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  throw new Error("页面等待超时");
}
function mount(component: typeof D1Page | typeof D2Page | typeof D3Page | typeof D4Page): void {
  root.render(createElement(SourceProvider, null, createElement(component)));
}
function button(text: string): HTMLButtonElement {
  const found = [...document.querySelectorAll<HTMLButtonElement>("button")].find((item) => item.textContent?.trim() === text);
  assert(found, `缺少按钮：${text}`);
  return found;
}
async function submitD3(): Promise<void> {
  await waitFor(() => Boolean(document.querySelector('[aria-label="决策提问"]')));
  const input = document.querySelector<HTMLInputElement>('[aria-label="决策提问"]')!;
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "今晚火烧云是否值得去");
  input.dispatchEvent(new Event("input", { bubbles: true }));
  await new Promise((resolve) => setTimeout(resolve, 40));
  input.closest("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
}
function complete(index: number, label: string): void {
  requests[index].resolve(new Response(`event: card\ndata: ${JSON.stringify({ type: "card", card: { ...fixture, conclusion: label } })}\n\nevent: done\ndata: {"type":"done"}\n\n`));
}

async function changeValue(selector: string, value: string): Promise<void> {
  const input = document.querySelector<HTMLInputElement | HTMLSelectElement>(selector)!;
  assert(input, `缺少输入框：${selector}`);
  const prototype = input instanceof HTMLInputElement ? HTMLInputElement.prototype : HTMLSelectElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, "value")!.set!.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
  await new Promise((resolve) => setTimeout(resolve, 40));
}

function upload(selector: string, count = 1): void {
  const files = new DataTransfer();
  for (let i = 0; i < count; i++) files.items.add(new File(["synthetic"], `合成照片-${i}.jpg`, { type: "image/jpeg" }));
  const input = document.querySelector<HTMLInputElement>(selector)!;
  input.files = files.files;
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

async function run(): Promise<void> {
  // 样本由静态导入加载，fetch 全部替换后也不会误发 API。
  fixture = (await import("./fixtures/u1-cards.json")).default.ready as DecisionCard;
  localStorage.removeItem("lt.last_card");
  if (new URLSearchParams(location.search).has("manual")) {
    for (const [label, action] of [
      ["灵感页", () => mount(D1Page)], ["规划页", () => mount(D2Page)], ["决策页", () => mount(D3Page)], ["复盘页", () => mount(D4Page)],
      ["离开当前页", () => root.render(createElement("p", null, "已离开；旧请求不得保存结果。"))],
      ["返回最近请求的迟到结果", () => { if (requests.length) complete(requests.length - 1, "离线迟到结果"); }],
    ] as const) {
      const control = document.createElement("button");
      control.textContent = label;
      control.onclick = action;
      document.getElementById("controls")!.append(control);
    }
    result.textContent = "手动离线替身：发起请求后停止等待或离开，再返回迟到结果。底层替身故意忽略取消。";
    mount(D3Page);
    return;
  }
  mount(D3Page);
  await submitD3();
  await waitFor(() => requests.length === 1);
  button("停止等待").click();
  await waitFor(() => Boolean(requests[0].signal?.aborted));
  assert(document.body.textContent?.includes("不能确认"), "停止等待应说明底层取消未确认");
  await submitD3();
  await waitFor(() => requests.length === 2);
  complete(0, "旧请求不得回写");
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert(!localStorage.getItem("lt.last_card"), "停止的旧请求写入了缓存");
  assert(button("决策中…").disabled, "旧 finally 清除了新请求的等待状态");
  complete(1, "当前请求完成");
  await waitFor(() => Boolean(localStorage.getItem("lt.last_card")?.includes("当前请求完成")));
  localStorage.removeItem("lt.last_card");
  await submitD3();
  await waitFor(() => requests.length === 3);
  mount(D1Page);
  await waitFor(() => Boolean(document.querySelector('[aria-label="输入内容"]')));
  assert(requests[2].signal?.aborted, "离开 D3 没有清理浏览器请求");
  complete(2, "离开页面后的旧结果");
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert(!localStorage.getItem("lt.last_card"), "离开后的请求仍写入全局历史缓存");

  let index = requests.length;
  await changeValue('[aria-label="输入内容"]', "合成对话");
  button("发送").click();
  await waitFor(() => requests.length === index + 1);
  button("停止等待").click();
  assert(requests[index].signal?.aborted, "D1 对话停止没有 abort");
  complete(index, "D1 旧对话");
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert(!localStorage.getItem("lt.last_card"), "D1 停止后仍写缓存");
  index = requests.length;
  upload("#ref-upload");
  await waitFor(() => requests.length === index + 1);
  mount(D4Page);
  await waitFor(() => Boolean(document.querySelector("#d4-uploads")));
  assert(requests[index].signal?.aborted, "D1 反推离开没有清理");
  complete(index, "D1 旧反推");
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert(!localStorage.getItem("lt.last_card"), "D1 旧反推仍保存结果");

  index = requests.length;
  upload("#d4-uploads", 2);
  await waitFor(() => requests.length === index + 1);
  button("停止等待").click();
  assert(requests[index].signal?.aborted, "D4 批量停止没有 abort");
  complete(index, "D4 旧复盘");
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert(requests.length === index + 1, "D4 停止后仍提交后续照片");
  assert(!document.body.textContent?.includes("D4 旧复盘"), "D4 停止后仍展示旧卡");
  index = requests.length;
  upload("#d4-uploads");
  await waitFor(() => requests.length === index + 1);
  mount(D3Page);
  await waitFor(() => Boolean(document.querySelector('[aria-label="决策提问"]')));
  assert(requests[index].signal?.aborted, "D4 离开没有清理");
  complete(index, "D4 离开后旧复盘");
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert(!document.body.textContent?.includes("D4 离开后旧复盘"), "旧复盘回写当前决策页");

  mount(D2Page);
  await waitFor(() => Boolean(document.querySelector('option[value="u2-synthetic"]')));
  await changeValue('input[type="date"]', "2026-10-06");
  await changeValue(".d2-selection-fields label:last-child select", "u2-synthetic");
  index = requests.length;
  button("运行规划").click();
  await waitFor(() => requests.length === index + 1);
  button("停止等待").click();
  assert(requests[index].signal?.aborted, "D2 规划停止没有 abort");
  requests[index].resolve(Response.json({ status: "ready" }));
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert(document.body.textContent?.includes("不能确认"), "D2 旧请求覆盖停止提示");
  assert(!document.body.textContent?.includes("结构化计划已生成"), "D2 旧规划仍回写");
  index = requests.length;
  button("运行规划").click();
  await waitFor(() => requests.length === index + 1);
  mount(D1Page);
  await waitFor(() => Boolean(document.querySelector('[aria-label="输入内容"]')));
  assert(requests[index].signal?.aborted, "D2 离开没有清理");
  requests[index].resolve(Response.json({ status: "ready" }));
  result.textContent = "PASS: D1–D4 停止/卸载、D3 迟到与新请求隔离、D4 批量停止、D2 旧计划拦截";
}
void run().catch((error: unknown) => { result.textContent = `FAIL: ${error instanceof Error ? error.message : String(error)}`; });
