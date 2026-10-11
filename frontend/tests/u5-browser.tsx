/** 真实D3组件连接生产反馈API；只在隔离服务中注入写盘/响应故障。 */
import { createElement } from "react";
import { createRoot } from "react-dom/client";
import { SourceProvider } from "../src/context/SourceContext";
import { D3Page } from "../src/pages/D3Page";
import { go } from "../src/components/AppShell";
import "../src/styles.css";

const output = document.getElementById("u5-result")!;
const root = createRoot(document.getElementById("root")!);
let mountId = 0;
const cases: string[] = [];
const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
function assert(value: unknown, message: string): asserts value { if (!value) throw new Error(message); }
async function wait(check: () => boolean) {
  for (let i = 0; i < 500; i++) { if (check()) return; await sleep(20); }
  throw new Error(`等待超时：${document.querySelector('[data-testid="feedback-status"]')?.textContent}`);
}
async function json(path: string, method = "GET", body?: unknown) {
  const response = await fetch(path, { method, cache: "no-store", ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}) });
  assert(response.ok, `${path} ${response.status}`); return response.json();
}
function button(label: string): HTMLButtonElement {
  const found = [...document.querySelectorAll<HTMLButtonElement>("button")].find(item => item.textContent?.trim() === label);
  assert(found, `缺少按钮 ${label}`); return found;
}
function notice() { return document.querySelector('[data-testid="feedback-status"]')?.textContent ?? ""; }
async function change(label: string, value: string) {
  await wait(() => Boolean(document.querySelector(`[aria-label="${label}"]`) && !(document.querySelector(`[aria-label="${label}"]`) as HTMLInputElement).disabled));
  const field = document.querySelector<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(`[aria-label="${label}"]`)!;
  assert(field && !field.disabled, `字段不可用 ${label}`);
  const proto = field instanceof HTMLSelectElement ? HTMLSelectElement.prototype : field instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(field, value);
  field.dispatchEvent(new Event("input", { bubbles: true })); field.dispatchEvent(new Event("change", { bubbles: true })); await sleep(40);
}
async function mount(target: { session_id: string; result_id: string }) {
  history.replaceState(null, "", `#/d3?session=${target.session_id}&result=${target.result_id}`);
  root.render(createElement(SourceProvider, { key: ++mountId }, createElement(D3Page)));
  await wait(() => notice().includes("尚无反馈") || notice().includes("已恢复本地记录"));
}
async function saved() { await wait(() => notice().includes("已保存")); }
async function click(label: string) {
  await wait(() => !button(label).disabled); button(label).click(); await sleep(60);
}

