/** 手动一次性高德联通检查；只输出状态，不输出含公开 Key 的页面源码。 */

import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";

if (process.env.LIGHTTRAIL_ALLOW_AMAP_LIVE !== "1") {
  throw new Error("真实高德调用需设置 LIGHTTRAIL_ALLOW_AMAP_LIVE=1 并确认费用条件");
}

const browser = [
  process.env.CHROME_PATH,
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
].find((candidate) => candidate && existsSync(candidate));
if (!browser) throw new Error("未找到本机 Chrome/Edge");

const base = process.env.LIGHTTRAIL_UI_BASE ?? "http://127.0.0.1:5174";
const response = spawnSync(browser, [
  "--headless=new", "--disable-gpu", "--no-sandbox", "--virtual-time-budget=10000",
  "--dump-dom", `${base}/tests/amap-connectivity.html?run=1`,
], { encoding: "utf-8", maxBuffer: 8 * 1024 * 1024, timeout: 30000 });
if (response.error) throw response.error;
const result = response.stdout.match(/<p id="amap-result">([^<]*)<\/p>/)?.[1] ?? "FAIL: NO_RESULT";
console.log(result);
if (response.status !== 0 || !result.startsWith("PASS:")) process.exit(1);
