/** 在真实浏览器中验证机位 CRUD、删除确认与本次活动机位交接。 */

import { createElement } from "react";
import { createRoot } from "react-dom/client";
import "../src/styles.css";
import { SourceProvider } from "../src/context/SourceContext";
import { D2Page } from "../src/pages/D2Page";
import { M1Page } from "../src/pages/M1Page";

interface Spot {
  id: string;
  name: string;
  latitude: number | null;
  longitude: number | null;
  coordinate_system: string | null;
  coordinate_state: string;
  tz_offset: string | null;
  direction_deg: number | null;
  subject_types: string[];
  notes: string;
  source: string;
}

const result = document.getElementById("s1-3-result")!;
const root = createRoot(document.getElementById("root")!);
const calls: Array<{ method: string; url: string; body: Record<string, unknown> | null }> = [];
let allowDelete = false;
let spots: Spot[] = [{
  id: "legacy-spot", name: "旧机位", latitude: 31.1, longitude: 121.4,
  coordinate_system: null, coordinate_state: "unknown_system", tz_offset: null,
  direction_deg: null, subject_types: [], notes: "", source: "旧档案，来源未记录",
}, {
  id: "unlocated-spot", name: "待勘察", latitude: null, longitude: null,
  coordinate_system: null, coordinate_state: "missing_coordinates", tz_offset: null,
  direction_deg: null, subject_types: [], notes: "", source: "用户收藏",
}, {
  id: "missing-timezone", name: "旧 WGS84 机位", latitude: 31.2, longitude: 121.5,
  coordinate_system: "WGS84", coordinate_state: "ready", tz_offset: null,
  direction_deg: null, subject_types: [], notes: "", source: "旧档案",
}];

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

async function waitFor(check: () => boolean): Promise<void> {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    if (check()) return;
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  throw new Error("页面状态等待超时");
}

function changeValue(element: HTMLInputElement | HTMLSelectElement, value: string): void {
  assert(element, `缺少待填写控件：${value}`);
  const prototype = element instanceof HTMLInputElement
    ? HTMLInputElement.prototype : HTMLSelectElement.prototype;
  try {
    Object.getOwnPropertyDescriptor(prototype, "value")!.set!.call(element, value);
  } catch (error) {
    throw new Error(`无法填写 ${element.tagName} ${element.getAttribute("type") ?? ""}：${error instanceof Error ? error.message : String(error)}`);
  }
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
}

function buttonIn(row: Element, label: string): HTMLButtonElement {
  const button = [...row.querySelectorAll<HTMLButtonElement>("button")]
    .find((item) => item.textContent?.trim() === label);
  assert(button, `缺少按钮：${label}`);
  return button;
}

window.confirm = () => allowDelete;
window.fetch = (async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
  const url = String(input);
  const method = init?.method ?? "GET";
  const body = typeof init?.body === "string" ? JSON.parse(init.body) as Record<string, unknown> : null;
  calls.push({ method, url, body });
  if (url === "/api/profile" && method === "GET") {
    return Response.json({
      camera_body: "", lenses: [], preferences: [], common_locations: [],
      skill_level: "", favorite_spots: [],
    });
  }
  if (url === "/api/favorite-spots" && method === "GET") {
    return Response.json({ items: spots, skipped: [] });
  }
  if (url === "/api/favorite-spots" && method === "POST") {
    const input = body!;
    const spot: Spot = {
      id: "created-spot",
      name: String(input.name),
      latitude: Number(input.latitude),
      longitude: Number(input.longitude),
      coordinate_system: String(input.coordinate_system),
      coordinate_state: "ready",
      tz_offset: String(input.tz_offset),
      direction_deg: input.direction_deg == null ? null : Number(input.direction_deg),
      subject_types: input.subject_types as string[],
      notes: String(input.notes),
      source: String(input.source),
    };
    spots = [...spots, spot];
    return Response.json(spot, { status: 201 });
  }
  if (url.startsWith("/api/favorite-spots/") && method === "PUT") {
    const id = url.split("/").pop()!;
    spots = spots.map((spot) => spot.id === id ? {
      ...spot, ...body, id,
      coordinate_state: body?.latitude == null ? "missing_coordinates"
        : body.coordinate_system === "WGS84" ? "ready" : "unsupported_system",
    } as Spot : spot);
    return Response.json(spots.find((spot) => spot.id === id));
  }
  if (url.startsWith("/api/favorite-spots/") && method === "DELETE") {
    const id = url.split("/").pop()!;
    spots = spots.filter((spot) => spot.id !== id);
    return Response.json({ deleted_id: id });
  }
  throw new Error(`意外请求：${method} ${url}`);
}) as typeof fetch;

