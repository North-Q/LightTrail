/** 本机Chrome/Edge真实组件验收；关闭浏览器、重建服务后再次恢复。 */
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { randomUUID } from "node:crypto";

const browser = [process.env.CHROME_PATH, "C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"].filter(Boolean).find(existsSync);
if (!browser) throw new Error("未找到本机Chrome/Edge");
const base = "http://127.0.0.1:4173";
const api = "http://127.0.0.1:8785";
const folder = resolve("../.pytest-run-u5-implementation/20261011", `browser-${randomUUID()}`);
mkdirSync(folder, { recursive: true });
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function connect(url) {
  const socket = new WebSocket(url); const pending = new Map(); let sequence = 0;
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  const events = new Map();
  socket.onmessage = event => {
    const message = JSON.parse(event.data);
    if (!message.id) { events.get(message.method)?.(message.params); return; }
    const entry = pending.get(message.id); pending.delete(message.id);
    if (message.error) entry?.reject(new Error(JSON.stringify(message.error))); else entry?.resolve(message.result);
  };
  return { socket, events, call(method, params = {}) { return new Promise((resolve, reject) => {
    const id = ++sequence; pending.set(id, { resolve, reject }); socket.send(JSON.stringify({ id, method, params }));
  }); } };
}
const summary = { phases: [], blocked_page_external_requests: 0, cases: [] };
let serverChild;
// 每次开发验收只通过生产撤回入口清空本机合成反馈，不触及任何真实材料。
await fetch(`${api}/api/_offline/mode/normal`, { method: "POST" });
const pointers = await (await fetch(`${api}/api/_offline/pointers`)).json();
for (const rid of pointers.result_ids) {
  const path = `${api}/api/sessions/${pointers.session_id}/results/${rid}/feedback`;
  const current = await (await fetch(path)).json();
  if (current.state === "active") {
    const response = await fetch(path + "/withdraw", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_revision: current.revision, mutation_id: randomUUID() }) });
    if (!response.ok) throw new Error("本批合成反馈准备失败");
  }
}
for (const phase of ["lifecycle", "restore"]) {
  if (phase === "restore") {
    const response = await fetch(`${api}/api/_offline/shutdown`, { method: "POST" });
    if (!response.ok) throw new Error("本批合成服务停止失败");
    let stopped = false;
    for (let index = 0; index < 100; index++) {
      await sleep(100);
      try { await fetch(`${api}/api/_offline/pointers`); } catch { stopped = true; break; }
    }
    if (!stopped) throw new Error("合成服务进程未退出，不能宣称重启恢复");
    serverChild = spawn(resolve("../.venv/Scripts/python.exe"), ["-X", "utf8", resolve("../.pytest-run-u5-implementation/20261011/offline.py"), "serve"], { cwd: resolve(".."), stdio: "ignore", windowsHide: true });
    let ready = false;
    for (let index = 0; index < 150; index++) {
      await sleep(100);
      try { if ((await fetch(`${api}/api/_offline/pointers`)).ok) { ready = true; break; } } catch { /* 新进程尚未就绪。 */ }
    }
    if (!ready) { serverChild.kill(); throw new Error("新进程未启动"); }
  }
  const portFile = `${folder}/profile/DevToolsActivePort`;
  const child = spawn(browser, ["--headless=new", "--disable-gpu", "--disable-background-networking", "--disable-component-update", "--no-first-run", "--no-sandbox", `--user-data-dir=${folder}/profile`, "--remote-debugging-port=0", "--window-size=1440,1100", "about:blank"], { stdio: "ignore", windowsHide: true });
  let cdp;
  try {
    let port;
    for (let index = 0; index < 150; index++) {
      if (existsSync(portFile)) {
        port = readFileSync(portFile, "utf8").split("\n")[0];
        try { if ((await fetch(`http://127.0.0.1:${port}/json/version`)).ok) break; } catch { /* 旧端口不采用。 */ }
      }
      await sleep(100);
    }
    const targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
    cdp = await connect(targets.find(item => item.type === "page").webSocketDebuggerUrl);
    const errors = [];
    cdp.events.set("Runtime.exceptionThrown", event => errors.push(event.exceptionDetails.text));
    cdp.events.set("Fetch.requestPaused", async event => {
      try {
        const address = new URL(event.request.url);
        if (["127.0.0.1", "localhost"].includes(address.hostname)) await cdp.call("Fetch.continueRequest", { requestId: event.requestId });
        else { summary.blocked_page_external_requests++; await cdp.call("Fetch.failRequest", { requestId: event.requestId, errorReason: "BlockedByClient" }); }
      } catch (error) {
        // 页面跳转或Abort已取消的拦截ID没有继续请求资格。
        if (!String(error).includes("Invalid InterceptionId")) errors.push(String(error));
      }
    });
    await cdp.call("Runtime.enable");
    await cdp.call("Fetch.enable", { patterns: [{ urlPattern: "http*", requestStage: "Request" }] });
    await cdp.call("Page.navigate", { url: `${base}/tests/u5.html?phase=${phase}` });
    let status = "";
    for (let index = 0; index < 600; index++) {
      const result = await cdp.call("Runtime.evaluate", { expression: 'document.getElementById("u5-result")?.textContent ?? ""', returnByValue: true });
      status = result.result.value;
      if (/^(PASS|FAIL):/.test(status)) break;
      await sleep(100);
    }
    const dom = await cdp.call("Runtime.evaluate", { expression: "document.documentElement.outerHTML", returnByValue: true });
    writeFileSync(`${folder}/${phase}.html`, dom.result.value);
    const screenshot = await cdp.call("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
    writeFileSync(`${folder}/${phase}.png`, Buffer.from(screenshot.data, "base64"));
    console.log(`${phase}: ${status}`);
    if (!status.startsWith("PASS:")) throw new Error(status || "组件验收未完成");
    if (errors.length) throw new Error(`组件运行异常 ${errors.join(", ")}`);
    const checks = await cdp.call("Runtime.evaluate", { expression: 'JSON.parse(localStorage.getItem("u5.cases") ?? "[]")', returnByValue: true });
    summary.cases = checks.result.value;
    if (phase === "restore") {
      const counts = await cdp.call("Runtime.evaluate", { expression: 'JSON.parse(localStorage.getItem("u5.restore-metrics"))', returnByValue: true });
      summary.restore = counts.result.value;
      const pointers = await (await fetch(`${api}/api/_offline/pointers`)).json();
      const beforeSPA = await (await fetch(`${api}/api/_offline/metrics`)).json();
      await cdp.call("Page.navigate", { url: `${base}/#/d3?session=${pointers.session_id}&result=${pointers.result_ids[0]}` });
      let restored = false;
      for (let index = 0; index < 150; index++) {
        const result = await cdp.call("Runtime.evaluate", { expression: 'document.querySelector("[data-testid=feedback-status]")?.textContent ?? ""', returnByValue: true });
        if (result.result.value.includes("已恢复本地记录")) { restored = true; break; }
        await sleep(100);
      }
      if (!restored) throw new Error("完整SPA恢复反馈失败");
      const afterSPA = await (await fetch(`${api}/api/_offline/metrics`)).json();
      for (const key of ["model", "weather", "maps", "photos", "memory", "feedback_writes"]) {
        if (beforeSPA[key] !== afterSPA[key]) throw new Error(`SPA恢复出现调用/写入 ${key}`);
      }
      summary.spa_restore = { before: beforeSPA, after: afterSPA };
      await cdp.call("Runtime.evaluate", { expression: 'document.querySelector(".page-body").style.scrollBehavior="auto"; document.querySelector("[data-testid=feedback-panel]").scrollIntoView({block:"start",behavior:"instant"})' });
      await sleep(150);
      const desktop = await cdp.call("Page.captureScreenshot", { format: "png" });
      writeFileSync(`${folder}/spa-feedback-desktop.png`, Buffer.from(desktop.data, "base64"));
      await cdp.call("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
      await sleep(250);
      await cdp.call("Runtime.evaluate", { expression: 'document.querySelector("[data-testid=feedback-panel]").scrollIntoView({block:"start",behavior:"instant"})' });
      await sleep(150);
      const position = await cdp.call("Runtime.evaluate", { expression: 'document.querySelector("[data-testid=feedback-panel]").getBoundingClientRect().top', returnByValue: true });
      if (position.result.value < 0 || position.result.value > 150) throw new Error("反馈面板未滚入移动视口，截图不能作为可见性证据");
      const width = await cdp.call("Runtime.evaluate", { expression: "innerWidth === 390 && document.documentElement.scrollWidth <= innerWidth && getComputedStyle(document.querySelector('.feedback-grid')).gridTemplateColumns.split(' ').length === 1", returnByValue: true });
      if (!width.result.value) throw new Error("移动视口横向溢出");
      const mobile = await cdp.call("Page.captureScreenshot", { format: "png" });
      writeFileSync(`${folder}/restore-mobile.png`, Buffer.from(mobile.data, "base64"));
    }
    summary.phases.push({ phase, status });
    await cdp.call("Browser.close");
    await new Promise(resolve => { if (child.exitCode !== null) resolve(); else child.once("exit", resolve); });
  } finally { cdp?.socket.close(); if (child.exitCode === null) child.kill(); }
}
summary.metrics = await (await fetch(`${api}/api/_offline/metrics`)).json();
writeFileSync(`${folder}/summary.json`, JSON.stringify(summary, null, 2));
writeFileSync(resolve("../.pytest-run-u5-implementation/20261011/latest-browser.json"), JSON.stringify({ folder, ...summary }, null, 2));
console.log(`证据：${folder}`);
await fetch(`${api}/api/_offline/shutdown`, { method: "POST" });
if (serverChild && serverChild.exitCode === null) await new Promise(resolve => serverChild.once("exit", resolve));
