/** U3 真实页面组件经离线生产 API 验证；无手写决策卡，无 fetch 响应替换。 */
import { createElement } from "react";
import { createRoot } from "react-dom/client";
import { SourceProvider } from "../src/context/SourceContext";
import { D1Page } from "../src/pages/D1Page";
import { D2Page } from "../src/pages/D2Page";
import { D3Page } from "../src/pages/D3Page";
import { HomePage } from "../src/pages/HomePage";
import "../src/styles.css";

const get = Storage.prototype.getItem;
const set = Storage.prototype.setItem;
const remove = Storage.prototype.removeItem;
Storage.prototype.getItem = function (key: string) { return get.call(this, `u3-demo.${key}`); };
Storage.prototype.setItem = function (key: string, value: string) { set.call(this, `u3-demo.${key}`, value); };
Storage.prototype.removeItem = function (key: string) { remove.call(this, `u3-demo.${key}`); };
const root = createRoot(document.getElementById("root")!);
const output = document.getElementById("u3-result")!;
let mountId = 0;
function mount(page: typeof D1Page | typeof D2Page | typeof D3Page | typeof HomePage) {
  root.render(createElement(SourceProvider, { key: ++mountId }, createElement(page)));
}
function assert(value: unknown, message: string): asserts value { if (!value) throw new Error(message); }
async function wait(check: () => boolean) {
  for (let i = 0; i < 500; i++) { if (check()) return; await new Promise(resolve => setTimeout(resolve, 20)); }
  throw new Error("等待真实组件超时");
}
async function json(path: string, body?: unknown) {
  const response = await fetch(path, body === undefined ? { cache: "no-store" } : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  assert(response.ok, `离线 API ${path}：${response.status}`);
  return response.json();
}
function button(label: string) {
  const result = [...document.querySelectorAll<HTMLButtonElement>("button")].find(item => item.textContent?.trim() === label);
  assert(result, `缺少按钮 ${label}`); return result;
}
async function change(selector: string, value: string) {
  const input = document.querySelector<HTMLInputElement | HTMLSelectElement>(selector)!;
  assert(input, `缺少 ${selector}`);
  const prototype = input instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, "value")!.set!.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true })); input.dispatchEvent(new Event("change", { bubbles: true }));
  await new Promise(resolve => setTimeout(resolve, 40));
}
function savedPointer(kind: string) { return JSON.parse(localStorage.getItem(`lt.saved.${kind}`) ?? "null"); }
async function readResult(pointer: { session_id: string; result_id: string }) { return json(`/api/sessions/${pointer.session_id}/results/${pointer.result_id}`); }
async function submitD3(text: string) {
  await wait(() => Boolean(document.querySelector<HTMLInputElement>('[aria-label="决策提问"]')?.disabled === false));
  await change('[aria-label="决策提问"]', text); button("决策").click();
  await wait(() => Boolean(document.querySelector('[data-testid="saved-history"]')?.textContent?.includes("已保存实际结果")));
}
async function run() {
  const phase = new URLSearchParams(location.search).get("phase") ?? "manual";
  if (phase === "manual") {
    mount(D3Page);
    window.addEventListener("hashchange", () => {
      const page = location.hash.split("?")[0];
      if (page === "#/d3") mount(D3Page);
      else if (page === "#/d2") mount(D2Page);
      else if (page === "#/d1") mount(D1Page);
    });
    const controls = document.getElementById("controls")!;
    for (const [label, action] of [
      ["合成收藏", () => void json("/api/favorite-spots", { name: "合成西岸", latitude: 0, longitude: 0, coordinate_system: "WGS84", tz_offset: "+08:00", source: "离线合成" })],
      ["数据失败", () => void json("/api/_offline/mode/failed", {})], ["数据完整", () => void json("/api/_offline/mode/ready", {})],
      ["模拟保存失败", () => void json("/api/_offline/mode/save-failed", {})],
      ["重建应用", () => void json("/api/_offline/restart", {}).then(() => location.reload())],
      ["决策页", () => mount(D3Page)], ["灵感页", () => mount(D1Page)], ["规划页", () => mount(D2Page)], ["历史首页", () => mount(HomePage)],
    ] as const) { const control = document.createElement("button"); control.textContent = label; control.onclick = action; controls.append(control); }
    return;
  }
  if (phase === "generate") {
    for (const key of ["lt.saved.decision", "lt.saved.spot_plan", "lt.saved.chat", "u3.original", "u3.plan"]) localStorage.removeItem(key);
    const spot = await json("/api/favorite-spots", { name: "合成西岸", latitude: 0, longitude: 0, coordinate_system: "WGS84", tz_offset: "+08:00", source: "离线合成" });
    mount(D3Page); await submitD3("今晚火烧云是否值得去");
    const original = await readResult(savedPointer("decision"));
    localStorage.setItem("u3.original", JSON.stringify(original));
    assert(Date.parse(original.card.evaluated_at) === Date.parse("2026-10-06T08:00:00+00:00"), "原始评估时间不正确");
    mount(D2Page);
    await wait(() => Boolean(document.querySelector(`option[value="${spot.id}"]`)));
    await change('input[type="date"]', "2026-10-06");
    await change(".d2-selection-fields label:last-child select", spot.id);
    await wait(() => !button("运行规划").disabled); button("运行规划").click();
    await wait(() => Boolean(savedPointer("spot_plan")));
    localStorage.setItem("u3.plan", JSON.stringify(await readResult(savedPointer("spot_plan"))));
    output.textContent = "PASS: 真实 D3 卡/消息/管线及 D2 计划已落盘；现在关闭浏览器并重建应用";
    return;
  }
  const original = JSON.parse(localStorage.getItem("u3.original")!);
  const plan = JSON.parse(localStorage.getItem("u3.plan")!);
  mount(D3Page);
  await wait(() => Boolean(document.body.textContent?.includes("历史回看 / 待更新")));
  assert(JSON.stringify(await readResult(savedPointer("decision"))) === JSON.stringify(original), "重建后原卡/消息/证据/时间变化");
  assert(document.querySelector('[data-testid="decision-card"]')?.textContent?.includes(original.card.conclusion), "真实组件没有恢复原卡");
  assert(document.querySelector(".message-list")?.textContent?.includes("今晚火烧云是否值得去"), "原消息未恢复");
  assert(document.body.textContent?.includes("历史结果，仅供回看"), "恢复后冒充实时结果");
  mount(D2Page); await wait(() => Boolean(document.querySelector(".d2-plan-state")?.textContent?.includes("冻结")));
  assert(JSON.stringify(await readResult(savedPointer("spot_plan"))) === JSON.stringify(plan), "原计划变化");
  assert(document.body.textContent?.includes("冻结"), "原计划未标识冻结");
  let metrics = await json("/api/_offline/metrics");
  assert(metrics.model_calls === 0 && metrics.tool_calls === 0, "恢复触发了模型或天气地图");
  localStorage.setItem("u3.restore-metrics", JSON.stringify({ model_calls: metrics.model_calls, tool_calls: metrics.tool_calls }));
  mount(D3Page); await wait(() => Boolean(document.querySelector('[aria-label="决策提问"]') && document.body.textContent?.includes("历史回看 / 待更新")));
  await submitD3("那我需要带什么？");
  metrics = await json("/api/_offline/metrics");
  assert(JSON.stringify(metrics.messages).includes("今晚火烧云是否值得去"), "Fake 模型未收到保存历史");
  const followup = await readResult(savedPointer("decision"));
  assert(followup.parent_result_id === original.result_id && followup.version === 2, "续问没有关联版本");
  await wait(() => !button("重新查询原请求（新版本）").disabled);
  button("重新查询原请求（新版本）").click();
  await wait(() => savedPointer("decision")?.result_id !== followup.result_id);
  assert(JSON.stringify(await readResult({ session_id: original.session_id, result_id: original.result_id })) === JSON.stringify(original), "重查覆盖旧版本");
  await json("/api/_offline/mode/save-failed", {});
  await wait(() => !document.querySelector<HTMLInputElement>('[aria-label="决策提问"]')?.disabled);
  await change('[aria-label="决策提问"]', "今晚火烧云是否值得去"); button("决策").click();
  await wait(() => Boolean(document.querySelector('[data-testid="saved-history"]')?.textContent?.includes("保存失败")));
  assert(!document.querySelector('[data-testid="saved-history"]')?.textContent?.includes("已保存实际结果"), "保存失败仍显示已保存");
  await json("/api/_offline/mode/ready", {});
  location.hash = `#/d1?session=${original.session_id}&result=${original.result_id}`;
  mount(D1Page);
  await wait(() => Boolean(document.querySelector('[aria-label="输入内容"]') && document.querySelector('[data-testid="decision-card"]')?.textContent?.includes(original.card.conclusion)));
  assert(document.querySelector(".message-list")?.textContent?.includes("今晚火烧云是否值得去"), "D1 未恢复原消息");
  mount(HomePage); await wait(() => Boolean(document.body.textContent?.includes("今晚火烧云是否值得去")));
  output.textContent = "PASS: 浏览器关闭+应用重建；原卡/消息/计划恢复零调用；续问携带历史；重查保留旧版本；保存失败明确";
}
void run().catch(error => { output.textContent = `FAIL: ${error instanceof Error ? error.message : String(error)}`; });
