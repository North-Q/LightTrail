/** 真实关闭浏览器，重建离线应用，再用同一浏览器隔离目录恢复；使用实时 CDP。 */
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { randomUUID } from "node:crypto";
const browser = [process.env.CHROME_PATH, "C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"].filter(Boolean).find(existsSync);
if (!browser) throw new Error("未找到本机 Chrome/Edge");
const base = process.env.LIGHTTRAIL_UI_BASE ?? "http://127.0.0.1:4173";
const api = process.env.LIGHTTRAIL_U3_API ?? "http://127.0.0.1:8783";
const folder = resolve("../.pytest-run-u3", `browser-${randomUUID()}`);
mkdirSync(folder, { recursive: true });
const fresh = await fetch(`${api}/api/_offline/new-run`, { method: "POST" });
if (!fresh.ok) throw new Error(`隔离验收目录创建失败 ${fresh.status}`);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function connect(url) {
  const socket = new WebSocket(url); const pending = new Map(); let sequence = 0;
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  socket.onmessage = event => { const message = JSON.parse(event.data); if (!message.id) return; const entry = pending.get(message.id); pending.delete(message.id); if (message.error) entry?.reject(new Error(JSON.stringify(message.error))); else entry?.resolve(message.result); };
  return { socket, call(method, params = {}) { return new Promise((resolve, reject) => { const id = ++sequence; pending.set(id, { resolve, reject }); socket.send(JSON.stringify({ id, method, params })); }); } };
}
for (const phase of ["generate", "restore"]) {
  if (phase === "restore") {
    const response = await fetch(`${api}/api/_offline/restart`, { method: "POST" });
    if (!response.ok) throw new Error(`重建失败 ${response.status}`);
  }
  const activePort = `${folder}/profile/DevToolsActivePort`;
  const child = spawn(browser, ["--headless=new", "--disable-gpu", "--no-sandbox", `--user-data-dir=${folder}/profile`, "--remote-debugging-port=0", "--window-size=1440,900", "about:blank"], { stdio: "ignore", windowsHide: true });
  let cdp;
  try {
    let port;
    for (let i = 0; i < 150; i++) {
      if (existsSync(activePort)) {
        port = readFileSync(activePort, "utf-8").split("\n")[0];
        try { if ((await fetch(`http://127.0.0.1:${port}/json/version`)).ok) break; } catch { /* 旧文件对应进程已退出。 */ }
      }
      await sleep(100);
    }
    const targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
    cdp = await connect(targets.find(item => item.type === "page").webSocketDebuggerUrl);
    await cdp.call("Page.navigate", { url: `${base}/tests/u3.html?phase=${phase}` });
    let status = "";
    for (let i = 0; i < 400; i++) {
      const result = await cdp.call("Runtime.evaluate", { expression: 'document.getElementById("u3-result")?.textContent ?? ""', returnByValue: true });
      status = result.result.value;
      if (/^(PASS|FAIL):/.test(status)) break;
      await sleep(100);
    }
    const dom = await cdp.call("Runtime.evaluate", { expression: "document.documentElement.outerHTML", returnByValue: true });
    writeFileSync(`${folder}/${phase}.html`, dom.result.value);
    const screenshot = await cdp.call("Page.captureScreenshot", { format: "png" });
    writeFileSync(`${folder}/${phase}.png`, Buffer.from(screenshot.data, "base64"));
    console.log(`${phase}: ${status}`);
    if (!status.startsWith("PASS:")) throw new Error(status || "浏览器未完成验收");
    if (phase === "restore") {
      const original = await cdp.call("Runtime.evaluate", { expression: 'JSON.parse(localStorage.getItem("u3.original"))', returnByValue: true });
      const snapshot = original.result.value;
      const restoreMetrics = await cdp.call("Runtime.evaluate", { expression: 'JSON.parse(localStorage.getItem("u3.restore-metrics"))', returnByValue: true });
      writeFileSync(`${folder}/initial-restoration-metrics.json`, JSON.stringify(restoreMetrics.result.value, null, 2));
      const before = await (await fetch(`${api}/api/_offline/metrics`)).json();
      await cdp.call("Page.navigate", { url: `${base}/#/d3?session=${snapshot.session_id}&result=${snapshot.result_id}` });
      let restored = false;
      for (let i = 0; i < 150; i++) {
        const content = await cdp.call("Runtime.evaluate", { expression: 'document.querySelector("[data-testid=decision-card]")?.textContent ?? ""', returnByValue: true });
        if (content.result.value.includes(snapshot.card.conclusion) && content.result.value.includes("历史结果，仅供回看")) { restored = true; break; }
        await sleep(100);
      }
      if (!restored) throw new Error("完整 SPA 稳定 ID 恢复失败");
      const after = await (await fetch(`${api}/api/_offline/metrics`)).json();
      if (before.model_calls !== after.model_calls || before.tool_calls !== after.tool_calls) throw new Error("完整 SPA 恢复触发外部替身");
      const appDom = await cdp.call("Runtime.evaluate", { expression: "document.documentElement.outerHTML", returnByValue: true });
      writeFileSync(`${folder}/app-restored.html`, appDom.result.value);
      const appImage = await cdp.call("Page.captureScreenshot", { format: "png" });
      writeFileSync(`${folder}/app-restored.png`, Buffer.from(appImage.data, "base64"));
      writeFileSync(`${folder}/restoration-metrics.json`, JSON.stringify({ before: { model_calls: before.model_calls, tool_calls: before.tool_calls }, after: { model_calls: after.model_calls, tool_calls: after.tool_calls } }, null, 2));
      await cdp.call("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
      const width = await cdp.call("Runtime.evaluate", { expression: "document.documentElement.scrollWidth <= innerWidth", returnByValue: true });
      if (!width.result.value) throw new Error("恢复页面在移动视口横向溢出");
      const mobile = await cdp.call("Page.captureScreenshot", { format: "png" });
      writeFileSync(`${folder}/app-restored-mobile.png`, Buffer.from(mobile.data, "base64"));
      console.log("SPA: PASS: 按稳定 ID 恢复原卡与消息，调用数不增加");
    }
    await cdp.call("Browser.close");
    await new Promise(resolve => { if (child.exitCode !== null) resolve(); else child.once("exit", resolve); });
  } finally { cdp?.socket.close(); if (child.exitCode === null) child.kill(); }
}
const response = await fetch(`${api}/api/_offline/metrics`);
writeFileSync(`${folder}/metrics.json`, JSON.stringify(await response.json(), null, 2));
console.log(`证据：${folder}`);
