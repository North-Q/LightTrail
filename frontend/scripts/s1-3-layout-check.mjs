/** 使用 Chrome DevTools 的真实设备视口检查 M1 编辑器水平溢出。 */

import { mkdir, writeFile } from "node:fs/promises";

const [debugPort = "9227", origin = "http://127.0.0.1:5173"] = process.argv.slice(2);
const targets = await (await fetch(`http://127.0.0.1:${debugPort}/json/list`)).json();
const target = targets.find((item) => item.type === "page");
if (!target) throw new Error("未找到可用的 Chrome 页面");

const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener("open", resolve, { once: true });
  socket.addEventListener("error", reject, { once: true });
});
let nextId = 1;
const pending = new Map();
socket.addEventListener("message", ({ data }) => {
  const message = JSON.parse(data);
  if (!message.id) return;
  const task = pending.get(message.id);
  if (!task) return;
  pending.delete(message.id);
  if (message.error) task.reject(new Error(message.error.message));
  else task.resolve(message.result);
});

function send(method, params = {}) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

async function evaluate(expression) {
  const response = await send("Runtime.evaluate", { expression, returnByValue: true });
  if (response.exceptionDetails) throw new Error(response.exceptionDetails.text);
  return response.result.value;
}

try {
  await send("Page.enable");
  for (const [width, height] of [[390, 844], [1440, 900]]) {
    await send("Emulation.setDeviceMetricsOverride", {
      width, height, deviceScaleFactor: 1, mobile: width < 600,
    });
    await send("Page.navigate", { url: `${origin}/tests/s1-3.html?preview=1` });
    let status = "RUNNING";
    for (let attempt = 0; attempt < 100; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 100));
      status = await evaluate("document.getElementById('s1-3-result')?.textContent ?? ''");
      if (status.startsWith("PASS") || status.startsWith("FAIL")) break;
    }
    if (!status.startsWith("PASS")) {
      const details = await evaluate("[...document.querySelectorAll('.m1-spot-row')].map((row) => row.textContent).join(' | ') + ' NOTICE=' + (document.querySelector('.m1-spot-notice')?.textContent ?? '')");
      throw new Error(`${width}px: ${status} ${details}`);
    }
    const layout = await evaluate(`(() => {
      document.querySelector('.m1-spot-form')?.scrollIntoView();
      const form = document.querySelector('.m1-spot-form').getBoundingClientRect();
      const root = document.documentElement;
      return { viewport: innerWidth, documentWidth: root.scrollWidth,
        formLeft: form.left, formRight: form.right, formWidth: form.width };
    })()`);
    if (layout.viewport !== width || layout.documentWidth > width + 1 ||
        layout.formLeft < -1 || layout.formRight > width + 1) {
      throw new Error(`${width}px 布局溢出: ${JSON.stringify(layout)}`);
    }
    await mkdir("data/s1-3-screenshots", { recursive: true });
    const screenshot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
    await writeFile(`data/s1-3-screenshots/m1-${width}-cdp.png`, Buffer.from(screenshot.data, "base64"));
    console.log(`${width}px: PASS ${JSON.stringify(layout)}`);
  }
} finally {
  socket.close();
}
