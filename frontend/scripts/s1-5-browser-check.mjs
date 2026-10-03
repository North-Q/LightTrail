/** 在本机浏览器验证 D2 URL、脱敏导出和离线回放交互。 */

import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";

const browser = [
  process.env.CHROME_PATH,
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
].find((candidate) => candidate && existsSync(candidate));
if (!browser) throw new Error("未找到本机 Chrome/Edge");
const base = process.env.LIGHTTRAIL_UI_BASE ?? "http://127.0.0.1:5173";
for (const [page, resultId] of [["s1-5", "s1-5-result"], ["s1-gate", "s1-gate-result"]]) {
  const response = spawnSync(browser, [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--virtual-time-budget=12000",
    "--dump-dom", `${base}/tests/${page}.html`,
  ], { encoding: "utf-8", maxBuffer: 8 * 1024 * 1024, timeout: 30000 });
  if (response.error) throw response.error;
  const result = response.stdout.match(new RegExp(`<p id="${resultId}">([^<]*)<\\/p>`))?.[1] ?? "FAIL: NO_RESULT";
  console.log(result);
  if (response.status !== 0 || !result.startsWith("PASS:")) process.exit(1);
}
