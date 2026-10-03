/** 运行 R0-6 离线浏览器交互检查；需先启动 Vite 开发服务器。 */

import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";

const candidates = [
  process.env.CHROME_PATH,
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].filter(Boolean);
const browser = candidates.find((candidate) => existsSync(candidate));
if (!browser) throw new Error("未找到本机 Chrome/Edge");
const base = process.env.LIGHTTRAIL_UI_BASE ?? "http://127.0.0.1:5173";
const response = spawnSync(browser, [
  "--headless=new", "--disable-gpu", "--no-sandbox", "--virtual-time-budget=10000",
  "--dump-dom", `${base}/tests/r0-6.html`,
], { encoding: "utf-8", maxBuffer: 8 * 1024 * 1024, timeout: 30000 });
if (response.error) throw response.error;
const match = response.stdout.match(/<p id="r0-6-result">([^<]*)<\/p>/);
const result = match?.[1] ?? "未找到浏览器检查结果";
console.log(result);
if (response.status !== 0 || !result.startsWith("PASS:")) process.exit(1);
