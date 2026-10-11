/** 真实页面离线验收：发送前说明、接收方与本地删除边界。 */
import { createElement } from "react";
import { createRoot } from "react-dom/client";
import "../src/styles.css";
import { SourceProvider } from "../src/context/SourceContext";
import { D1Page } from "../src/pages/D1Page";
import { D2Page } from "../src/pages/D2Page";
import { D3Page } from "../src/pages/D3Page";
import { D4Page } from "../src/pages/D4Page";
import { M1Page } from "../src/pages/M1Page";

// 不读取用户已有浏览器存储，也不允许任何请求离开合成替身。
Storage.prototype.getItem = () => null;
Storage.prototype.setItem = () => undefined;
Storage.prototype.removeItem = () => undefined;
window.fetch = (async (input: RequestInfo | URL): Promise<Response> => {
  const path = String(input);
  if (path === "/api/data-boundaries") return Response.json({ llm_receiver: "synthetic-model.invalid" });
  if (path === "/api/profile") return Response.json({ camera_body: "合成相机", lenses: [], preferences: [], common_locations: [], skill_level: "合成", favorite_spots: [] });
  if (path === "/api/favorite-spots") return Response.json({ items: [], skipped: [] });
  throw new Error(`验收拒绝非预期请求：${path}`);
}) as typeof fetch;
const root = createRoot(document.getElementById("root")!);
const result = document.getElementById("u23-result")!;
function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(message);
}
async function mount(component: typeof D1Page | typeof D2Page | typeof D3Page | typeof D4Page | typeof M1Page): Promise<void> {
  root.render(createElement(SourceProvider, null, createElement(component)));
  await new Promise((resolve) => setTimeout(resolve, 100));
}
function precedes(first: Element, last: Element): boolean {
  return Boolean(first.compareDocumentPosition(last) & Node.DOCUMENT_POSITION_FOLLOWING);
}
async function run(): Promise<void> {
  await mount(D1Page);
  let notices = document.querySelectorAll(".data-notice");
  assert(notices.length === 2, "D1 文字及参考图均需提示");
  assert(precedes(notices[0], document.querySelector("form")!), "D1 文字提示应在发送前");
  assert(precedes(notices[1], document.querySelector('[type="file"]')!), "D1 参考图提示应在选图前");
  assert(notices[1].textContent?.includes("不提取 GPS"), "不得把选定 EXIF 误称不含拍摄信息");
  await mount(D2Page);
  assert(document.querySelector(".data-notice")?.textContent?.includes("Open-Meteo"), "规划天气接收方缺失");
  assert(document.body.textContent?.includes("高德或百度"), "赶场接收方缺失");
  await mount(D3Page);
  assert(document.querySelector(".data-notice")?.textContent?.includes("synthetic-model.invalid"), "应显示配置模型接收方");
  await mount(D4Page);
  const photoNotice = document.querySelector(".data-notice")!;
  assert(precedes(photoNotice, document.querySelector('[type="file"]')!), "D4 选图前未提示");
  assert(photoNotice.textContent?.includes("停止等待不会保证即时删除"), "取消与临时文件边界缺失");
  await mount(M1Page);
  const memory = document.querySelector('[aria-label="本地数据与删除说明"]')!;
  assert(memory.textContent?.includes("不清除历史会话"), "收藏删除范围不诚实");
  assert(memory.textContent?.includes("不会删除模型、天气或地图供应商的副本"), "不能把本地清理当供应商删除");
  assert(memory.textContent?.includes("暂无一键删除"), "缺少尚未覆盖的说明");
  assert(document.documentElement.scrollWidth <= window.innerWidth + 1, "说明造成横向溢出");
  result.textContent = "PASS: D1–D4 发送前说明、配置接收方、照片/EXIF、停止边界与 M1 删除说明";
  const controls = document.getElementById("controls")!;
  for (const [label, page] of [["D1", D1Page], ["D2", D2Page], ["D3", D3Page], ["D4", D4Page], ["M1", M1Page]] as const) {
    const button = document.createElement("button");
    button.className = "btn btn-secondary";
    button.textContent = label;
    button.onclick = () => { void mount(page); };
    controls.appendChild(button);
  }
}
void run().catch((error) => { result.textContent = `FAIL: ${String(error)}`; });