async function run(): Promise<void> {
  sessionStorage.clear();
  root.render(createElement(SourceProvider, null, createElement(M1Page)));
  await waitFor(() => Boolean(document.querySelector(".m1-spot-row")));
  const legacyRow = [...document.querySelectorAll(".m1-spot-row")]
    .find((row) => row.textContent?.includes("旧机位"))!;
  assert(legacyRow.textContent?.includes("坐标系待确认"), "旧机位缺少禁用原因");
  assert(buttonIn(legacyRow, "设为本次").disabled, "未知坐标系机位可被设为本次");
  const unlocatedRow = [...document.querySelectorAll(".m1-spot-row")]
    .find((row) => row.textContent?.includes("待勘察"))!;
  assert(unlocatedRow.textContent?.includes("无坐标，不可参与计算"), "无坐标机位缺少原因");
  assert(buttonIn(unlocatedRow, "设为本次").disabled, "无坐标机位可被设为本次");
  const missingTimezoneRow = [...document.querySelectorAll(".m1-spot-row")]
    .find((row) => row.textContent?.includes("旧 WGS84 机位"))!;
  assert(missingTimezoneRow.textContent?.includes("时区未填，不可用于本次规划"), "旧机位缺少时区风险提示");
  assert(buttonIn(missingTimezoneRow, "设为本次").disabled, "无时区机位可被设为本次");

  const add = [...document.querySelectorAll<HTMLButtonElement>("button")]
    .find((item) => item.textContent?.trim() === "新增机位")!;
  add.click();
  await waitFor(() => Boolean(document.querySelector(".m1-spot-form")));
  await waitFor(() => document.activeElement === document.querySelector(".m1-spot-form input"));
  const form = document.querySelector<HTMLFormElement>(".m1-spot-form")!;
  const required = form.querySelectorAll<HTMLInputElement>('input[required]');
  const numbers = form.querySelectorAll<HTMLInputElement>('input[type="number"]');
  changeValue(required[0], "东滩");
  changeValue(numbers[0], "31.123456");
  changeValue(numbers[1], "121.987654");
  changeValue(form.querySelector<HTMLSelectElement>("select")!, "WGS84");
  changeValue(required[1], "用户实测");
  form.querySelector<HTMLButtonElement>('button[type="submit"]')!.click();
  await waitFor(() => calls.some((call) => call.method === "POST" && call.url === "/api/favorite-spots"));
  await waitFor(() => document.querySelectorAll(".m1-spot-row").length === 4);
  const posted = calls.find((call) => call.method === "POST")!.body!;
  assert(posted.coordinate_system === "WGS84" && posted.tz_offset === "+08:00", "创建机位未提交坐标系和时区");

  let newRow = [...document.querySelectorAll(".m1-spot-row")]
    .find((row) => row.textContent?.includes("东滩"))!;
  buttonIn(newRow, "设为本次").click();
  assert(sessionStorage.getItem("lt.active_spot_id") === "created-spot", "本次机位未保存 ID");
  assert(!JSON.stringify({ ...sessionStorage }).includes("31.123456"), "浏览器存储了精确坐标");

  root.render(createElement(SourceProvider, null, createElement(D2Page)));
  await waitFor(() => Boolean(document.querySelector('option[value="created-spot"]')));
  assert(!document.querySelector('option[value="missing-timezone"]'), "D2 可选择无时区旧机位");
  const selected = document.querySelector('option[value="created-spot"]')!.parentElement as HTMLSelectElement;
  assert(selected.value === "created-spot", "D2 未按稳定 ID 恢复本次机位");

  root.render(createElement(SourceProvider, null, createElement(M1Page)));
  await waitFor(() => document.querySelectorAll(".m1-spot-row").length === 4);
  newRow = [...document.querySelectorAll(".m1-spot-row")]
    .find((row) => row.textContent?.includes("东滩"))!;
  buttonIn(newRow, "编辑").click();
  await waitFor(() => Boolean(document.querySelector(".m1-spot-form")));
  const editName = document.querySelector<HTMLInputElement>('.m1-spot-form input[required]')!;
  changeValue(editName, "东滩新机位");
  document.querySelector<HTMLButtonElement>('.m1-spot-form button[type="submit"]')!.click();
  await waitFor(() => calls.some((call) => call.method === "PUT" && call.url.endsWith("created-spot")));
  await waitFor(() => document.body.textContent?.includes("东滩新机位") ?? false);
  newRow = [...document.querySelectorAll(".m1-spot-row")]
    .find((row) => row.textContent?.includes("东滩新机位"))!;

  buttonIn(newRow, "删除").click();
  assert(!calls.some((call) => call.method === "DELETE"), "取消确认后仍删除了机位");
  allowDelete = true;
  buttonIn(newRow, "删除").click();
  await waitFor(() => calls.some((call) => call.method === "DELETE"));
  await waitFor(() => document.querySelectorAll(".m1-spot-row").length === 3);
  assert(sessionStorage.getItem("lt.active_spot_id") === null, "删除后仍保留本次机位 ID");

  const oldRow = [...document.querySelectorAll(".m1-spot-row")]
    .find((row) => row.textContent?.includes("旧机位"))!;
  buttonIn(oldRow, "编辑").click();
  await waitFor(() => Boolean(document.querySelector(".m1-spot-form")));
  const oldForm = document.querySelector<HTMLFormElement>(".m1-spot-form")!;
  assert(oldForm.textContent?.includes("旧坐标系待确认"), "编辑旧机位缺少确认提示");
  assert(oldForm.querySelector<HTMLSelectElement>("select")?.value === "", "旧坐标系被静默推断");
  assert(oldForm.checkValidity(), `旧机位表单被原生校验拦截：${[...oldForm.querySelectorAll(":invalid")].map((item) => item.outerHTML).join("; ")}`);
  oldForm.querySelector<HTMLButtonElement>('button[type="submit"]')!.click();
  await waitFor(() => document.body.textContent?.includes("请确认坐标系") ?? false);
  assert(!calls.some((call) => call.method === "PUT" && call.url.endsWith("legacy-spot")), "未确认坐标系时写入旧机位");
  changeValue(oldForm.querySelector<HTMLSelectElement>("select")!, "WGS84");
  const timezone = [...oldForm.querySelectorAll<HTMLInputElement>("input")]
    .find((input) => input.placeholder === "+08:00")!;
  changeValue(timezone, "+08:00");
  assert(oldForm.checkValidity(), `确认后的旧机位表单无效：${[...oldForm.querySelectorAll(":invalid")].map((item) => item.outerHTML).join("; ")}`);
  oldForm.querySelector<HTMLButtonElement>('button[type="submit"]')!.click();
  await waitFor(() => calls.some((call) => call.method === "PUT" && call.url.endsWith("legacy-spot")));
  await waitFor(() => [...document.querySelectorAll(".m1-spot-row")]
    .some((row) => row.textContent?.includes("旧机位") && Boolean(row.querySelector(".m1-spot-state.is-ready"))));
  const previewAdd = [...document.querySelectorAll<HTMLButtonElement>("button")]
    .find((item) => item.textContent?.trim() === "新增机位")!;
  previewAdd.click();
  await waitFor(() => Boolean(document.querySelector(".m1-spot-form")));
  if (new URLSearchParams(window.location.search).has("preview")) {
    document.querySelector(".m1-spots")?.scrollIntoView();
  }
  result.textContent = "PASS: M1 CRUD、旧机位禁用、删除确认与 D2 ID 交接";
}

void run().catch((error: unknown) => {
  result.textContent = `FAIL: ${error instanceof Error ? error.stack ?? error.message : String(error)}`;
});