async function run() {
  window.confirm = () => true; // 仅自动响应原生确认；API和组件状态不替换。
  const pointers = await json("/api/_offline/pointers");
  const target = { session_id: pointers.session_id, result_id: pointers.result_ids[0] };
  const second = { session_id: pointers.session_id, result_id: pointers.result_ids[1] };
  const url = `/api/sessions/${target.session_id}/results/${target.result_id}/feedback`;
  const sourceUrl = url.replace(/\/feedback$/, "");
  const original = await json(sourceUrl);
  const phase = new URLSearchParams(location.search).get("phase") ?? "lifecycle";
  if (phase === "restore") {
    const before = await json("/api/_offline/metrics");
    await mount(target);
    const feedback = await json(url);
    assert(feedback.record.feedback.observation_result === null, "恢复把未观察变成了结果");
    assert(document.querySelector('[aria-label="是否出门"]')?.getAttribute("disabled") === null, "恢复表单不可用");
    assert((document.querySelector('[aria-label="是否出门"]') as HTMLSelectElement).value === "not_went", "反馈未恢复");
    assert(JSON.stringify(await json(sourceUrl)) === localStorage.getItem("u5.original"), "恢复改写原卡或有效期");
    const after = await json("/api/_offline/metrics");
    for (const key of ["model", "weather", "maps", "photos", "memory", "feedback_writes"]) assert(after[key] === before[key], `恢复新增调用/写入 ${key}`);
    output.textContent = "PASS: 浏览器关闭后重建服务，稳定ID恢复仅GET、外调与反馈写入零新增";
    localStorage.setItem("u5.restore-metrics", JSON.stringify({ before, after })); return;
  }
  localStorage.setItem("u5.original", JSON.stringify(original));
  await mount(target);
  assert(!document.querySelector('[type="file"]'), "出现照片上传控件");
  await change("是否出门", "not_went"); await change("是否独立观察", "not_observed");
  await click("保存反馈"); await saved();
  let current = await json(url);
  assert(current.record.feedback.observation_result === null && !current.record.experience, "没去未观察被推断或自动沉淀");
  cases.push("V01/V02/V14/V15: 没去/未观察null，独立原因，无经验/照片自动动作");
  await change("本人经验内容", "合成本人偏好：出发前核对有效期");
  await click("保存经验候选"); await wait(() => notice().includes("候选已保存"));
  assert(!button("确认本地经验").disabled, "草稿不能独立确认");
  await click("确认本地经验"); await wait(() => Boolean(document.querySelector('[role="dialog"][aria-label="确认本人本地经验"]')));
  await click("本人确认保存"); await wait(() => notice().includes("本人已确认"));
  assert((await json(url)).experience_effective, "独立确认失败");
  await change("出门选择原因（可选）", "合成原因，非真实陈述");
  await click("保存反馈"); await saved();
  current = await json(url);
  assert(current.record.experience.status === "needs_review" && current.record.experience.confirmed_at === null, "修改留下旧有效确认");
  assert(button("确认本地经验").disabled, "待复核允许直接确认");
  await click("保存经验候选"); await wait(() => notice().includes("候选已保存"));
  await click("确认本地经验"); await wait(() => Boolean(document.querySelector('[role="dialog"][aria-label="确认本人本地经验"]')));
  await click("本人确认保存"); await wait(() => notice().includes("本人已确认"));
  cases.push("V05: 反馈修改使旧确认失效，重新保存当前版本候选后才能再次确认");
  await click("撤回经验"); await wait(() => notice().includes("经验已撤回"));
  current = await json(url); assert(current.record.experience.content === null && current.record.feedback, "经验撤回影响原反馈或残留正文");
  cases.push("V07: 经验撤回清空正文与确认时间，原始反馈保留");
  await json("/api/_offline/mode/write_fail", "POST");
  const beforeFailure = JSON.stringify(current);
  await change("原始补充（可选）", "合成写入失败草稿"); await click("保存反馈");
  await wait(() => notice().includes("保存失败"));
  assert(JSON.stringify(await json(url)) === beforeFailure, "写盘失败改变旧记录");
  cases.push("V10: 实际生产仓库替换失败，组件不显示保存成功");
  await json("/api/_offline/mode/response_loss", "POST");
  await click("保存反馈"); await wait(() => notice().includes("响应未确认"));
  assert(button("保存反馈").disabled, "响应未知仍可盲目写入");
  await json("/api/_offline/mode/normal", "POST");
  await click("只读核对保存状态"); await wait(() => notice().includes("最近操作已保存"));
  cases.push("V11: 持久化后响应丢失显示未确认，只读按mutation核对");
  await change("是否独立观察", "observed"); await change("观察结果", "not_seen");
  await change("观察范围描述", "合成：在家观察，窗口未知");
  assert(document.body.textContent?.includes("不能据此判读原机位完整窗口"), "范围未知仍评原机位");
  window.confirm = () => false;
  await change("是否独立观察", "not_observed");
  assert((document.querySelector('[aria-label="是否独立观察"]') as HTMLSelectElement).value === "observed", "取消仍清空旧结果");
  const hash = location.hash; go("#/home"); assert(location.hash === hash, "未保存离开取消无效");
  window.confirm = () => true;
  await change("是否独立观察", "not_observed");
  assert(!document.querySelector('[aria-label="观察结果"]'), "切换未观察未清空结果控件");
  await click("保存反馈"); await saved();
  cases.push("V03/V04: 观察范围独立，未看到非全局结论，取消/确认清除与离开草稿提醒");
  await json("/api/_offline/mode/late", "POST");
  await change("原始补充（可选）", "合成迟到保存"); await click("保存反馈"); await sleep(100);
  await change("历史结果版本", `${second.session_id}/${second.result_id}`);
  await wait(() => notice().includes("尚无反馈")); await sleep(1500);
  assert(notice().includes("尚无反馈"), "迟到保存污染另一结果");
  assert((document.querySelector('[aria-label="是否出门"]') as HTMLSelectElement).value === "unknown", "另一结果继承反馈");
  await json("/api/_offline/mode/normal", "POST"); await mount(target);
  await json("/api/_offline/mode/late", "POST");
  await change("原始补充（可选）", "合成停止等待"); await click("保存反馈"); await sleep(80);
  await click("停止反馈等待"); await sleep(1400);
  assert(notice().includes("不能确认服务端写入已取消"), "停止等待错误承诺取消或迟到回写");
  await json("/api/_offline/mode/normal", "POST");
  await click("只读核对保存状态"); await wait(() => notice().includes("最近操作已保存"));
  await json("/api/_offline/mode/late", "POST");
  await change("原始补充（可选）", "合成卸载等待"); await click("保存反馈"); await sleep(80);
  root.render(createElement("p", { id: "unmounted" }, "已离开反馈页")); await sleep(1500);
  assert(document.querySelector("#unmounted")?.textContent === "已离开反馈页", "卸载后仍回写");
  await json("/api/_offline/mode/normal", "POST"); await mount(target);
  cases.push("V13: 切换/停止/卸载均拒绝迟到回写，未承诺服务端取消");
  await click("撤回反馈"); await wait(() => notice().includes("反馈已撤回"));
  current = await json(url); assert(!current.record.feedback && !current.record.experience, "反馈撤回残留正文或经验");
  await change("是否出门", "not_went"); await change("是否独立观察", "not_observed");
  await click("保存反馈"); await saved();
  assert(!(await json(url)).record.experience, "重新填写复活旧经验");
  assert(JSON.stringify(await json(sourceUrl)) === JSON.stringify(original), "反馈动作改写原卡或有效期");
  cases.push("V06: 反馈撤回清空经验，最新版本重新填写不复活旧经验，原卡与有效期字节语义不变");
  const metrics = await json("/api/_offline/metrics");
  for (const key of ["model", "weather", "maps", "photos", "memory"]) assert(metrics[key] === 0, `出现业务调用 ${key}`);
  localStorage.setItem("u5.cases", JSON.stringify(cases));
  output.textContent = `PASS: ${cases.length}组真实D3组件闭环；生产API持久化，业务外调0`;
}
void run().catch(error => { output.textContent = `FAIL: ${error.message}`; console.error(error); });
